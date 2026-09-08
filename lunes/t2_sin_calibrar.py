# -*- coding: utf-8 -*-
"""T2 - Donde queda la cadena SIN calibrar, en cada combinacion de ganancia.

ES EL ENSAYO QUE JUSTIFICA LA AUTOCALIBRACION EN LA TESIS. La pregunta es
simple: con los cuatro IDAC en cero -o sea, solo la tension de base, que es
exactamente lo que seria el nodo si no existiera el sistema de calibracion-,
¿cuantas configuraciones de ganancia arrancan con una etapa que no transmite?

El 2026-09-06 dio 12 de 14. Despues del cambio de resistencias el numero tiene
que volver a medirse: si baja mucho, parte del argumento se lo lleva el
hardware; si no baja, el argumento queda mas firme todavia.

TRES COSAS QUE HACE DISTINTO A UNA PRIMERA VERSION DE ESTE ENSAYO, y las tres
salieron de que la primera version daba resultados que no se repetian:

  - espera a que la cadena DEJE DE MOVERSE, no un tiempo fijo. Volver de la
    saturacion tarda mucho mas que 2 tau, y con espera fija cada punto heredaba
    el estado del anterior;
  - el orden ALTERNA ganancias altas y bajas, para que un efecto de historia se
    vea como incoherencia entre vecinos y no como una tendencia prolija;
  - la configuracion de campo se repite tres veces como CONTROL. Si las tres no
    coinciden en el veredicto, la medida no se puede creer y hay que decirlo en
    vez de promediar.

Y NO CONVIERTE A MILIVOLTS las lecturas de la zona ciega: abajo de 880 de banco
la lectura no mide el tap, y ponerle un numero seria inventar precision.

Uso:  python -m lunes.t2_sin_calibrar
"""
from __future__ import annotations
import sys

from .comun import (abrir_banco, cerar_idacs, esperar_quieto, guardar,
                    en_riel, a_voltios, lectura_valida, VREF_V, GANANCIA,
                    CAMPO_PGA, CAMPO_PGAOUT, TAP_SIGNAL_CHANNELS)

#: Alternado a proposito. Los tres (8,0) son el control de reproducibilidad.
COMBOS = [
    (8, 0), (0, 0), (8, 2), (1, 0), (6, 1), (0, 2), (3, 0),
    (8, 0),
    (6, 0), (1, 2), (3, 2), (0, 1), (6, 2), (3, 1), (1, 1), (8, 1),
    (8, 0),
]


def correr(combos=None, log=print):
    combos = combos or COMBOS
    log("=" * 70)
    log("T2  la cadena sin calibrar, %d puntos" % len(combos))
    log("=" * 70)

    c, s, lab = abrir_banco(log=log)
    filas = []
    try:
        for i, (pga, out) in enumerate(combos):
            etiqueta = "PGA x%-2d  PGAout x%-2d  (total x%d)" % (
                GANANCIA[pga], GANANCIA[out], GANANCIA[pga] * GANANCIA[out])
            log("")
            log("[%2d/%d] %s" % (i + 1, len(combos), etiqueta))
            if not lab.set_gain("pga", pga) or not lab.set_gain("pgaout", out):
                log("      ABORTA: el firmware no acepto la ganancia")
                break
            mal = cerar_idacs(lab)
            if mal:
                log("      ABORTA: el firmware rechazo los IDAC %s" % mal)
                break

            v, t_s, quieto = esperar_quieto(lab, log=lambda t: log("  " + t))
            railados = [k for k in TAP_SIGNAL_CHANNELS if en_riel(v.get(k))]
            # El desvio solo se informa cuando TODOS los taps estan en ventana;
            # si alguno no lo esta, la magnitud no existe y el veredicto es
            # cualitativo.
            peor = None
            if not railados:
                peor = max(abs(a_voltios(v[k]) - VREF_V) * 1000 for k in TAP_SIGNAL_CHANNELS
                           if v.get(k) is not None)
            log("      banco  " + " ".join(
                ("%9.2f" % v[k]) if v.get(k) is not None else "%9s" % "-"
                for k in TAP_SIGNAL_CHANNELS))
            log("      -> %s%s%s" % (
                ("EN RIEL: taps %s" % railados) if railados else "todas en rango",
                ("; peor tap a %.0f mV de Vref" % peor) if peor is not None else "",
                "" if quieto else "   [NO ASENTADA: dato dudoso]"))

            filas.append({"orden": i, "pga_code": pga, "pgaout_code": out,
                          "pga_x": GANANCIA[pga], "pgaout_x": GANANCIA[out],
                          "taps_mv": {str(k): v.get(k) for k in TAP_SIGNAL_CHANNELS},
                          "en_riel": railados, "peor_mv_reales": peor,
                          "segundos": round(t_s, 1), "asentada": quieto})
    finally:
        c.close()

    # ---- control de reproducibilidad --------------------------------------
    ctrl = [f for f in filas if (f["pga_code"], f["pgaout_code"]) == (CAMPO_PGA, CAMPO_PGAOUT)]
    log("")
    log("-" * 70)
    reproducible = None
    if len(ctrl) >= 2:
        log("CONTROL: la configuracion de campo medida %d veces" % len(ctrl))
        for f in ctrl:
            log("   orden %2d: %s   %s" % (
                f["orden"],
                [None if f["taps_mv"][str(k)] is None else round(f["taps_mv"][str(k)], 1)
                 for k in TAP_SIGNAL_CHANNELS],
                ("EN RIEL %s" % f["en_riel"]) if f["en_riel"] else "en rango"))
        reproducible = all(bool(f["en_riel"]) == bool(ctrl[0]["en_riel"]) for f in ctrl)
        log("   -> %s" % ("coinciden en el veredicto: la medida se puede creer"
                          if reproducible else
                          "NO COINCIDEN: el estado depende de la historia y no de la "
                          "ganancia. El resto de este ensayo NO se puede usar."))

    unicas = {}
    for f in filas:
        unicas.setdefault((f["pga_code"], f["pgaout_code"]), bool(f["en_riel"]))
    n_riel = sum(1 for x in unicas.values() if x)
    log("")
    log("RESULTADO: %d de %d combinaciones distintas arrancan con al menos una"
        % (n_riel, len(unicas)))
    log("           etapa contra el riel, o sea sin capturar nada.")
    en_rango = [k for k, x in unicas.items() if not x]
    if en_rango:
        log("           Las que arrancan en rango: %s"
            % ", ".join("x%d x%d" % (GANANCIA[a], GANANCIA[b]) for a, b in en_rango))

    ruta = guardar("t2_sin_calibrar",
                   {"reproducible": reproducible,
                    "n_en_riel": n_riel, "n_combinaciones": len(unicas),
                    "filas": filas})
    log("")
    log("datos -> %s" % ruta)
    return ruta


if __name__ == "__main__":
    correr()
