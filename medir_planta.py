"""Caracterizacion de la planta analogica del nodo GEO, sobre la placa.

Mide lo que el firmware de calibracion supone y nunca se habia verificado:

  escalon   respuesta al escalon de cada IDAC vista en los CUATRO taps a la vez.
            De ahi salen la ganancia directa, las ganancias CRUZADAS y el tau de
            cada par (etapa, tap). Es la medida que decide cuanto hay que esperar
            entre etapas.

  curva     curva completa de un IDAC contra su tap, sin suponer linealidad.
            Hoy solo el LP tiene curva medida; las otras tres usan una constante.

  matriz    barrido de las combinaciones de ganancia PGA x PGAout, para responder
            si la cadena se puede calibrar con CUALQUIER par de ganancias.

Todo se guarda crudo en JSON antes de ajustar nada: si un ajuste esta mal, el
dato sigue estando. Los ajustes se recalculan con `--reajustar` sin volver a
tocar la placa.

Uso:
    python medir_planta.py escalon --port COM8
    python medir_planta.py curva   --port COM8 --etapa 1
    python medir_planta.py matriz  --port COM8
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SALIDA = REPO / "lab" / "planta"
RESET_PSOC = REPO / "src" / "firmware" / "psoc" / "reset_psoc.ps1"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testbench.core import console as con                      # noqa: E402
from testbench.core.lab import (                               # noqa: E402
    GAIN_CODES, SETTLE_MS, Lab, describe_stage, describe_tap,
)
from testbench.core.session import Session                     # noqa: E402

# El FIR de calibracion tiene 128 taps a 2604 Hz = 49 ms. Cualquier medida con
# un asentamiento menor mezcla el canal nuevo con el anterior, que es la trampa
# que el propio firmware documenta. El selector 2 son 120 ms: alcanza para
# vaciar el FIR con margen y deja una vuelta de cuatro canales en menos de 1 s.
SETTLE_SERIE = 2
# Para puntos de regimen permanente, el mismo que usa D2.
SETTLE_DC = 3

CANALES = (0, 1, 2, 3)


# --------------------------------------------------------------------------
# banco
# --------------------------------------------------------------------------

def reiniciar_psoc() -> bool:
    """ToggleReset por KitProg, con el puerto serie ya abierto.

    Tiene que ser en ese orden: abrir COM8 resetea el ESP, y si eso cae en
    mitad de un byte la UART de bajada del PSoC queda desincronizada. Resetear
    el PSoC ANTES de abrir el puerto no sirve de nada por ese motivo.
    """
    if not RESET_PSOC.is_file():
        print(f"  no encuentro {RESET_PSOC}")
        return False
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(RESET_PSOC)],
        capture_output=True, text=True, timeout=180,
    )
    ok = r.returncode == 0
    print("  reset_psoc: " + ("OK" if ok else f"FALLO rc={r.returncode}"))
    if not ok and r.stderr:
        print("  " + r.stderr.strip().splitlines()[-1])
    return ok


def abrir_banco(port: str, intentos: int = 3):
    """Abre la consola y deja el enlace con el PSoC ARRIBA, o aborta."""
    c = con.Console(port)
    print(f"Abriendo {port} (abrir resetea el ESP)...")
    if not c.open(wait_ready=True, timeout=25.0):
        print("  aviso: no se vio el banner del autotest; "
              "puede que el ESP no tenga slaveTest")
    sess = Session(c, on_line=None, on_item=None, on_phase=None)

    for intento in range(1, intentos + 1):
        link = sess.probe()
        if link.up:
            print(f"  enlace ARRIBA (ok={link.frames_ok} pings={link.pings})")
            return c, sess, Lab(sess)
        print(f"  enlace CAIDO (intento {intento}/{intentos}); reseteo el PSoC")
        reiniciar_psoc()
        # El firmware puede correr auto-calibracion al arrancar; si se le pide
        # una medida en el medio, contesta cualquier cosa o no contesta.
        time.sleep(25.0)

    c.close()
    raise SystemExit("El enlace con el PSoC no subio. Revisar COM8 y el KitProg.")


def guardar(nombre: str, payload: dict) -> Path:
    SALIDA.mkdir(parents=True, exist_ok=True)
    ruta = SALIDA / nombre
    ruta.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n  crudo -> {ruta}")
    return ruta


# --------------------------------------------------------------------------
# ajuste exponencial
# --------------------------------------------------------------------------

@dataclass
class Ajuste:
    """y(t) = y_inf + A*exp(-t/tau), ajustado por grilla en tau + LS lineal."""
    y_inf_uv: float
    amplitud_uv: float
    tau_s: float
    rms_uv: float
    n: int
    #: True si la amplitud no supera al ruido: no hay exponencial que ajustar
    plano: bool


def ajustar_exponencial(ts: list[float], ys: list[float],
                        piso_uv: float = 30.0) -> Ajuste | None:
    """Ajuste sin scipy: grilla logaritmica en tau, minimos cuadrados en el resto.

    Para cada tau candidato, y_inf y A salen de un sistema lineal 2x2, que es
    exacto. Solo tau se busca por grilla, y como es un unico parametro con un
    minimo bien definido, la grilla alcanza y sobra.
    """
    n = len(ts)
    if n < 6:
        return None
    excursion = max(ys) - min(ys)
    if excursion < piso_uv:
        media = sum(ys) / n
        return Ajuste(media, 0.0, float("nan"),
                      math.sqrt(sum((y - media) ** 2 for y in ys) / n), n, True)

    span = ts[-1] - ts[0]
    mejor: Ajuste | None = None
    # De un decimo de la ventana a diez veces: cubre 1 s .. 1500 s con span=150 s
    for k in range(140):
        tau = (span / 10.0) * (10.0 ** (k / 69.0))       # 0,1..10 x span, log
        e = [math.exp(-(t - ts[0]) / tau) for t in ts]
        # LS lineal: y = y_inf*1 + A*e
        s11, s1e, see = float(n), sum(e), sum(x * x for x in e)
        s1y = sum(ys)
        sey = sum(x * y for x, y in zip(e, ys))
        det = s11 * see - s1e * s1e
        if abs(det) < 1e-12:
            continue
        y_inf = (s1y * see - s1e * sey) / det
        amp = (s11 * sey - s1e * s1y) / det
        resid = [y - (y_inf + amp * x) for x, y in zip(e, ys)]
        rms = math.sqrt(sum(r * r for r in resid) / n)
        if mejor is None or rms < mejor.rms_uv:
            mejor = Ajuste(y_inf, amp, tau, rms, n, False)
    return mejor


# --------------------------------------------------------------------------
# experimento: respuesta al escalon
# --------------------------------------------------------------------------

def serie_taps(lab: Lab, duracion_s: float, t0: float) -> list[dict]:
    """Lee los cuatro taps en ronda hasta cumplir la duracion."""
    puntos: list[dict] = []
    while time.monotonic() - t0 < duracion_s:
        for ch in CANALES:
            p = lab.measure_dc(ch, SETTLE_SERIE)
            if p is None:
                continue
            puntos.append({"t": round(time.monotonic() - t0, 3),
                           "ch": ch, "uv": p.mean_uv})
    return puntos


def exp_escalon(lab: Lab, etapas: list[int], amplitud: int,
                duracion_s: float, pga: int, pgaout: int) -> dict:
    """Escalon de cada IDAC, mirando los CUATRO taps.

    Da de una sola corrida: ganancia directa, ganancias cruzadas y tau por par.
    """
    print(f"\n=== ESCALON  PGA={GAIN_CODES[pga]}x  PGAout={GAIN_CODES[pgaout]}x "
          f"  amplitud {amplitud:+d} codigos  {duracion_s:.0f} s por tramo ===")
    lab.set_gain("pga", pga)
    lab.set_gain("pgaout", pgaout)

    ensayos = []
    for etapa in etapas:
        for destino in (amplitud, 0):
            etiqueta = f"etapa {etapa} -> {destino:+d}"
            print(f"\n  {etiqueta}  ({describe_stage(etapa)})")
            base = {ch: lab.measure_dc(ch, SETTLE_DC) for ch in CANALES}
            base_uv = {ch: (p.mean_uv if p else None) for ch, p in base.items()}
            print("    antes:  " + "  ".join(
                f"ch{ch} {(base_uv[ch] or 0)/1000.0:8.3f} mV" for ch in CANALES))

            if not lab.set_idac(etapa, destino):
                print("    FALLO el set_idac; salteo")
                continue
            t0 = time.monotonic()
            puntos = serie_taps(lab, duracion_s, t0)
            print(f"    {len(puntos)} puntos en {duracion_s:.0f} s")

            ajustes = {}
            for ch in CANALES:
                ts = [p["t"] for p in puntos if p["ch"] == ch]
                ys = [float(p["uv"]) for p in puntos if p["ch"] == ch]
                aj = ajustar_exponencial(ts, ys)
                ajustes[ch] = asdict(aj) if aj else None
                if aj is None:
                    continue
                if aj.plano:
                    print(f"    ch{ch} {describe_tap(ch):<9} plano "
                          f"({aj.y_inf_uv/1000.0:8.3f} mV, rms {aj.rms_uv:.0f} uV)")
                else:
                    print(f"    ch{ch} {describe_tap(ch):<9} "
                          f"y_inf {aj.y_inf_uv/1000.0:8.3f} mV   "
                          f"A {aj.amplitud_uv/1000.0:8.3f} mV   "
                          f"tau {aj.tau_s:7.2f} s   rms {aj.rms_uv:.0f} uV")

            ensayos.append({
                "etapa": etapa, "destino": destino, "base_uv": base_uv,
                "puntos": puntos, "ajustes": ajustes,
            })
        lab.set_idac(etapa, 0)

    return {"experimento": "escalon", "pga_code": pga, "pgaout_code": pgaout,
            "pga_x": GAIN_CODES[pga], "pgaout_x": GAIN_CODES[pgaout],
            "amplitud": amplitud, "duracion_s": duracion_s,
            "settle_sel": SETTLE_SERIE, "settle_ms": SETTLE_MS[SETTLE_SERIE],
            "ensayos": ensayos}


# --------------------------------------------------------------------------
# experimento: curva completa
# --------------------------------------------------------------------------

def exp_curva(lab: Lab, etapa: int, lo: int, hi: int, paso: int,
              settle_sel: int, pga: int, pgaout: int, todos: bool,
              dwell_s: float = 0.0) -> dict:
    """Curva del IDAC contra los taps, sin suponer linealidad.

    `dwell_s` es la espera de PLANTA entre mover el IDAC y medir, y no tiene
    nada que ver con `settle_sel`, que es el asentamiento del mux/FIR. Hace
    falta porque el acople medido tiene tau ~ 29 s: con los 500 ms de D2 cada
    punto se toma sobre un transitorio.

    No alcanza para llegar a regimen -serian 5 tau = 150 s por punto, o sea
    horas por etapa- pero con pasos iguales el residuo e^(-T/tau) es
    aproximadamente constante, asi que sesga la pendiente global y NO la forma
    de la curva, que es lo que se quiere ver aca.
    """
    canales = CANALES if todos else (etapa,)
    print(f"\n=== CURVA etapa {etapa} ({describe_stage(etapa)})  "
          f"PGA={GAIN_CODES[pga]}x PGAout={GAIN_CODES[pgaout]}x  "
          f"{lo}..{hi} paso {paso}  settle {SETTLE_MS[settle_sel]} ms ===")
    lab.set_gain("pga", pga)
    lab.set_gain("pgaout", pgaout)

    puntos = []
    for code in range(lo, hi + 1, paso):
        if not lab.set_idac(etapa, code):
            print(f"  codigo {code:>4}  FALLO set_idac")
            continue
        if dwell_s > 0.0:
            time.sleep(dwell_s)
        fila = {"code": code}
        for ch in canales:
            p = lab.measure_dc(ch, settle_sel)
            fila[f"ch{ch}"] = p.mean_uv if p else None
        puntos.append(fila)
        print("  codigo {:>4}  ".format(code) + "  ".join(
            f"ch{ch} {(fila[f'ch{ch}'] or 0)/1000.0:9.3f} mV" for ch in canales))
    lab.set_idac(etapa, 0)

    return {"experimento": "curva", "etapa": etapa, "lo": lo, "hi": hi,
            "paso": paso, "settle_sel": settle_sel, "dwell_s": dwell_s,
            "settle_ms": SETTLE_MS[settle_sel], "canales": list(canales),
            "pga_code": pga, "pgaout_code": pgaout,
            "pga_x": GAIN_CODES[pga], "pgaout_x": GAIN_CODES[pgaout],
            "puntos": puntos}


# --------------------------------------------------------------------------
# experimento: matriz de ganancias
# --------------------------------------------------------------------------

def autoridad_y_offset(lab: Lab, espera_s: float) -> dict:
    """Para una combinacion de ganancias: offset en reposo y autoridad por etapa.

    El offset es el tap con los cuatro IDAC en 0 (referencia). La autoridad es
    cuanto mueve ese mismo tap el IDAC yendo a +255 y a -255. Si la autoridad no
    cubre al offset, esa combinacion NO se puede calibrar, y eso es exactamente
    lo que hay que saber.
    """
    for st in CANALES:
        lab.set_idac(st, 0)
    time.sleep(espera_s)
    reposo = {ch: (lab.measure_dc(ch, SETTLE_DC) or None) for ch in CANALES}
    reposo_uv = {ch: (p.mean_uv if p else None) for ch, p in reposo.items()}

    autoridad = {}
    for st in CANALES:
        extremos = {}
        for code in (255, -255):
            lab.set_idac(st, code)
            time.sleep(espera_s)
            p = lab.measure_dc(st, SETTLE_DC)
            extremos[code] = p.mean_uv if p else None
        lab.set_idac(st, 0)
        autoridad[st] = extremos
    time.sleep(espera_s)
    return {"reposo_uv": reposo_uv, "autoridad_uv": autoridad}


def exp_matriz(lab: Lab, combos: list[tuple[int, int]], espera_s: float) -> dict:
    print(f"\n=== MATRIZ de ganancias: {len(combos)} combinaciones, "
          f"espera {espera_s:.0f} s por punto ===")
    filas = []
    for i, (pga, pgaout) in enumerate(combos, 1):
        print(f"\n  [{i}/{len(combos)}] PGA={GAIN_CODES[pga]}x "
              f"PGAout={GAIN_CODES[pgaout]}x")
        lab.set_gain("pga", pga)
        lab.set_gain("pgaout", pgaout)
        d = solo_offset(lab, espera_s) if rapido else autoridad_y_offset(lab, espera_s)
        if rapido:
            d["autoridad_uv"] = {}
        d.update({"pga_code": pga, "pgaout_code": pgaout,
                  "pga_x": GAIN_CODES[pga], "pgaout_x": GAIN_CODES[pgaout]})
        filas.append(d)
        print("    reposo: " + "  ".join(
            f"ch{ch} {(d['reposo_uv'][ch] or 0)/1000.0:9.3f} mV" for ch in CANALES))
    return {"experimento": "matriz", "espera_s": espera_s, "filas": filas}


# --------------------------------------------------------------------------

def combos_por_dificultad() -> list[tuple[int, int]]:
    """Las 81 combinaciones, de la mas dificil de calibrar a la mas facil.

    El criterio no es arbitrario, sale de lo ya medido:

    PRIMERA VERSION DE ESTO ESTABA MAL, Y LA CORRECCION IMPORTA.

    Habia ordenado PGA ascendente razonando que "PGA bajo = poca autoridad = mas
    dificil": con el PGA en 1x el recorrido de Vref_PGA es de solo 28,2 mV. Pero
    eso ignora que el OFFSET en ch0 tambien escala con la ganancia del PGA, asi
    que lo que decide no es la autoridad sino la RAZON autoridad/offset.

    Y lo medido apunta al reves: 28,2 mV de recorrido con el PGA en 1x contra
    355 mV en 50x son x12,6 de autoridad para x50 de ganancia. Si el offset
    crece x50 y la autoridad x12,6, el caso dificil es PGA ALTO, cuatro veces
    peor que el bajo.

    Salvo que hay una trampa que impide cerrarlo por razonamiento: en 50x, un
    recorrido de +-478 mV en la referencia serian +-24 V a la salida, muy fuera
    de los rieles. O sea que esos 355 mV son RECORTE, no autoridad real, y ahi
    el modo de falla es saturacion y no falta de rango. Son dos problemas
    distintos y no se descartan uno al otro.

    Como no se puede decidir sin medir, este orden NO apuesta a ninguna de las
    dos hipotesis: recorre primero las CUATRO ESQUINAS -los dos extremos de cada
    PGA cruzados entre si- y despues va cerrando hacia el centro. Asi las
    primeras cuatro combinaciones ya cubren el peor caso sea cual sea de los dos,
    que es lo que se quiere de una campana que puede quedar a medias.
    """
    n = len(GAIN_CODES)
    combos = []
    for i_pga in range(n):
        for i_out in range(n):
            # Distancia al centro de cada eje: 0 en el medio, maxima en los
            # extremos. Se ordena por la MAYOR de las dos, asi las esquinas
            # -donde las dos son maximas- salen primero.
            d_pga = abs(i_pga - (n - 1) / 2.0)
            d_out = abs(i_out - (n - 1) / 2.0)
            combos.append((min(d_pga, d_out), d_pga + d_out, i_pga, i_out))
    # min() primero privilegia las esquinas sobre los bordes; la suma desempata.
    combos.sort(key=lambda c: (-c[0], -c[1]))
    return [(p, o) for _, _, p, o in combos]


#: Selector de cantidad de muestras para la medida de AC. 4 = 4096 muestras a
#: 2604 Hz = 1,57 s por canal. Con cuatro canales por combinación son ~6 s, que
#: contra una espera de planta de 60 s es ruido en el presupuesto de tiempo.
AC_N_SEL = 4


def solo_offset(lab: Lab, espera_s: float) -> dict:
    """El reposo de la combinación: DC de los cuatro taps y ruido de los cuatro.

    Diez veces más rápido que `autoridad_y_offset` porque paga UNA espera de
    planta en vez de diez. La autoridad no hace falta medirla en cada
    combinación: sale de la ganancia de la etapa, que ya está medida, y se
    verifica en unos pocos puntos en vez de en los 81.

    Además del DC se toma el **AC de los cuatro taps**, y eso no es un extra
    gratuito sino que contesta tres preguntas de una sola campaña:

    - **Ganancias del camino de SEÑAL.** El geófono entrega ruido ambiente de
      banda ancha, o sea la misma excitación en todos los taps al mismo tiempo.
      El cociente de RMS entre taps consecutivos ES la ganancia de la etapa que
      hay en el medio, medida sobre la señal real. Eso no se puede sacar de la
      matriz de acople, que es referencia→tap y no entrada→tap.
    - **Los pesos de la optimización conjunta.** Elías pidió ponderar por
      ganancia acumulada, porque las etapas con más ganancia son las que
      saturan. La ganancia acumulada hasta el tap j es justamente RMS_j/RMS_0.
    - **El ranking de combinaciones.** Cuál par de ganancias entrega mejor señal
      antes de recortar.

    OJO con interpretar el RMS a ganancia alta: si el DC se fue lejos del
    centro, el tap puede estar recortando y entonces el RMS miente por abajo.
    Por eso se guarda el DC junto al AC y hay que mirarlos juntos.
    """
    for st in CANALES:
        lab.set_idac(st, 0)
    time.sleep(espera_s)
    dc = {ch: lab.measure_dc(ch, SETTLE_DC) for ch in CANALES}
    ac = {ch: lab.measure_ac(ch, AC_N_SEL) for ch in CANALES}
    return {
        "reposo_uv": {ch: (p.mean_uv if p else None) for ch, p in dc.items()},
        "ac_n_sel": AC_N_SEL,
        "ac": {ch: (None if p is None else {
            "media_uv": p.mean_uv, "rms_uv": p.rms_uv,
            "pp_uv": p.pp_uv, "hz50_uv": p.hz50_uv, "ok": p.ok,
        }) for ch, p in ac.items()},
    }


def exp_campana(lab: Lab, cons, espera_s: float, max_combos: int,
                stamp: str, rapido: bool = False) -> None:
    """La campana completa, desatendida y REANUDABLE.

    Guarda cada combinacion apenas la termina, en su propio archivo. Si se corta
    -o si hay que apagar el banco- al volver a correr saltea lo que ya esta y
    sigue donde iba. Con 81 combinaciones y esperas de minutos, dar por perdida
    una corrida de horas por un corte no es aceptable.
    """
    SALIDA.mkdir(parents=True, exist_ok=True)
    combos = combos_por_dificultad()[:max_combos]
    print(f"\n=== CAMPANA: {len(combos)} combinaciones, de la mas dificil a la mas facil ===")
    print(f"    espera {espera_s:.0f} s por punto; se guarda cada una al terminar\n")

    for i, (pga, pgaout) in enumerate(combos, 1):
        ruta = SALIDA / f"campana_pga{pga}_out{pgaout}.json"
        if ruta.is_file():
            print(f"  [{i}/{len(combos)}] PGA={GAIN_CODES[pga]}x "
                  f"PGAout={GAIN_CODES[pgaout]}x  ya estaba, salteo")
            continue
        print(f"  [{i}/{len(combos)}] PGA={GAIN_CODES[pga]}x "
              f"PGAout={GAIN_CODES[pgaout]}x")
        lab.set_gain("pga", pga)
        lab.set_gain("pgaout", pgaout)
        d = solo_offset(lab, espera_s) if rapido else autoridad_y_offset(lab, espera_s)
        if rapido:
            d["autoridad_uv"] = {}
        d.update({"experimento": "matriz", "pga_code": pga, "pgaout_code": pgaout,
                  "pga_x": GAIN_CODES[pga], "pgaout_x": GAIN_CODES[pgaout],
                  "espera_s": espera_s, "filas": None})
        # Se guarda con la forma que espera el analizador: una fila por archivo.
        payload = {"experimento": "matriz", "espera_s": espera_s, "filas": [d]}
        ruta.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                        encoding="utf-8")
        print(f"      reposo: " + "  ".join(
            f"ch{ch} {(d['reposo_uv'][ch] or 0)/1000.0:9.3f} mV" for ch in CANALES))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM8")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("escalon", help="respuesta al escalon en los 4 taps")
    e.add_argument("--etapas", default="0,1,2,3")
    e.add_argument("--amplitud", type=int, default=100)
    e.add_argument("--duracion", type=float, default=150.0)
    e.add_argument("--pga", type=int, default=0)
    e.add_argument("--pgaout", type=int, default=0)

    c = sub.add_parser("curva", help="curva completa de un IDAC")
    c.add_argument("--etapa", type=int, required=True)
    c.add_argument("--lo", type=int, default=-255)
    c.add_argument("--hi", type=int, default=255)
    c.add_argument("--paso", type=int, default=15)
    c.add_argument("--settle", type=int, default=SETTLE_DC)
    c.add_argument("--pga", type=int, default=0)
    c.add_argument("--pgaout", type=int, default=0)
    c.add_argument("--todos", action="store_true",
                   help="medir los cuatro taps, no solo el propio")
    c.add_argument("--dwell", type=float, default=0.0,
                   help="espera de PLANTA por punto, en segundos (tau ~ 29 s)")

    k = sub.add_parser("campana", help="las 81 combinaciones, de la mas dificil "
                                      "a la mas facil, desatendida y reanudable")
    k.add_argument("--espera", type=float, default=150.0)
    k.add_argument("--max", type=int, default=81)
    k.add_argument("--rapido", action="store_true",
                   help="solo el reposo: 1 espera por combinacion en vez de 10")

    m = sub.add_parser("matriz", help="barrido de combinaciones PGA x PGAout")
    m.add_argument("--combos", default="",
                   help="lista pga:pgaout separada por comas; vacio = las 4 esquinas")
    m.add_argument("--espera", type=float, default=150.0)

    args = ap.parse_args()
    cons, sess, lab = abrir_banco(args.port)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    try:
        if args.cmd == "escalon":
            etapas = [int(x) for x in args.etapas.split(",") if x.strip()]
            d = exp_escalon(lab, etapas, args.amplitud, args.duracion,
                            args.pga, args.pgaout)
            guardar(f"escalon_pga{args.pga}_out{args.pgaout}_{stamp}.json", d)
        elif args.cmd == "curva":
            d = exp_curva(lab, args.etapa, args.lo, args.hi, args.paso,
                          args.settle, args.pga, args.pgaout, args.todos,
                          args.dwell)
            guardar(f"curva_e{args.etapa}_pga{args.pga}_out{args.pgaout}_{stamp}.json", d)
        elif args.cmd == "campana":
            exp_campana(lab, cons, args.espera, args.max, stamp, args.rapido)
        else:
            if args.combos:
                combos = [tuple(int(v) for v in par.split(":"))
                          for par in args.combos.split(",")]
            else:
                combos = [(0, 8), (8, 8), (0, 0), (8, 0)]
            d = exp_matriz(lab, combos, args.espera)
            guardar(f"matriz_{stamp}.json", d)
    finally:
        for st in CANALES:
            try:
                lab.set_idac(st, 0)
            except Exception:
                pass
        cons.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
