# -*- coding: utf-8 -*-
"""T0 - La huella de la placa ANTES de tocar el soldador.

Es corto a proposito -unos diez minutos- porque su valor no esta en medir mucho
sino en medir lo mismo antes y despues. Sin este ensayo, cualquier diferencia
que aparezca el lunes se puede explicar por el cambio de resistencia o por que
la placa se movio, y no habria forma de distinguir.

QUE REGISTRA
  1. Donde queda la cadena con los cuatro IDAC en cero, en la configuracion de
     campo. Es el punto de partida de todo lo demas.
  2. La pendiente de CADA actuador sobre CADA tap de señal, con un escalon
     chico. Cuatro por cinco, con tres puntos cada uno.

POR QUE ESCALONES CHICOS Y NO EL BARRIDO COMPLETO
Porque el barrido completo es T1 y tarda. Aca alcanza con la pendiente local
alrededor del punto de reposo, que es lo que cambia al cambiar una resistencia:
si el LP pasa de 15 k a 1,8 k, su pendiente tiene que caer por el mismo factor,
y eso se ve con dos puntos. Si NO cae por ese factor, el que hay que revisar es
el soldado, y conviene enterarse antes de correr media hora de bateria.

CUIDADO CON LA PENDIENTE QUE NO SE PUEDE MEDIR
Con la cadena railada, varias de las veinte pendientes van a salir cero,
porque la etapa que mira esta saturada y no transmite. Eso NO es un error del
ensayo ni de la placa: es informacion, y es la misma que hizo falta descubrir a
los golpes el fin de semana. Se informan como "sin pendiente medible".

Uso:  python -m lunes.t0_linea_base [--pga N] [--pgaout N]
"""
from __future__ import annotations
import sys

from .comun import (abrir_banco, poner_idac, cerar_idacs, esperar_quieto,
                    describir_taps, leer_taps, guardar, pendiente_por_codigo,
                    lectura_valida, GANANCIA, CAMPO_PGA, CAMPO_PGAOUT,
                    TAP_NAMES, TAP_SIGNAL_CHANNELS)

NOMBRES = {0: "PGA", 1: "BP", 2: "ADDER", 3: "LP"}
#: Escalon del sondeo. 32 codigos son 60 mV de referencia: bastante para salir
#: del ruido y poco para no sacar la cadena de donde esta.
ESCALON = 32


def correr(pga=CAMPO_PGA, pgaout=CAMPO_PGAOUT, log=print):
    log("=" * 70)
    log("T0  linea base: la huella de la placa antes del cambio")
    log("    PGA x%d, PGAout x%d" % (GANANCIA[pga], GANANCIA[pgaout]))
    log("=" * 70)

    c, s, lab = abrir_banco(pga, pgaout, log=log)
    try:
        mal = cerar_idacs(lab)
        if mal:
            raise SystemExit("ABORTA: el firmware rechazo los IDAC %s" % mal)

        log("")
        log("1. punto de reposo, con los cuatro IDAC en cero")
        reposo, t_s, quieto = esperar_quieto(lab, log=lambda t: log("   " + t))
        log(describir_taps(reposo))
        if not quieto:
            log("   OJO: no llego a asentarse. El resto del ensayo igual sirve,")
            log("   porque las pendientes son diferencias y el arrastre comun se")
            log("   cancela, pero el punto de reposo de arriba es provisorio.")

        log("")
        log("2. pendiente de cada actuador sobre cada tap")
        log("   %-8s %-8s %14s %16s" % ("actuador", "tap", "mV banco/cod", "uV reales/cod"))
        matriz = {}
        for etapa in range(4):
            puntos_por_tap = {ch: [] for ch in TAP_SIGNAL_CHANNELS}
            for code in (-ESCALON, 0, ESCALON):
                if not poner_idac(lab, etapa, code):
                    log("   el firmware rechazo IDAC %d = %d; se saltea esta etapa"
                        % (etapa, code))
                    puntos_por_tap = None
                    break
                v, _, _ = esperar_quieto(lab, log=None)
                for ch in TAP_SIGNAL_CHANNELS:
                    puntos_por_tap[ch].append((code, v.get(ch)))
            poner_idac(lab, etapa, 0)
            if puntos_por_tap is None:
                continue
            for ch in TAP_SIGNAL_CHANNELS:
                m, uv, n = pendiente_por_codigo(puntos_por_tap[ch])
                matriz["%d->%d" % (etapa, ch)] = {"mv_banco": m, "uv_reales": uv, "n": n}
                if m is None:
                    log("   %-8s %-8s %14s %16s" % (
                        NOMBRES[etapa], "ch%d %s" % (ch, TAP_NAMES[ch]),
                        "-", "sin pendiente medible"))
                else:
                    log("   %-8s %-8s %14.4f %16.0f" % (
                        NOMBRES[etapa], "ch%d %s" % (ch, TAP_NAMES[ch]), m, uv))
    finally:
        c.close()

    log("")
    log("-" * 70)
    log("COMO SE USA ESTO EL LUNES")
    log("  Correr T0 antes de desoldar y de nuevo despues de cada cambio. La")
    log("  pendiente de la etapa que se toco tiene que cambiar por el mismo")
    log("  factor que la resistencia; las otras relaciones, no. Si cambia alguna")
    log("  no se toco, hay algo mal en el soldado y conviene parar ahi.")

    ruta = guardar("t0_linea_base",
                   {"pga_x": GANANCIA[pga], "pgaout_x": GANANCIA[pgaout],
                    "reposo_mv": {str(k): reposo.get(k) for k in TAP_SIGNAL_CHANNELS},
                    "reposo_asentado": quieto,
                    "pendientes": matriz})
    log("")
    log("datos -> %s" % ruta)
    return ruta


if __name__ == "__main__":
    args = sys.argv[1:]
    def opc(nombre, defecto):
        return int(args[args.index(nombre) + 1]) if nombre in args else defecto
    correr(pga=opc("--pga", CAMPO_PGA), pgaout=opc("--pgaout", CAMPO_PGAOUT))
