"""Parseo y veredicto del checklist que emite el firmware ``slaveTest``.

Por qué existe habiendo ya un ``autotest_runner.py`` en el repo del firmware:
ese runner lee una corrida entera y recién después parsea. El banco necesita
llenar una tabla **mientras** la corrida avanza, así que el parseo es
incremental, línea por línea. Los invariantes del veredicto sí son los mismos
y están copiados a propósito, no reinterpretados: si el firmware dice APTO
teniendo un FAIL, o el ``#JSON`` no coincide con el checklist, o falta un ítem
obligatorio, la corrida no vale. Una corrida que muere al tercer ítem imprime
tres PASS y ningún FAIL; sin esos invariantes eso parece un éxito.

Las expresiones regulares están alineadas con
``firmware/esp32/Nodo comunicación/slave/autotest_runner.py``. Si allá cambian,
acá también: ``self_test()`` compara contra las mismas capturas de referencia.

Además del checklist, este módulo extrae los números que sirven para graficar
—matriz D2, ruido D6, golpes D7, reposo D1— que el runner descarta porque sólo
le interesa el veredicto.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# --------------------------------------------------------------------------
# Gramática de la salida del firmware
# --------------------------------------------------------------------------
VERDICTS = ("PASS", "FAIL", "WARN", "SKIP", "INFO")

# `[A3] Pull-ups de botones .......... FAIL  detalle`
ITEM_RE = re.compile(
    r"^\[([A-Za-z0-9_.*]{1,8})\]\s+(.*?)[\s.]*\s(" + "|".join(VERDICTS) + r")(?:\s\s(.*))?$"
)
JSON_RE = re.compile(r"^#JSON\s+(\{.*\})\s*$")
SUMMARY_RE = re.compile(r"^RESUMEN\s+.*VEREDICTO:\s+(APTO|NO APTO)\s*$")
BANNER_RE = re.compile(r"^=+$")

# `[ST] probe=1 bOK=0 bBad=0 ping=3 diag=3 ovr=0`
PROBE_RE = re.compile(
    r"probe=(\d+)\s+bOK=(\d+)\s+bBad=(\d+)\s+ping=(\d+)\s+diag=(\d+)\s+ovr=(\d+)"
)
# `[ST]   oled = 0 (ausente)`
HW_RE = re.compile(r"^\[ST\]\s+(oled|btn|geo|sd|psoc)\s*=\s*([012])\s*\((\w+)\)")

# Ítems que TIENEN que haber corrido para que el veredicto valga.
REQUIRED_ALWAYS = ("A1", "A2", "A3", "A4", "B1", "B4")
# Estos dependen del PSoC; si no contesta, el firmware emite `C*`/`D*` y la
# corrida es PARCIAL A PROPÓSITO, no truncada.
REQUIRED_WITH_PSOC = ("B2", "B3", "C1", "C4", "C6", "D1", "D2", "D8")
SKIP_MARKERS = ("C*", "D*")

# --------------------------------------------------------------------------
# Calibración de esta placa (ver registro_pruebas_analogicas_2026-09-02.md)
# --------------------------------------------------------------------------
# El firmware deriva su escala de la portadora JitX, que no se fabricó: R de
# conversión 30 kΩ y Vref de un AMS1117 que en la placa no existe. La placa
# construida tenía R11-R14 = 15 kΩ contra Vref. La topología en prueba del
# 2026-09-08 lleva IDAC2 a la referencia de OPA_SUM (PGAout queda referenciado
# a 2,5 V fijos) y usa 10 kΩ tanto allí como en LP. Los cuatro IDAC usan el
# rango fino normal de 31,875 µA.
IDAC_LSB_NA = 125.0            # compatibilidad: etapas 0, 1 y 3
IDAC_LSB_NA_BY_STAGE = (125.0, 125.0, 125.0, 125.0)
IDAC_RSET_OHM_PLACA = 15_000.0  # medido: R11 = 14,76 kΩ sobre seis lecturas
IDAC_RSET_OHM_BY_STAGE = (15_000.0, 15_000.0, 10_000.0, 10_000.0)
IDAC_RSET_OHM_FIRMWARE = 30_000.0
#: µV por código de IDAC en la placa construida: 125 nA x 15 kΩ = 1875 µV.
LSB_UV_PLACA = IDAC_LSB_NA * IDAC_RSET_OHM_PLACA / 1000.0
LSB_UV_BY_STAGE = tuple(i * r / 1000.0 for i, r in
                        zip(IDAC_LSB_NA_BY_STAGE, IDAC_RSET_OHM_BY_STAGE))
#: El umbral del firmware (200 µV/código) está justificado con 3,75 mV/LSB.
#: Escalado a esta placa da la mitad.
D2_MIN_SLOPE_UV_FIRMWARE = 200.0
D2_MIN_SLOPE_UV_PLACA = D2_MIN_SLOPE_UV_FIRMWARE * (
    IDAC_RSET_OHM_PLACA / IDAC_RSET_OHM_FIRMWARE
)

#: Qué mide cada canal del AMux, en orden.
TAP_NAMES = ("PGAgain", "BPo", "OPA_SUMo", "SUMo", "LPo", "AMuxCap")
#: Canales de señal. El último elemento de TAP_NAMES es el capacitor auxiliar.
SIGNAL_TAP_CHANNELS = tuple(range(len(TAP_NAMES) - 1))
#: Qué referencia mueve cada etapa de IDAC, en orden.
STAGE_NAMES = ("Vref_PGA", "Vref_BP", "Vref_OPAsum", "Vref_LP")
#: Tap propio de cada etapa. LP salta ch3 porque ese canal ahora es SUMo.
STAGE_TAP_CHANNELS = (0, 1, 2, 4)


def fmt_mv(uv: float, decimales: int = 3) -> str:
    """Un valor del ADC en mV, CON SIGNO explícito.

    El ADC mide en diferencial contra ``Vref``, así que el signo es parte del
    dato: un valor puede quedar por debajo de la referencia y eso no es un
    error. Sin el ``+`` delante, un número positivo y uno sin signo se leen
    igual y se pierde que la magnitud es una desviación respecto de ``Vref`` y
    no una tensión absoluta contra masa.

    Vive acá y no en ``figures`` porque ``figures`` arrastra matplotlib, y la
    terminal formatea números en comandos que no dibujan nada.
    """
    return f"{uv / 1000.0:+,.{decimales}f} mV".replace(",", " ")


# --------------------------------------------------------------------------
# Estructuras
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Item:
    """Una línea del checklist."""

    code: str
    name: str
    verdict: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "verdict": self.verdict,
            "detail": self.detail,
        }


@dataclass
class LinkState:
    """Lo último que dijo el comando ``probe``."""

    up: bool = False
    frames_ok: int = 0
    frames_bad: int = 0
    pings: int = 0
    diags: int = 0
    overruns: int = 0
    seen: bool = False


@dataclass
class Measurements:
    """Números útiles para graficar, extraídos de los detalles del checklist.

    Nada de esto participa del veredicto: es lo que el runner tira y el banco
    quiere ver.
    """

    #: matriz[etapa][tap] en µV por código de IDAC, None donde no hubo dato
    d2: list[list[Optional[float]]] = field(
        default_factory=lambda: [[None] * len(SIGNAL_TAP_CHANNELS)
                                 for _ in STAGE_NAMES]
    )
    #: reposo DC por tap, en mV (ítem D1)
    d1_mv: dict[int, float] = field(default_factory=dict)
    #: ruido por tap: {tap: {"media_uv","rms_uv","pp_uv","hz50_uv"}} (ítems D6.x)
    d6: dict[int, dict[str, float]] = field(default_factory=dict)
    #: un registro por intento de D7
    d7: list[dict] = field(default_factory=list)

    def d2_diagonal(self) -> list[Optional[float]]:
        # Si se abre un informe viejo de cuatro taps, la etapa LP estaba en
        # ch3. Los informes nuevos usan ch4. No se reetiquetan las otras
        # columnas: esta única caída conserva el veredicto histórico.
        out: list[Optional[float]] = []
        for stage, tap in enumerate(STAGE_TAP_CHANNELS):
            slope = self.d2[stage][tap]
            if stage == 3 and slope is None:
                slope = self.d2[stage][3]
            out.append(slope)
        return out

    def d2_veredicto_placa(self) -> list[tuple[str, Optional[float], bool]]:
        """Diagonal contra el umbral corregido a esta placa.

        Devuelve (nombre de etapa, pendiente, pasa) por etapa. Se usa el umbral
        de 100 µV/código, no los 200 del firmware: ver la nota de arriba.
        """
        out: list[tuple[str, Optional[float], bool]] = []
        for i, slope in enumerate(self.d2_diagonal()):
            ok = slope is not None and abs(slope) >= D2_MIN_SLOPE_UV_PLACA
            out.append((STAGE_NAMES[i], slope, ok))
        return out


# --------------------------------------------------------------------------
# Parser incremental
# --------------------------------------------------------------------------
class ChecklistParser:
    """Consume líneas sueltas y va armando el estado de la corrida.

    Se usa igual desde el modo terminal y desde el gráfico: ``feed()`` devuelve
    qué cambió con esa línea, para que la interfaz refresque sólo eso.
    """

    def __init__(self) -> None:
        self.items: list[Item] = []
        self.json: Optional[dict] = None
        self.summary_verdict: Optional[str] = None
        self.link = LinkState()
        self.hw: dict[str, int] = {}
        self.meas = Measurements()
        self.lines: list[str] = []
        self._json_seen = False

    # -- ciclo de vida ----------------------------------------------------
    def reset_run(self) -> None:
        """Empieza una corrida nueva conservando enlace y perfil de hardware."""
        self.items = []
        self.json = None
        self.summary_verdict = None
        self.meas = Measurements()
        self.lines = []
        self._json_seen = False

    @property
    def run_finished(self) -> bool:
        """True cuando ya llegó el ``#JSON`` que cierra la corrida."""
        return self._json_seen

    # -- entrada ----------------------------------------------------------
    def feed(self, line: str) -> str:
        """Procesa una línea. Devuelve qué tipo de cosa era.

        Valores: ``item``, ``json``, ``summary``, ``probe``, ``hw``, ``banner``
        o ``texto``.
        """
        line = line.rstrip("\r\n")
        self.lines.append(line)

        m = JSON_RE.match(line)
        if m:
            try:
                self.json = json.loads(m.group(1))
            except json.JSONDecodeError:
                self.json = None
            self._json_seen = True
            return "json"

        m = SUMMARY_RE.match(line)
        if m:
            self.summary_verdict = m.group(1)
            return "summary"

        m = ITEM_RE.match(line)
        if m:
            item = Item(m.group(1), m.group(2).strip(), m.group(3), (m.group(4) or "").strip())
            self.items.append(item)
            self._extract(item)
            return "item"

        m = PROBE_RE.search(line)
        if m:
            self.link = LinkState(
                up=m.group(1) == "1",
                frames_ok=int(m.group(2)),
                frames_bad=int(m.group(3)),
                pings=int(m.group(4)),
                diags=int(m.group(5)),
                overruns=int(m.group(6)),
                seen=True,
            )
            return "probe"

        m = HW_RE.match(line)
        if m:
            self.hw[m.group(1)] = int(m.group(2))
            return "hw"

        if BANNER_RE.match(line.strip()):
            return "banner"
        return "texto"

    def feed_many(self, text: str) -> None:
        for line in text.splitlines():
            self.feed(line)

    # -- extracción de números -------------------------------------------
    def _extract(self, item: Item) -> None:
        code, detail = item.code, item.detail

        # `[D2.1] pendientes uV/codigo ... INFO  -0.9 190.2 -685.2 6542.7`
        if code.startswith("D2.") and code[3:].isdigit():
            stage = int(code[3:])
            nums = re.findall(r"-?\d+(?:\.\d+)?", detail)
            if 0 <= stage < len(STAGE_NAMES) and len(nums) >= 4:
                for tap in range(min(len(SIGNAL_TAP_CHANNELS), len(nums))):
                    self.meas.d2[stage][tap] = float(nums[tap])
            return

        # `[D1] ... PASS  ch0=1010mV ... ch3=950mV ch4=907mV`
        if code == "D1":
            for ch, mv in re.findall(r"ch(\d+)=(-?\d+)mV", detail):
                self.meas.d1_mv[int(ch)] = float(mv)
            return

        # `[D6.0] ... PASS  media 1010761 uV, RMS 38 uV, pp 228 uV, 50Hz 0 uV`
        if code.startswith("D6.") and code[3:].isdigit():
            tap = int(code[3:])
            campos = {
                "media_uv": r"media\s+(-?\d+)\s*uV",
                "rms_uv": r"RMS\s+(-?\d+)\s*uV",
                "pp_uv": r"pp\s+(-?\d+)\s*uV",
                "hz50_uv": r"50Hz\s+(-?\d+)\s*uV",
            }
            datos: dict[str, float] = {}
            for nombre, patron in campos.items():
                m = re.search(patron, detail)
                if m:
                    datos[nombre] = float(m.group(1))
            if datos:
                self.meas.d6[tap] = datos
            return

        # `[D7] ... PASS  pico 4246 counts (fondo pp 295), 1a excursion POSITIVA`
        # `[D7] ... FAIL  pico 93 counts no supera el fondo pp 259: sin definir`
        if code == "D7":
            pico = re.search(r"pico\s+(-?\d+)\s*counts", detail)
            fondo = re.search(r"fondo pp\s+(-?\d+)", detail)
            pol = re.search(r"(POSITIVA|NEGATIVA|sin definir)", detail)
            self.meas.d7.append(
                {
                    "intento": len(self.meas.d7) + 1,
                    "verdict": item.verdict,
                    "pico": int(pico.group(1)) if pico else None,
                    "fondo_pp": int(fondo.group(1)) if fondo else None,
                    "polaridad": pol.group(1) if pol else None,
                    "detail": detail,
                }
            )


# --------------------------------------------------------------------------
# Veredicto
# --------------------------------------------------------------------------
def counts_from_items(items: Iterable[Item]) -> dict[str, int]:
    counts = {v: 0 for v in VERDICTS}
    for it in items:
        if it.verdict in counts:
            counts[it.verdict] += 1
    return counts


def evaluate(parser: ChecklistParser) -> dict:
    """Aplica los invariantes y devuelve el veredicto del banco.

    Mismos criterios que ``autotest_runner.evaluate``. La diferencia es que acá
    la entrada es el parser incremental en vez de un texto completo.
    """
    problems: list[str] = []
    items = parser.items
    payload = parser.json

    if payload is None:
        problems.append("no llego la linea #JSON: corrida incompleta o puerto perdido")
    if parser.summary_verdict is None:
        problems.append("no llego la linea RESUMEN")
    if not items:
        problems.append("no se parseo ningun item del checklist")

    counts = counts_from_items(items)
    codes = {it.code for it in items}

    partial = any(m in codes for m in SKIP_MARKERS)

    requeridos = list(REQUIRED_ALWAYS)
    if not partial:
        requeridos += list(REQUIRED_WITH_PSOC)
    faltantes = [c for c in requeridos if c not in codes]
    if faltantes:
        problems.append("items obligatorios ausentes: " + ", ".join(faltantes))

    fails = sorted(it.code for it in items if it.verdict == "FAIL")
    warns = sorted(it.code for it in items if it.verdict == "WARN")

    # El #JSON del firmware y el checklist parseado tienen que coincidir. Si no,
    # se perdieron lineas y ningun conteo es confiable.
    if payload is not None:
        if payload.get("pass") != counts["PASS"]:
            problems.append(
                f"PASS del #JSON ({payload.get('pass')}) != items parseados ({counts['PASS']})"
            )
        if sorted(payload.get("fail", [])) != fails:
            problems.append(
                f"lista FAIL del #JSON {sorted(payload.get('fail', []))} != parseada {fails}"
            )
        if payload.get("skip") != counts["SKIP"]:
            problems.append(
                f"SKIP del #JSON ({payload.get('skip')}) != items parseados ({counts['SKIP']})"
            )
        esperado_json = "FAIL" if fails else "PASS"
        if payload.get("verdict") != esperado_json:
            problems.append(
                f"verdict del #JSON '{payload.get('verdict')}' no coincide con la lista FAIL"
            )

    if parser.summary_verdict is not None:
        esperado = "NO APTO" if fails else "APTO"
        if parser.summary_verdict != esperado:
            problems.append(
                f"RESUMEN dice '{parser.summary_verdict}' pero la lista FAIL dice '{esperado}'"
            )

    ok = (not problems) and (not fails)
    if partial:
        cobertura = "parcial_sin_psoc"
    elif problems:
        cobertura = "incompleta"
    else:
        cobertura = "completa"

    return {
        "ok": ok,
        "partial": partial,
        "coverage": cobertura,
        "board_verdict": (
            "NO APTO"
            if (fails or problems)
            else ("APTO (parcial: sin PSoC)" if partial else "APTO")
        ),
        "counts": counts,
        "fails": fails,
        "warns": warns,
        "missing_required": faltantes,
        "problems": problems,
        "item_count": len(items),
    }


# --------------------------------------------------------------------------
# Autoprueba offline
# --------------------------------------------------------------------------
GOOD_REPORT = """=========== AUTOTEST NODO ESCLAVO ===========
ESP  slaveTest Sep  1 2026 10:00:00   MAC 24:6F:28:11:22:33   NODE_ID=2
[A1] Arranque ESP32 ....................... PASS  POWERON, heap 218 KB
[A2] OLED SSD1306 por SPI ................. PASS  responde en SPI
[A3] Pull-ups de botones .................. PASS  4/4 en alto estable
[A4] ESP-NOW (init + peer + TX) ........... PASS  esp_now_send=0
[A5] Radio / maestro visible .............. INFO  GeoNetwork en canal 1
[A6] GPIO25 (ex PSOC_UART_RX) ............. SKIP  sin conectar por diseno
[B1] Subida I2C PSoC->ESP (0x42) .......... PASS  4200 B/1.5s
[B2] Bajada UART ESP->PSoC ................ PASS  STATUS respondido en 41 ms
[B3] Linea SYNC GPIO27 -> P0[4] ........... PASS  20 flancos en 10 ciclos
[C1] Identidad del PSoC ................... PASS  clase=GEO, Fs=2604 Hz
[C4] Camino digital E2E (rampa cruda) ..... PASS  8/8 lotes, 99% crecientes
[C6] SD FatFs (ruteo SPIp nuevo) .......... PASS  SDHC, FAT montado
[D1] Reposo de los taps analogicos ........ PASS  ch0=12mV ch1=-4mV
[D2] Matriz DC IDAC->etapa ................ PASS  4/4 barridos
[D8] Auto-calibracion ..................... PASS  IDAC 142/118/97/163
[B4] Integridad de trama .................. PASS  bBad=0 badLen=0

RESUMEN  14 PASS - 0 FAIL - 0 WARN - 1 SKIP - 1 INFO     VEREDICTO: APTO
#JSON {"verdict":"PASS","pass":14,"fail":[],"warn":[],"skip":1,"info":1}
=============================================
"""

BAD_REPORT = GOOD_REPORT.replace(
    "[A3] Pull-ups de botones .................. PASS  4/4 en alto estable",
    "[A3] Pull-ups de botones .................. FAIL  BTN_OK(36) flotante",
).replace(
    "RESUMEN  14 PASS - 0 FAIL - 0 WARN - 1 SKIP - 1 INFO     VEREDICTO: APTO",
    "RESUMEN  13 PASS - 1 FAIL - 0 WARN - 1 SKIP - 1 INFO     VEREDICTO: NO APTO",
).replace(
    '#JSON {"verdict":"PASS","pass":14,"fail":[],"warn":[],"skip":1,"info":1}',
    '#JSON {"verdict":"FAIL","pass":13,"fail":["A3"],"warn":[],"skip":1,"info":1}',
)

TRUNCATED_REPORT = "\n".join(GOOD_REPORT.splitlines()[:8]) + "\n"

# Corrida real del 2026-09-02: los detalles que alimentan los gráficos.
REAL_DETAILS = """[D1] Reposo de los taps analogicos ........ PASS  ch0=1010mV ch1=1015mV ch2=1030mV ch3=950mV ch4=907mV
[D2.0]   pendientes uV/codigo ............. INFO  61.0 -66.8 0.9 43.4 40.0
[D2.1]   pendientes uV/codigo ............. INFO  -0.5 190.7 -686.2 6546.5 6000.0
[D2.2]   pendientes uV/codigo ............. INFO  2.9 -5.7 767.2 -6718.2 -6000.0
[D2.3]   pendientes uV/codigo ............. INFO  0.5 -0.9 0.0 0.0 1269.8
[D6.0] Piso de ruido del tap .............. PASS  media 1010761 uV, RMS 38 uV, pp 228 uV, 50Hz 0 uV
[D7] Golpe al geofono (pico y polaridad) .. PASS  pico 4246 counts (fondo pp 295), 1a excursion POSITIVA
[D7] Golpe al geofono (pico y polaridad) .. FAIL  pico 93 counts no supera el fondo pp 259: sin definir
[ST] probe=1 bOK=0 bBad=0 ping=3 diag=3 ovr=0
[ST]   oled = 0 (ausente)
[ST]   geo  = 1 (presente)
"""


def self_test() -> int:
    """Pruebas sin hardware. Devuelve 0 si pasan todas."""
    checks: list[tuple[str, bool]] = []

    def check(nombre: str, cond: bool) -> None:
        checks.append((nombre, cond))
        print(f"[{'PASS' if cond else 'FAIL'}] {nombre}")

    p = ChecklistParser()
    p.feed_many(GOOD_REPORT)
    r = evaluate(p)
    check("placa sana: ok", r["ok"])
    check("placa sana: APTO", r["board_verdict"] == "APTO")
    check("placa sana: 16 items", r["item_count"] == 16)
    check("placa sana: sin problemas", r["problems"] == [])
    check("placa sana: cobertura completa", r["coverage"] == "completa")

    p = ChecklistParser()
    p.feed_many(BAD_REPORT)
    r = evaluate(p)
    check("placa con falla: no ok", not r["ok"])
    check("placa con falla: FAIL = [A3]", r["fails"] == ["A3"])
    check("placa con falla: sin falsos problemas", r["problems"] == [])

    p = ChecklistParser()
    p.feed_many(TRUNCATED_REPORT)
    r = evaluate(p)
    check("corrida trunca: no ok pese a no tener FAIL", not r["ok"] and not r["fails"])
    check("corrida trunca: detecta faltantes", bool(r["missing_required"]))

    p = ChecklistParser()
    p.feed_many(GOOD_REPORT.replace('"pass":14', '"pass":99'))
    r = evaluate(p)
    check(
        "desajuste #JSON vs checklist: detectado",
        any("PASS del #JSON" in x for x in r["problems"]),
    )

    p = ChecklistParser()
    p.feed_many(BAD_REPORT.replace("VEREDICTO: NO APTO", "VEREDICTO: APTO"))
    r = evaluate(p)
    check("RESUMEN mentiroso: detectado", any("RESUMEN dice" in x for x in r["problems"]))

    # Extracción de números para los gráficos.
    p = ChecklistParser()
    p.feed_many(REAL_DETAILS)
    m = p.meas
    check("D2: matriz 4x5 completa", all(v is not None for fila in m.d2 for v in fila))
    check("D2: diagonal correcta", m.d2_diagonal() == [61.0, 190.7, 767.2, 1269.8])
    check("D2: fila 1 con negativos", m.d2[1][2] == -686.2)
    check("D1: cinco taps", len(m.d1_mv) == 5 and m.d1_mv[4] == 907.0)
    check("D6: ruido del tap 0", m.d6[0]["rms_uv"] == 38.0)
    check("D7: dos intentos", len(m.d7) == 2)
    check("D7: pico y polaridad", m.d7[0]["pico"] == 4246 and m.d7[0]["polaridad"] == "POSITIVA")
    check("D7: fallido sin polaridad", m.d7[1]["polaridad"] == "sin definir")
    check("probe: enlace arriba", p.link.up and p.link.pings == 3)
    check("hw: perfil parseado", p.hw == {"oled": 0, "geo": 1})

    # Umbral corregido a esta placa: la etapa 1 pasa con 100 y no con 200.
    check("umbral de placa = 100 uV/codigo", abs(D2_MIN_SLOPE_UV_PLACA - 100.0) < 1e-9)
    check("LSB de placa = 1875 uV", abs(LSB_UV_PLACA - 1875.0) < 1e-9)
    veredicto = m.d2_veredicto_placa()
    check("D2 corregido: etapa 0 reprueba", veredicto[0][2] is False)
    check("D2 corregido: etapa 1 aprueba", veredicto[1][2] is True)

    # El parser incremental tiene que dar lo mismo que el de un solo golpe.
    p_batch = ChecklistParser()
    p_batch.feed_many(GOOD_REPORT)
    p_inc = ChecklistParser()
    for linea in GOOD_REPORT.splitlines():
        p_inc.feed(linea)
    check(
        "incremental da lo mismo que de un golpe",
        [i.as_dict() for i in p_inc.items] == [i.as_dict() for i in p_batch.items]
        and evaluate(p_inc) == evaluate(p_batch),
    )
    check("run_finished tras el #JSON", p_inc.run_finished)
    p_inc.reset_run()
    check("reset_run limpia la corrida", p_inc.items == [] and not p_inc.run_finished)

    ok = sum(1 for _, c in checks if c)
    print(f"\nchecklist self-test: {ok}/{len(checks)} PASS")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(self_test())
