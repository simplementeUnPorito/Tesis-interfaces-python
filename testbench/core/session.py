"""Secuenciador del banco: ordena los comandos del ``slaveTest`` y evita sus trampas.

La consola del firmware acepta comandos sueltos, pero no todos los órdenes son
válidos y el firmware no siempre lo dice de forma legible. Este módulo encapsula
lo aprendido en el banco para que ni la terminal ni la interfaz gráfica puedan
pedir una secuencia que produzca un diagnóstico falso:

* **Toda captura exige SYNC armado.** ``stCapture()`` del firmware devuelve
  false de entrada si ``g_syncOk`` es falso, y ``g_syncOk`` sólo se pone en el
  grupo B. Correr ``c``, ``d`` o ``tap`` sin ``b`` antes, en el mismo arranque
  del ESP, da ``SKIP`` en C4/C5 y ``no se pudo capturar el fondo`` en D7. No es
  una falla de la placa: es el orden. Por eso ``arm_first`` viene en True.
* **Resetear el ESP desarma el SYNC.** Cada arranque necesita su propio ``b``.
* **D7 no se puede sincronizar desde la PC.** El firmware mide el fondo, cuenta
  hasta tres y captura 1,47 s. Como no hay forma de avisarle al operador el
  instante exacto desde acá, ``tap(repeat=N)`` dispara N intentos seguidos y se
  queda con los que salieron: golpeando a ritmo parejo, varios caen bien.

Nada de esto decide veredictos; eso es de ``checklist.evaluate``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .checklist import ChecklistParser, Item, evaluate
from .console import Console

#: Comando -> (descripción, plazo en segundos). Los plazos salen de corridas
#: reales del 2026-09-02: el grupo D es el largo, unos dos minutos.
GROUPS: dict[str, tuple[str, float]] = {
    "a": ("Grupo A — ESP32 solo", 120.0),
    "b": ("Grupo B — enlace ESP32 <-> PSoC", 180.0),
    "c": ("Grupo C — infraestructura del PSoC", 240.0),
    "d": ("Grupo D — cadena analogica", 420.0),
    "run": ("Corrida completa (A+B+C+D)", 900.0),
}

#: Comandos que necesitan a una persona en el banco.
INTERACTIVE = {
    "botones": ("Los cuatro pulsadores del ESP, de a uno", 90.0),
    "boton": ("Pulsador del PSoC", 30.0),
    "tap": ("Golpe al geofono (D7)", 40.0),
    "e": ("Fase interactiva completa", 180.0),
}


@dataclass
class RunResult:
    """Resultado de una corrida: el parser, el veredicto y cuánto tardó."""

    command: str
    parser: ChecklistParser
    verdict: dict
    seconds: float
    completed: bool
    lines: list[str] = field(default_factory=list)

    @property
    def items(self) -> list[Item]:
        return self.parser.items

    def summary(self) -> str:
        c = self.verdict["counts"]
        return (
            f"{c['PASS']} PASS  {c['FAIL']} FAIL  {c['WARN']} WARN  "
            f"{c['SKIP']} SKIP  {c['INFO']} INFO  ->  {self.verdict['board_verdict']}"
        )


class Session:
    """Maneja una consola abierta y sabe en qué estado quedó la placa."""

    def __init__(
        self,
        console: Console,
        on_line: Optional[Callable[[str], None]] = None,
        on_item: Optional[Callable[[Item], None]] = None,
        on_phase: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.console = console
        self.parser = ChecklistParser()
        self._on_line = on_line
        self._on_item = on_item
        self._on_phase = on_phase
        #: True si el grupo B corrió desde el último arranque del ESP.
        self.sync_armed = False

    # -- utilidades -------------------------------------------------------
    def _phase(self, texto: str) -> None:
        if self._on_phase is not None:
            self._on_phase(texto)

    def _consume(self, line: str) -> None:
        antes = len(self.parser.items)
        self.parser.feed(line)
        if self._on_line is not None:
            self._on_line(line)
        if self._on_item is not None and len(self.parser.items) > antes:
            self._on_item(self.parser.items[-1])

    def _drain(self, seconds: float = 0.6) -> None:
        """Vacía lo que quedó colgando antes de empezar algo nuevo."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            for linea in self.console.poll():
                self._consume(linea)
            time.sleep(0.02)

    # -- consultas rápidas ------------------------------------------------
    def probe(self, timeout: float = 6.0):
        """Pregunta el estado del enlace con el PSoC."""
        self._drain(0.2)
        self.console.send("probe")
        self.console.wait_for("probe=", timeout=timeout, on_line=self._consume)
        self._drain(0.3)
        return self.parser.link

    def hw(self, timeout: float = 6.0) -> dict[str, int]:
        """Lee el perfil de hardware presente guardado en NVS."""
        self._drain(0.2)
        self.parser.hw = {}
        self.console.send("hw")
        self.console.wait_for("Cambiar con", timeout=timeout, on_line=self._consume)
        self._drain(0.3)
        return dict(self.parser.hw)

    def set_hw(self, parte: str, valor: int, timeout: float = 6.0) -> dict[str, int]:
        """Cambia una parte del perfil (0 ausente, 1 presente, 2 auto)."""
        if parte not in ("oled", "btn", "geo", "sd", "psoc"):
            raise ValueError(f"parte desconocida: {parte}")
        if valor not in (0, 1, 2):
            raise ValueError("el valor tiene que ser 0, 1 o 2")
        self.console.send(f"hw {parte} {valor}")
        self._drain(1.0)
        return self.hw(timeout=timeout)

    def diag(self, encendido: bool) -> None:
        """Enciende o apaga el eco por USB de los eventos que manda el PSoC."""
        self.console.send("diag on" if encendido else "diag off")
        self._drain(0.5)

    def raw(self, comando: str, idle: float = 2.0, timeout: float = 30.0) -> list[str]:
        """Manda cualquier comando y devuelve lo que conteste. Escotilla de escape."""
        self._drain(0.2)
        self.console.send(comando)
        return self.console.read_idle(idle=idle, timeout=timeout, on_line=self._consume)

    # -- corridas ---------------------------------------------------------
    def arm_sync(self, force: bool = False) -> RunResult:
        """Corre el grupo B, que es lo único que arma el SYNC para las capturas."""
        if self.sync_armed and not force:
            raise RuntimeError("el SYNC ya esta armado; usar force=True para repetir")
        return self._run_command("b", GROUPS["b"][1], reset_parser=True)

    def run_group(self, letra: str, arm_first: bool = True) -> RunResult:
        """Corre un grupo. Si hace falta capturar, arma el SYNC antes."""
        letra = letra.lower()
        if letra not in GROUPS:
            raise ValueError(f"grupo desconocido: {letra}")
        # C y D capturan; sin B antes dan SKIP o FAIL por una razon que no es
        # la placa. `run` incluye su propio B, asi que no necesita ayuda.
        if arm_first and letra in ("c", "d") and not self.sync_armed:
            self._phase("armando el SYNC con el grupo B (hace falta para capturar)")
            self.arm_sync()
        return self._run_command(letra, GROUPS[letra][1], reset_parser=True)

    def run_full(self) -> RunResult:
        """Corrida completa. Incluye su propio grupo B."""
        return self._run_command("run", GROUPS["run"][1], reset_parser=True)

    def buttons_esp(self) -> RunResult:
        return self._run_command("botones", INTERACTIVE["botones"][1], reset_parser=True)

    def button_psoc(self) -> RunResult:
        return self._run_command("boton", INTERACTIVE["boton"][1], reset_parser=True)

    def tap(self, repeat: int = 1, arm_first: bool = True) -> list[dict]:
        """Dispara D7 ``repeat`` veces seguidas y devuelve un registro por intento.

        No se puede avisar el instante del golpe desde la PC, así que la
        estrategia es repetir: el operador golpea a ritmo parejo y varias
        capturas caen con el fondo quieto y el golpe adentro.
        """
        if arm_first and not self.sync_armed:
            self._phase("armando el SYNC con el grupo B (D7 no captura sin el)")
            self.arm_sync()

        resultados: list[dict] = []
        for i in range(1, repeat + 1):
            self._phase(f"D7 intento {i} de {repeat}: golpea al lado del geofono")
            antes = len(self.parser.meas.d7)
            self._drain(0.3)
            self.console.send("tap")
            visto = self.console.wait_for(
                "[D7]", timeout=INTERACTIVE["tap"][1], on_line=self._consume
            )
            self._drain(0.4)
            if visto and len(self.parser.meas.d7) > antes:
                resultados.append(self.parser.meas.d7[-1])
            else:
                resultados.append(
                    {
                        "intento": i,
                        "verdict": "FAIL",
                        "pico": None,
                        "fondo_pp": None,
                        "polaridad": None,
                        "detail": "sin respuesta del firmware dentro del plazo",
                    }
                )
        return resultados

    # -- motor ------------------------------------------------------------
    def _run_command(self, comando: str, timeout: float, reset_parser: bool) -> RunResult:
        """Manda un comando de corrida y lee hasta el ``#JSON`` que la cierra."""
        self._drain(0.5)
        if reset_parser:
            self.parser.reset_run()
        etiqueta = GROUPS.get(comando, INTERACTIVE.get(comando, (comando,)))[0]
        self._phase(f"corriendo: {etiqueta}")

        t0 = time.monotonic()
        primera = len(self.parser.lines)
        self.console.send(comando)

        completado = False
        while time.monotonic() - t0 < timeout:
            nuevas = self.console.poll()
            if nuevas:
                for linea in nuevas:
                    self._consume(linea)
                if self.parser.run_finished:
                    completado = True
                    break
            else:
                time.sleep(0.02)

        # El firmware imprime el cierre `=====` después del #JSON; darle lugar
        # para que no quede a medio leer y contamine la corrida siguiente.
        self._drain(1.0)
        segundos = time.monotonic() - t0

        # El grupo B es el único que arma el SYNC, y `run` lo incluye.
        if comando in ("b", "run") and completado:
            b3 = next((i for i in self.parser.items if i.code == "B3"), None)
            self.sync_armed = b3 is not None and b3.verdict == "PASS"

        return RunResult(
            command=comando,
            parser=self.parser,
            verdict=evaluate(self.parser),
            seconds=segundos,
            completed=completado,
            lines=self.parser.lines[primera:],
        )

    # -- estado -----------------------------------------------------------
    def note_esp_reset(self) -> None:
        """Avisar cuando se resetea el ESP: el SYNC queda desarmado."""
        self.sync_armed = False


__all__ = ["Session", "RunResult", "GROUPS", "INTERACTIVE"]
