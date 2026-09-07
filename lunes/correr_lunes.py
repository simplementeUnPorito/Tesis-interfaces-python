# -*- coding: utf-8 -*-
"""El runner de la sesion del lunes. Encadena los ensayos en el orden util.

COMO SE USA

    python -m lunes.correr_lunes antes      # ANTES de tocar el soldador
    python -m lunes.correr_lunes despues    # despues de cada cambio
    python -m lunes.correr_lunes completo   # la bateria entera (~1 h)

    python -m lunes.t1_autoridad 3          # un ensayo suelto, la etapa que sea

POR QUE HAY DOS MODOS CORTOS Y UNO LARGO
Porque el lunes el cuello de botella es el tiempo entre soldaduras, no el
analisis. `antes` y `despues` son los diez minutos que hacen que la comparacion
signifique algo: la misma medida, en las mismas condiciones, con el unico cambio
siendo la resistencia. `completo` se corre una vez, al final, cuando ya se sabe
que la placa quedo bien.

EL ORDEN NO ES ARBITRARIO
  T0 primero porque es la huella que permite comparar, y porque si una pendiente
     que no se toco cambio, hay que parar y revisar el soldado antes de gastar
     una hora de bateria.
  T1 despues porque caracteriza el actuador que se cambio y dice si el cambio
     hizo lo que se esperaba, en volts por codigo.
  T4 pegado a T1 porque cruza sus dos salidas y no toca la placa: cuesta
     segundos y avisa si el par grueso+fino dejo huecos, que es la trampa
     clasica al achicar la resistencia del fino.
  T3 antes que T2 porque es el criterio de aceptacion: si el nodo se calibra
     solo, lo demas es caracterizacion; si no, T2 dice desde donde partia.
  T2 al final porque es el mas largo y el que menos depende del resultado de
     los otros.

TODO QUEDA EN JSON en lab/lunes/, un archivo por ensayo con fecha en el nombre,
asi que dos corridas del mismo ensayo nunca se pisan.
"""
from __future__ import annotations
import sys
import time
import traceback
import json
from datetime import datetime

from . import (t0_linea_base, t1_autoridad, t2_sin_calibrar,
               t3_cal_firmware, t4_encadenado)

_ultima_t1_adder = None


def _correr_t1_adder(log):
    global _ultima_t1_adder
    _ultima_t1_adder = t1_autoridad.correr(2, 3, log=log)
    return _ultima_t1_adder


def _correr_t1_lp_precentrado(log):
    """Mide el LP sólo después de llevar la cadena a su región útil con ADDER.

    Con todos los IDAC en cero el LP de esta placa queda oculto por saturación;
    barrerlo así había producido una curva vacía aunque el actuador funcionaba.
    """
    if not _ultima_t1_adder:
        raise SystemExit("no hay una curva ADDER previa para precentrar el LP")
    with open(_ultima_t1_adder, encoding="utf-8") as f:
        codigo = json.load(f).get("codigo_vref")
    if codigo is None:
        raise SystemExit("la curva ADDER no encontró un código operativo cerca de Vref")
    log("LP se mide con ADDER fijado en %+d (Vref de la curva anterior)" % codigo)
    return t1_autoridad.correr(3, 3, semillas={2: codigo}, log=log)

PLANES = {
    "antes":   [("T0 linea base", lambda log: t0_linea_base.correr(log=log))],
    "despues": [("T0 linea base", lambda log: t0_linea_base.correr(log=log)),
                ("T1 autoridad del ADDER", _correr_t1_adder),
                ("T1 autoridad del LP", _correr_t1_lp_precentrado),
                ("T4 encadenado", lambda log: t4_encadenado.correr(log=log))],
    "completo": [("T0 linea base", lambda log: t0_linea_base.correr(log=log)),
                 ("T1 autoridad del ADDER", _correr_t1_adder),
                 ("T1 autoridad del LP", _correr_t1_lp_precentrado),
                 ("T4 encadenado", lambda log: t4_encadenado.correr(log=log)),
                 ("T3 se calibra solo", lambda log: t3_cal_firmware.correr(log=log)),
                 ("T2 sin calibrar", lambda log: t2_sin_calibrar.correr(log=log))],
}


def main(plan_nombre="despues"):
    plan = PLANES.get(plan_nombre)
    if plan is None:
        print("planes: %s" % ", ".join(sorted(PLANES)))
        raise SystemExit(2)

    marca = datetime.now().strftime("%Y%m%d_%H%M")
    bitacora = r'C:\Github\Tesis\lab\lunes\corrida_%s_%s.log' % (plan_nombre, marca)
    import os
    os.makedirs(os.path.dirname(bitacora), exist_ok=True)
    f = open(bitacora, "w", encoding="utf-8")

    def log(t=""):
        print(t, flush=True)
        f.write(str(t) + "\n")
        f.flush()

    log("BATERIA DEL LUNES - plan '%s' - %s" % (plan_nombre, marca))
    log("")
    resultados = []
    t_total = time.time()
    for nombre, correr in plan:
        log("")
        log("#" * 70)
        log("# %s" % nombre)
        log("#" * 70)
        t0 = time.time()
        try:
            ruta = correr(log)
            resultados.append((nombre, "ok", round(time.time() - t0), ruta))
        except SystemExit as e:
            # Una guarda que corta es un resultado, no un accidente: significa
            # que el banco no estaba en el estado que el ensayo necesita. Se
            # anota y se sigue con el siguiente, que puede no depender de eso.
            log("")
            log("CORTADO: %s" % e)
            resultados.append((nombre, "cortado: %s" % e, round(time.time() - t0), None))
        except Exception:
            log("")
            log("ERROR inesperado:")
            log(traceback.format_exc())
            resultados.append((nombre, "error", round(time.time() - t0), None))

    log("")
    log("=" * 70)
    log("RESUMEN DE LA CORRIDA  (%.0f min en total)" % ((time.time() - t_total) / 60))
    for nombre, estado, seg, ruta in resultados:
        log("  %-26s %-10s %4d s  %s" % (nombre, estado, seg, ruta or ""))
    log("")
    log("bitacora -> %s" % bitacora)
    f.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "despues")
