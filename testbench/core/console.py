"""Enlace de texto con la consola USB del firmware ``slaveTest``.

Es un protocolo distinto del que habla ``geophone_scope``: aquel son paquetes
binarios de 6 bytes contra el maestro a 921600; éste es texto ASCII contra el
ESP esclavo a 115200. Por eso no se reutiliza ``serial_worker``.

Tres cosas que este módulo resuelve y que costaron una sesión de banco
averiguar:

1. **Abrir el puerto resetea el ESP.** pyserial afirma DTR/RTS un instante al
   abrir, y el ESP32 arranca de nuevo. Cualquier comando escrito enseguida se
   pierde. Por eso ``open()`` espera por defecto a ver el ``[ST] Autotest
   listo`` del final del ``setup()`` antes de dar el puerto por usable.
2. **El firmware no ejecuta nada solo al arrancar.** Hay que mandarle el
   comando. No sirve engancharse a una corrida que empieza sola.
3. **Todo lo que sale y entra se registra con dirección y hora.** Es el pedido
   explícito del banco: poder mirar qué se le manda al ESP y qué contesta,
   incluido el eco de los eventos que el PSoC manda por I2C cuando está
   ``diag on``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

import serial
from serial.tools import list_ports

#: Velocidad de la consola USB del esclavo. No es la UART hacia el PSoC.
BAUD = 115200

#: Última línea que imprime ``setup()``; marca que la consola acepta comandos.
READY_MARK = "Autotest listo"

#: Fabricantes conocidos, para no tener que acordarse de qué COM es cuál.
ESP_HINTS = ("CP210", "Silicon Labs", "CH340", "USB-SERIAL")
PSOC_HINTS = ("KitProg",)

TX = "TX"      # PC -> ESP
RX = "RX"      # ESP -> PC
PSOC = "PSoC"  # eco de un evento que el PSoC mandó al ESP
INFO = "--"    # notas del propio banco


@dataclass(frozen=True)
class Entry:
    """Una línea del transcript, con hora relativa y dirección."""

    t: float
    direction: str
    text: str

    def format(self) -> str:
        return f"{self.t:8.3f}  {self.direction:<4} {self.text}"


class Transcript:
    """Historial completo de la sesión, acotado para no crecer sin límite."""

    def __init__(self, limit: int = 20_000) -> None:
        self._entries: list[Entry] = []
        self._limit = limit
        self._t0 = time.monotonic()

    def add(self, direction: str, text: str) -> Entry:
        e = Entry(time.monotonic() - self._t0, direction, text)
        self._entries.append(e)
        if len(self._entries) > self._limit:
            del self._entries[: len(self._entries) - self._limit]
        return e

    def __iter__(self) -> Iterator[Entry]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def tail(self, n: int) -> list[Entry]:
        return self._entries[-n:]

    def text(self, directions: Optional[tuple[str, ...]] = None) -> str:
        sel = [e for e in self._entries if directions is None or e.direction in directions]
        return "\n".join(e.format() for e in sel)

    def rx_text(self) -> str:
        """Sólo lo que llegó del ESP, sin timestamps: lo que espera el parser."""
        return "\n".join(e.text for e in self._entries if e.direction in (RX, PSOC))


def find_ports() -> dict[str, list[str]]:
    """Clasifica los COM presentes en ``esp``, ``psoc`` y ``otros``."""
    out: dict[str, list[str]] = {"esp": [], "psoc": [], "otros": []}
    for p in list_ports.comports():
        etiqueta = f"{p.description} {p.manufacturer or ''} {p.product or ''}"
        if any(h.lower() in etiqueta.lower() for h in PSOC_HINTS):
            out["psoc"].append(p.device)
        elif any(h.lower() in etiqueta.lower() for h in ESP_HINTS):
            out["esp"].append(p.device)
        else:
            out["otros"].append(p.device)
    return out


def default_port() -> Optional[str]:
    """El COM del ESP si hay exactamente uno; si no, None."""
    esp = find_ports()["esp"]
    return esp[0] if len(esp) == 1 else None


class Console:
    """Conexión de línea con el esclavo. Sincrónica; el hilo lo pone quien la use."""

    def __init__(
        self,
        port: str,
        baud: int = BAUD,
        on_entry: Optional[Callable[[Entry], None]] = None,
    ) -> None:
        self.port = port
        self.baud = baud
        self.transcript = Transcript()
        self._on_entry = on_entry
        self._ser: Optional[serial.Serial] = None
        self._buf = ""

    # -- ciclo de vida ----------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def open(self, wait_ready: bool = True, timeout: float = 20.0) -> bool:
        """Abre el puerto. Devuelve True si además vio la consola lista.

        Abrir resetea el ESP, así que con ``wait_ready`` se espera el banner de
        ``setup()``. Sin él, los primeros comandos se pierden en el arranque.
        """
        self._ser = serial.Serial(self.port, self.baud, timeout=0)
        self._ser.dtr = False
        self._ser.rts = False
        self._note(f"abierto {self.port} @ {self.baud} (DTR/RTS en False)")
        if not wait_ready:
            time.sleep(0.3)
            self._ser.reset_input_buffer()
            return True
        listo = self.wait_for(READY_MARK, timeout=timeout)
        if not listo:
            self._note(
                "no se vio el banner de arranque; el ESP puede estar corriendo "
                "otro firmware o ya venia arrancado"
            )
        return listo

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
            self._note("puerto cerrado")

    def reset_esp(self, wait_ready: bool = True, timeout: float = 20.0) -> bool:
        """Resetea el ESP por RTS (que va a EN) sin tocar el PSoC.

        No confundir con resetear el PSoC: eso es ``reset_psoc.ps1`` por
        KitProg, y este banco no lo hace por su cuenta.
        """
        if not self.is_open:
            return False
        self._note("reset del ESP por RTS")
        self._ser.dtr = False
        self._ser.rts = True
        time.sleep(0.15)
        self._ser.rts = False
        self._buf = ""
        if not wait_ready:
            return True
        return self.wait_for(READY_MARK, timeout=timeout)

    # -- salida -----------------------------------------------------------
    def send(self, command: str) -> None:
        """Manda un comando y lo deja registrado en el transcript."""
        if not self.is_open:
            raise RuntimeError("el puerto no esta abierto")
        self._emit(TX, command)
        self._ser.write((command + "\n").encode())
        self._ser.flush()

    # -- entrada ----------------------------------------------------------
    def poll(self) -> list[str]:
        """Lee lo que haya y devuelve las líneas completas nuevas."""
        if not self.is_open:
            return []
        salida: list[str] = []
        try:
            n = self._ser.in_waiting
            if n:
                self._buf += self._ser.read(n).decode("utf-8", "replace")
        except (OSError, serial.SerialException) as exc:
            self._note(f"error de lectura: {exc}")
            self.close()
            return []
        while "\n" in self._buf:
            linea, self._buf = self._buf.split("\n", 1)
            linea = linea.rstrip("\r")
            self._emit(PSOC if "[PSoC]" in linea else RX, linea)
            salida.append(linea)
        return salida

    def wait_for(
        self,
        needle: str,
        timeout: float = 30.0,
        on_line: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """Lee hasta ver ``needle`` en una línea o agotar el plazo."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            for linea in self.poll():
                if on_line is not None:
                    on_line(linea)
                if needle in linea:
                    return True
            time.sleep(0.02)
        return False

    def read_idle(
        self,
        idle: float = 2.0,
        timeout: float = 30.0,
        on_line: Optional[Callable[[str], None]] = None,
        until: Optional[Callable[[str], bool]] = None,
    ) -> list[str]:
        """Lee hasta que pasen ``idle`` segundos sin datos nuevos.

        ``until`` corta apenas una línea lo satisface, sin esperar el silencio.
        Importa mucho más de lo que parece: un comando como ``dc`` contesta una
        sola línea y ya está, pero sin esto se seguían esperando los ``idle``
        segundos igual. Con el valor que usaba el modo manual eso eran 1,1 s de
        regalo POR MEDIDA —medido, y sin depender del asentamiento pedido—, o
        sea que leer los cuatro taps tardaba cuatro segundos y medio en vez de
        dos décimas.

        Lo que quede en el buffer después del corte no se pierde ni ensucia el
        comando siguiente: ``Session.raw`` drena antes de mandar cada uno.
        """
        lineas: list[str] = []
        t0 = time.monotonic()
        ultimo = t0
        while time.monotonic() - t0 < timeout:
            nuevas = self.poll()
            if nuevas:
                ultimo = time.monotonic()
                listo = False
                for linea in nuevas:
                    if on_line is not None:
                        on_line(linea)
                    if until is not None and until(linea):
                        listo = True
                lineas.extend(nuevas)
                if listo:
                    break
            elif time.monotonic() - ultimo >= idle:
                break
            else:
                time.sleep(0.02)
        return lineas

    # -- internos ---------------------------------------------------------
    def _emit(self, direction: str, text: str) -> None:
        e = self.transcript.add(direction, text)
        if self._on_entry is not None:
            self._on_entry(e)

    def _note(self, text: str) -> None:
        self._emit(INFO, text)


__all__ = [
    "BAUD",
    "READY_MARK",
    "TX",
    "RX",
    "PSOC",
    "INFO",
    "Entry",
    "Transcript",
    "Console",
    "find_ports",
    "default_port",
]
