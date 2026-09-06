# -*- coding: utf-8 -*-
"""T4 - ¿El actuador fino cubre un escalon del grueso? Sin tocar la placa.

LA PREGUNTA
La calibracion usa dos actuadores en cascada: el ADDER recorre la excursion y el
LP hace los ultimos milivolts. Para que eso funcione, el recorrido COMPLETO del
fino tiene que ser mayor que UN PASO del grueso. Si no lo es, quedan huecos:
tensiones que ningun par de codigos puede alcanzar, y la calibracion se queda
oscilando entre los dos bordes de un hueco sin poder entrar.

Es la trampa clasica de un par grueso+fino, y es exactamente lo que hay que
verificar despues de cambiar la resistencia del LP: bajarla lo hace mas preciso
-que es lo que se busca- pero le achica el recorrido, y en algun punto deja de
cubrir.

POR QUE NO TOCA LA PLACA
Porque los dos numeros que hacen falta ya los midio T1: la pendiente del grueso
en uV por codigo, y el recorrido util del fino. Este ensayo solo los cruza. Se
puede correr sobre datos viejos, y conviene correrlo ANTES de desoldar para
saber cuanto margen hay.

Uso:
    python -m lunes.t4_encadenado                  # los ultimos T1 de cada etapa
    python -m lunes.t4_encadenado <grueso.json> <fino.json>
"""
from __future__ import annotations
import sys
import os
import json
import glob

from .comun import SALIDA, lectura_valida, a_voltios, guardar

NOMBRES = {0: "PGA", 1: "BP", 2: "ADDER", 3: "LP"}


def ultimo_t1(etapa):
    cands = sorted(glob.glob(os.path.join(SALIDA, "t1_autoridad_e%d_tap*.json" % etapa)))
    return cands[-1] if cands else None


def recorrido_util_uv(datos):
    """Excursion REAL que el actuador logra en el tap, en uV, dentro de la ventana."""
    tap = str(datos["tap"])
    vals = [f["taps_mv"][tap] for f in datos["filas"]
            if f["taps_mv"].get(tap) is not None and lectura_valida(f["taps_mv"][tap])]
    if len(vals) < 2:
        return None
    return (a_voltios(max(vals)) - a_voltios(min(vals))) * 1e6


def correr(ruta_grueso=None, ruta_fino=None, log=print):
    ruta_grueso = ruta_grueso or ultimo_t1(2)
    ruta_fino = ruta_fino or ultimo_t1(3)
    if not ruta_grueso or not ruta_fino:
        log("Faltan datos de T1. Corre primero:")
        log("   python -m lunes.t1_autoridad 2")
        log("   python -m lunes.t1_autoridad 3")
        raise SystemExit(2)

    g = json.load(open(ruta_grueso, encoding="utf-8"))
    f = json.load(open(ruta_fino, encoding="utf-8"))

    log("=" * 70)
    log("T4  encadenado grueso -> fino, sobre datos de T1")
    log("    grueso: %s" % os.path.basename(ruta_grueso))
    log("    fino:   %s" % os.path.basename(ruta_fino))
    log("=" * 70)

    paso_grueso_uv = g.get("uv_reales_por_codigo")
    recorrido_fino_uv = recorrido_util_uv(f)
    paso_fino_uv = f.get("uv_reales_por_codigo")

    log("")
    if paso_grueso_uv is None:
        log("El grueso (%s) no tiene pendiente medible en ese tap: T4 no puede"
            % NOMBRES.get(g["etapa"], g["etapa"]))
        log("decidir nada. Revisar T1 del grueso antes que esto.")
        raise SystemExit(1)
    log("paso del grueso (%s)      %8.0f uV reales por codigo"
        % (NOMBRES.get(g["etapa"], g["etapa"]), abs(paso_grueso_uv)))
    if paso_fino_uv is not None:
        log("paso del fino (%s)         %8.0f uV reales por codigo"
            % (NOMBRES.get(f["etapa"], f["etapa"]), abs(paso_fino_uv)))
    if recorrido_fino_uv is None:
        log("recorrido del fino          no medible: nunca entro en la ventana")
        log("")
        log("VEREDICTO: no se puede decidir. El fino no mueve el tap dentro de la")
        log("ventana observable, asi que no hay con que cubrir nada. Eso ya es un")
        log("problema por si mismo y es anterior a la pregunta del encadenado.")
        raise SystemExit(1)
    log("recorrido total del fino    %8.0f uV reales" % recorrido_fino_uv)

    cobertura = recorrido_fino_uv / abs(paso_grueso_uv)
    log("")
    log("COBERTURA: el recorrido del fino son %.1f pasos del grueso" % cobertura)
    log("")
    if cobertura >= 2.0:
        log("VEREDICTO: CUBRE, con margen. El par encadena sin huecos: cualquier")
        log("tension entre dos codigos del grueso es alcanzable con el fino.")
    elif cobertura >= 1.0:
        log("VEREDICTO: CUBRE JUSTO (%.1f pasos). Encadena, pero sin margen: si la"
            % cobertura)
        log("pendiente del grueso sube un poco -y sube con la ganancia- deja de")
        log("cubrir. Conviene no bajar mas la resistencia del fino.")
    else:
        log("VEREDICTO: NO CUBRE. Quedan HUECOS de %.0f uV que ningun par de"
            % (abs(paso_grueso_uv) - recorrido_fino_uv))
        log("codigos puede alcanzar. La calibracion va a oscilar entre los dos")
        log("bordes de un hueco sin poder entrar, y eso NO se arregla con mas")
        log("iteraciones ni con mejor sintonia: hay que devolverle recorrido al")
        log("fino (subir su resistencia) o achicarle el paso al grueso.")

    ruta = guardar("t4_encadenado",
                   {"grueso": os.path.basename(ruta_grueso),
                    "fino": os.path.basename(ruta_fino),
                    "paso_grueso_uv": paso_grueso_uv,
                    "paso_fino_uv": paso_fino_uv,
                    "recorrido_fino_uv": recorrido_fino_uv,
                    "cobertura_en_pasos": cobertura})
    log("")
    log("datos -> %s" % ruta)
    return ruta


if __name__ == "__main__":
    a = sys.argv[1:]
    correr(a[0] if len(a) > 0 else None, a[1] if len(a) > 1 else None)
