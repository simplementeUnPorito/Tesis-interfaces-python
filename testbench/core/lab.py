"""Modo manual: mover los IDAC, mirar los taps, barrer y monitorear en vivo.

El autotest es el modo automático: corre solo, da un veredicto y no hay que
pensarlo. Esto es lo otro: las mismas primitivas del PSoC, pero sueltas, para
hacer experimentos finos a mano.

Los comandos los expone el firmware ``slaveTest`` (``idac``, ``dc``, ``ac``,
``mon``, ``sweep``, ``pga``, ``pgaout``) y contestan líneas prefijadas con
``#``, en campos separados por espacios, para que no haya que adivinar nada al
parsearlas. Este módulo las traduce a estructuras de Python.

Dos cosas que conviene tener presentes al usar esto:

* **Ninguno de estos comandos necesita el SYNC armado.** ``0xA4`` y ``0xA7``
  miden, no capturan, así que andan apenas hay enlace con el PSoC. Es la
  diferencia con C4/C5/D7, que sí capturan y sí lo necesitan.
* **Lo que se informa en µV es desviación respecto de ``Vref``**, no tensión
  absoluta: la cadena entra al ADC por un amplificador referido a ``Vdda/2``.
  Y el código de IDAC vale 1875 µV en la referencia de esta placa, no los 3750
  que asume el firmware (ver ``checklist.LSB_UV_PLACA``).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .checklist import LSB_UV_PLACA, STAGE_NAMES, TAP_NAMES
from .session import Session

# Selectores de asentamiento del comando `dc`, copiados de ST_SETTLE_MS[] del
# firmware. Si allá cambian, acá también.
SETTLE_MS = (5, 30, 120, 500, 1200, 1200, 1200, 1200)
SETTLE_DEFAULT = 3          # 500 ms, el que usa D2
SETTLE_FAST = 0             # 5 ms, el que usa `mon`

#: Cantidad de muestras del comando `ac`, por selector.
AC_SAMPLES = (256, 512, 1024, 2048, 4096, 8192, 128, 64)

#: Códigos de ganancia de PGA/PGAout y su valor.
GAIN_CODES = (1, 2, 4, 8, 16, 24, 32, 48, 50)

RE_DC = re.compile(r"^#DC (\d+) (\d+) (-?\d+) (-?\d+) (\d)")
RE_AC = re.compile(r"^#AC (\d+) (\d+) (-?\d+) (-?\d+) (-?\d+) (-?\d+) (\d)")
RE_IDAC = re.compile(r"^#IDAC (\d+) (-?\d+) (\d)")
RE_MON = re.compile(r"^#MON (\d+) (\d+) (\d+) (-?\d+) (-?\d+) (\d)")
RE_MONEND = re.compile(r"^#MONEND (\d+)")
RE_SWEEP = re.compile(r"^#SWEEP (\d+) (-?\d+) (\d+) (-?\d+) (-?\d+) (\d)")
RE_SWEEPEND = re.compile(r"^#SWEEPEND (\d+) (-?\d+)")
RE_GAIN = re.compile(r"^#GAIN (pga|pgaout) (\d+)")


@dataclass
class DcPoint:
    ch: int
    settle_sel: int
    mean_uv: int
    pp_uv: int
    ok: bool

    @property
    def mean_mv(self) -> float:
        return self.mean_uv / 1000.0


@dataclass
class AcPoint:
    ch: int
    n_sel: int
    mean_uv: int
    rms_uv: int
    pp_uv: int
    hz50_uv: int
    ok: bool


@dataclass
class MonSample:
    idx: int
    t_ms: int
    ch: int
    mean_uv: int
    pp_uv: int
    ok: bool


@dataclass
class Sweep:
    """Barrido de una etapa de IDAC contra uno o más taps."""

    stage: int
    #: {canal: [(codigo, media_uv), ...]}
    points: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
    final_code: Optional[int] = None

    def add(self, ch: int, code: int, mean_uv: int) -> None:
        self.points.setdefault(ch, []).append((code, mean_uv))

    def slope_uv_per_code(self, ch: int) -> Optional[float]:
        """Pendiente por mínimos cuadrados, en µV por código.

        Se ajusta con todos los puntos y no con los dos extremos como hace D2:
        un barrido tiene decenas de puntos y usar sólo dos tira a la basura la
        información que dice si la etapa es lineal o si satura en una punta.
        """
        pts = self.points.get(ch, [])
        if len(pts) < 2:
            return None
        n = len(pts)
        sx = sum(p[0] for p in pts)
        sy = sum(p[1] for p in pts)
        sxx = sum(p[0] * p[0] for p in pts)
        sxy = sum(p[0] * p[1] for p in pts)
        den = n * sxx - sx * sx
        if den == 0:
            return None
        return (n * sxy - sx * sy) / den

    def gain_from_reference(self, ch: int) -> Optional[float]:
        """Ganancia desde la referencia hasta el tap, adimensional.

        Divide la pendiente medida por el escalón real de esta placa
        (1875 µV por código). Es el número con sentido físico: cuánto amplifica
        la etapa, sin la escala equivocada del firmware de por medio.
        """
        s = self.slope_uv_per_code(ch)
        return None if s is None else s / LSB_UV_PLACA


class Lab:
    """Modo manual sobre una ``Session`` ya conectada."""

    def __init__(self, session: Session) -> None:
        self.s = session

    # -- primitivas -------------------------------------------------------
    #
    # REGLA, y esto ya rompió una medición entera: cada respuesta se acepta sólo
    # si su IDENTIDAD coincide con la del pedido. El canal, la etapa y el código
    # viajan en la propia respuesta justamente para eso.
    #
    # Tomar la primera línea que "parezca" la respuesta no alcanza. El firmware
    # a veces contesta tarde —hay un transitorio conocido tras cambiar de
    # configuración— y esa respuesta atrasada llega mientras uno ya está
    # esperando la del comando siguiente. Sin comprobar la identidad se la toma
    # como buena, y como el canal se lee de la línea recibida, el valor termina
    # etiquetado con el canal viejo: dos taps aparecen con los valores
    # intercambiados. Medido en placa: ch2 y ch3 se cambiaron los valores en
    # mitad de un barrido, que es exactamente lo que parece un mux trabado.
    def set_idac(self, stage: int, code: int, timeout: float = 8.0) -> bool:
        if not 0 <= stage <= 3 or not -255 <= code <= 255:
            raise ValueError("etapa 0-3, codigo -255..255 (0 = Vref)")

        def es_mia(linea: str) -> bool:
            m = RE_IDAC.match(linea)
            return bool(m) and int(m.group(1)) == stage and int(m.group(2)) == code

        # Un reintento. El firmware avisa en su propio comentario que "el primer
        # comando despues de un cambio de configuracion se ignora en silencio",
        # y todos sus runners usan el mismo patron acotado. Sin esto, en un
        # barrido aparecian puntos sueltos como si el IDAC no hubiera aceptado
        # el codigo, y el que fallaba era el comando, no el IDAC.
        for intento in range(2):
            for linea in self.s.raw(f"idac {stage} {code}", idle=0.8,
                                    timeout=timeout, until=es_mia):
                if es_mia(linea):
                    return RE_IDAC.match(linea).group(3) == "1"
        return False

    def measure_dc(self, ch: int, settle_sel: int = SETTLE_DEFAULT) -> Optional[DcPoint]:
        if not 0 <= ch <= 4 or not 0 <= settle_sel <= 7:
            raise ValueError("canal 0-4, asentamiento 0-7")
        plazo = SETTLE_MS[settle_sel] / 1000.0 + 8.0

        def es_mia(linea: str) -> bool:
            m = RE_DC.match(linea)
            return bool(m) and int(m.group(1)) == ch

        for intento in range(2):        # mismo reintento acotado que set_idac
            for linea in self.s.raw(f"dc {ch} {settle_sel}", idle=0.8,
                                    timeout=plazo, until=es_mia):
                if es_mia(linea):
                    m = RE_DC.match(linea)
                    return DcPoint(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                   int(m.group(4)), m.group(5) == "1")
        return None

    def measure_ac(self, ch: int, n_sel: int = 0) -> Optional[AcPoint]:
        if not 0 <= ch <= 4 or not 0 <= n_sel <= 7:
            raise ValueError("canal 0-4, n 0-7")
        # 8192 muestras a 2604 Hz son más de tres segundos; el plazo lo cubre.
        plazo = AC_SAMPLES[n_sel] / 2604.0 + 15.0

        def es_mia(linea: str) -> bool:
            m = RE_AC.match(linea)
            return bool(m) and int(m.group(1)) == ch

        # idle 1,0 s era CORTO y perdia medidas en silencio: en la campana del
        # 2026-09-05 los canales 2 y 3 volvian None mientras el 0 y el 1 salian
        # bien, o sea que el error dependia del orden y no del canal. `raw`
        # devuelve cuando pasa `idle` sin recibir nada, aunque `until` no se haya
        # cumplido todavia, asi que una respuesta que tarda mas que `idle` se
        # pierde y el llamador recibe None como si el PSoC no hubiera contestado.
        # Se pone en 6 s, holgado contra el peor caso real. El `until` sigue
        # cortando apenas llega la linea, asi que no cuesta tiempo cuando anda.
        for linea in self.s.raw(f"ac {ch} {n_sel}", idle=6.0, timeout=plazo,
                                until=es_mia):
            if es_mia(linea):
                m = RE_AC.match(linea)
                return AcPoint(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                               int(m.group(4)), int(m.group(5)), int(m.group(6)),
                               m.group(7) == "1")
        return None

    def set_gain(self, which: str, code: int) -> bool:
        if which not in ("pga", "pgaout"):
            raise ValueError("which tiene que ser 'pga' o 'pgaout'")
        if not 0 <= code <= 8:
            raise ValueError("codigo de ganancia 0-8")
        def es_mia(linea: str) -> bool:
            m = RE_GAIN.match(linea)
            return bool(m) and m.group(1) == which and int(m.group(2)) == code

        for linea in self.s.raw(f"{which} {code}", idle=0.8, timeout=8.0,
                                until=es_mia):
            if es_mia(linea):
                return True
        return False

    def read_all_taps(self, settle_sel: int = SETTLE_DEFAULT) -> dict[int, DcPoint]:
        """Los cuatro taps de una, para ver el estado completo de la cadena."""
        out: dict[int, DcPoint] = {}
        for ch in range(4):
            p = self.measure_dc(ch, settle_sel)
            if p is not None:
                out[ch] = p
        return out

    # -- monitor en vivo --------------------------------------------------
    def monitor(
        self,
        ch: int,
        period_ms: int = 200,
        n: int = 200,
        on_sample: Optional[Callable[[MonSample], None]] = None,
        stop: Optional[Callable[[], bool]] = None,
    ) -> list[MonSample]:
        """Osciloscopio lento sobre un tap.

        No es una captura a 2604 Hz: es una medida DC por vuelta, unas siete por
        segundo. Sirve para ver el punto de trabajo moverse mientras se toca un
        IDAC o una ganancia, que es lo que uno quiere mirando una cadena
        analógica sin calibrar.

        ``stop`` se consulta entre muestras; devolver True corta y le manda una
        tecla al firmware, que también corta su lazo.
        """
        if not 0 <= ch <= 4:
            raise ValueError("canal 0-4")
        muestras: list[MonSample] = []
        self.s.console.send(f"mon {ch} {period_ms} {n}")

        t0 = time.monotonic()
        # Plazo con margen: el firmware tarda ~137 ms por muestra con período 100.
        plazo = (period_ms + 200) / 1000.0 * max(n, 1) + 20.0
        terminado = False
        while not terminado and (time.monotonic() - t0) < plazo:
            if stop is not None and stop():
                self.s.console.send("")  # cualquier byte corta el lazo del firmware
                terminado = True
            for linea in self.s.console.poll():
                self.s.parser.feed(linea)
                m = RE_MON.match(linea)
                # Misma regla que las primitivas: una muestra de OTRO canal es
                # una respuesta atrasada de la corrida anterior. Aceptarla
                # metia el valor de un tap en la traza de otro.
                if m and int(m.group(3)) == ch:
                    ms = MonSample(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                   int(m.group(4)), int(m.group(5)), m.group(6) == "1")
                    muestras.append(ms)
                    if on_sample is not None:
                        on_sample(ms)
                elif RE_MONEND.match(linea):
                    terminado = True
            if not terminado:
                time.sleep(0.01)
        self.s._drain(0.5)  # noqa: SLF001 - vaciar el #MONEND que quedo colgando
        return muestras

    # -- barrido ----------------------------------------------------------
    def sweep(
        self,
        stage: int,
        lo: int = 0,
        hi: int = 255,
        step: int = 16,
        ch: int = -1,
        on_point: Optional[Callable[[int, int, int], None]] = None,
    ) -> Sweep:
        """Barre el IDAC de una etapa y mide uno o todos los taps.

        Es la matriz D2 pero con la curva entera en vez de dos puntos: con eso
        se ve si la etapa es lineal, dónde satura y cuál es su ganancia real.
        Cada punto tarda 500 ms por canal, así que un barrido de 16 puntos por
        los cuatro taps son unos 35 segundos.
        """
        if not 0 <= stage <= 3:
            raise ValueError("etapa 0-3")
        if step <= 0 or not -255 <= lo <= hi <= 255:
            raise ValueError("rango invalido")

        sw = Sweep(stage=stage)
        puntos = len(range(lo, hi + 1, step))
        canales = 4 if ch < 0 else 1
        plazo = puntos * canales * 4.0 + 30.0

        self.s.console.send(f"sweep {stage} {lo} {hi} {step} {ch}")
        t0 = time.monotonic()
        terminado = False
        while not terminado and (time.monotonic() - t0) < plazo:
            for linea in self.s.console.poll():
                self.s.parser.feed(linea)
                m = RE_SWEEP.match(linea)
                # La etapa tiene que ser la que se pidió: un punto de un barrido
                # anterior que llegue tarde ensuciaría esta curva con datos de
                # otra etapa, y el ajuste de pendiente saldría de una mezcla.
                if m and m.group(6) == "1" and int(m.group(1)) == stage:
                    etapa, code, canal, media = (int(m.group(1)), int(m.group(2)),
                                                 int(m.group(3)), int(m.group(4)))
                    sw.add(canal, code, media)
                    if on_point is not None:
                        on_point(canal, code, media)
                    continue
                m = RE_SWEEPEND.match(linea)
                if m:
                    sw.final_code = int(m.group(2))
                    terminado = True
            if not terminado:
                time.sleep(0.01)
        self.s._drain(0.5)  # noqa: SLF001
        return sw


def describe_stage(stage: int) -> str:
    return STAGE_NAMES[stage] if 0 <= stage < len(STAGE_NAMES) else f"etapa {stage}"


def describe_tap(ch: int) -> str:
    return TAP_NAMES[ch] if 0 <= ch < len(TAP_NAMES) else f"ch{ch}"


__all__ = [
    "Lab",
    "DcPoint",
    "AcPoint",
    "MonSample",
    "Sweep",
    "SETTLE_MS",
    "SETTLE_DEFAULT",
    "SETTLE_FAST",
    "AC_SAMPLES",
    "GAIN_CODES",
    "describe_stage",
    "describe_tap",
]
