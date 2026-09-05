"""Verifica el ORIGEN DE COORDENADAS de las medidas de continua. Bloqueante.

POR QUÉ EXISTE
Todo el trabajo del 2026-09-05 informa el error del LP como desvío respecto de
**1000 mV**. Elías señaló que ése no es el objetivo del firmware para GEO:

    CAL_TARGET_GEO_PGA_MV = ... = CAL_TARGET_GEO_LP_MV = 0

Los 1000/1024/3500 mV son de HAMMER. Y en GEO la comparación es **contra Vref**
(≈2,5 V), porque la señal del geófono tiene lado positivo y negativo; el martillo
no, y por eso allá el objetivo es otro.

Leer el código NO alcanza para resolverlo: la calibración y el comando `dc` del
banco usan el mismo AMux, el mismo ADC y la misma conversión
(`counts × rango / 131072`, sin offset), y `cal_pi_compare_counts` para GEO
devuelve el valor tal cual. O sea que las cuentas son la misma magnitud, y sin
embargo el objetivo es 0 y el banco lee ~52.429 cuentas.

Hasta resolver esto, **el criterio de aceptación de todo lo medido hoy está en
duda**. No las pendientes —que son diferencias y por lo tanto inmunes a un
offset— sino DÓNDE hay que dejar cada tap.

LAS CUATRO PRUEBAS, de la más barata a la más cara

1. **Coherencia de escala entre rangos del ADC.** Se mide el mismo tap con
   ±2,5 V, ±1,024 V y ±0,625 V. La tensión física no cambió, así que los µV
   informados tienen que coincidir. Si no coinciden, la conversión está mal y
   hay un factor de escala que corregir en todo. Este comando existe en el
   firmware justamente por esta pregunta.

2. **El canal del capacitor.** `AMuxCapacitor` es 100 nF **a Vss**, o sea 0 V.
   El autotest lo informa en 1053 mV. Si la escala fuera absoluta y sin offset,
   un canal a masa tendría que leer 0. Es la pista más directa de que el cero
   del banco no es el cero físico.

3. **`snapshot` contra `dc`.** Son dos caminos de código distintos dentro del
   mismo firmware. Si coinciden, el banco no tiene un bug propio y lo que se lee
   es lo que el PSoC ve.

4. **Correr la calibración de verdad.** El firmware lleva los taps a SU objetivo.
   Leer dónde quedan, medido por el banco, dice de una vez cuál es el cero: si
   quedan en ~0 mV el objetivo es 0 y los 1000 mV eran "sin calibrar"; si quedan
   en ~1000 mV, entonces 1000 en el banco ES el cero del firmware.

    python verificar_origen.py --port COM8
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from testbench.core import console as con                  # noqa: E402
from testbench.core.lab import Lab                         # noqa: E402
from testbench.core.session import Session                 # noqa: E402

SALIDA = REPO / "lab" / "planta"
RANGOS = {1: "+-2,5 V", 2: "+-0,512 V", 3: "+-1,024 V", 4: "+-0,625 V"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None)
    ap.add_argument("--sin-cal", action="store_true",
                    help="saltea la prueba 4, que tarda varios minutos")
    a = ap.parse_args()

    c = con.Console(a.port) if a.port else con.Console()
    print(f"Abriendo {c.port}...", flush=True)
    c.open(); time.sleep(1.0)
    sess = Session(c)
    lab = Lab(sess)
    res = {"cuando": datetime.now().isoformat(timespec="seconds")}

    def crudo(cmd, idle=5.0, tmo=40.0):
        out = []
        for ln in sess.raw(cmd, idle=idle, timeout=tmo):
            out.append(ln)
            print("      " + ln, flush=True)
        return out

    try:
        # --- 1. coherencia entre rangos del ADC ---------------------------
        print("\n=== 1. EL MISMO TAP EN TRES RANGOS DEL ADC ===")
        print("Si la conversion es correcta los uV tienen que coincidir.\n")
        print(f"{'rango':>12} {'ch3 uV':>12}")
        escala = {}
        for cfg in (1, 3, 4):
            lineas = crudo(f"adc {cfg} 3", idle=6.0, tmo=40.0)
            escala[cfg] = lineas
        res["rangos"] = {str(k): v for k, v in escala.items()}
        crudo("adc 1 3")          # dejarlo como estaba

        # --- 2. el canal del capacitor, que esta a Vss --------------------
        print("\n=== 2. CANAL DEL CAPACITOR (100 nF a Vss, o sea 0 V) ===")
        print("Un canal a masa deberia leer 0 si la escala fuera absoluta.\n")
        p = lab.measure_dc(4, 3)
        res["cap_ch4_uv"] = p.mean_uv if (p and p.ok) else None
        print(f"      ch4 = {res['cap_ch4_uv']} uV")

        # --- 3. snapshot contra dc ----------------------------------------
        print("\n=== 3. SNAPSHOT DEL PSoC CONTRA dc DEL BANCO ===")
        print("Dos caminos de codigo distintos sobre el mismo hardware.\n")
        antes = {ch: lab.measure_dc(ch, 3) for ch in (0, 1, 2, 3)}
        res["dc_antes_uv"] = {str(ch): (v.mean_uv if (v and v.ok) else None)
                              for ch, v in antes.items()}
        print(f"      dc: {res['dc_antes_uv']}")
        res["snapshot"] = crudo("snapshot", idle=6.0, tmo=40.0)

        # --- 4. la calibracion del firmware -------------------------------
        if not a.sin_cal:
            print("\n=== 4. CALIBRACION DEL FIRMWARE ===")
            print("El firmware lleva los taps a SU objetivo. Donde queden, lo dice todo.\n")
            res["cal"] = crudo("cal", idle=20.0, tmo=460.0)
            time.sleep(70.0)      # que la planta se asiente antes de leer
            desp = {ch: lab.measure_dc(ch, 3) for ch in (0, 1, 2, 3)}
            res["dc_despues_uv"] = {str(ch): (v.mean_uv if (v and v.ok) else None)
                                    for ch, v in desp.items()}
            print(f"      dc despues de calibrar: {res['dc_despues_uv']}")
    finally:
        c.close()

    SALIDA.mkdir(parents=True, exist_ok=True)
    ruta = SALIDA / f"verificar_origen_{datetime.now():%Y%m%d_%H%M%S}.json"
    ruta.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"\ncrudo -> {ruta}")

    print("\nCOMO SE LEE ESTO")
    print("  - rangos que NO coinciden  -> la conversion a uV tiene un error de")
    print("    escala y hay que corregir TODO lo medido hoy por ese factor.")
    print("  - ch4 (Vss) lejos de 0     -> el cero del banco no es el cero fisico.")
    print("  - dc despues de calibrar cerca de 0     -> el objetivo es 0 de")
    print("    verdad y los 1000 mV eran simplemente 'sin calibrar'.")
    print("  - dc despues de calibrar cerca de 1000  -> 1000 en el banco ES el")
    print("    cero del firmware, y todo lo del dia vale tal cual.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
