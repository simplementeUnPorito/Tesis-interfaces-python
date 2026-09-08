# -*- coding: utf-8 -*-
"""T3 - ¿El nodo se calibra SOLO? Es el criterio de aceptacion.

Todo lo demas de la bateria mide propiedades de la cadena. Este mide lo unico
que importa en campo: si el nodo, sin PC, deja el tap del LP cerca de Vref.

QUE MIRA, Y EN QUE ORDEN DE IMPORTANCIA
  1. Donde quedo el tap del LP. Es el que se captura.
  2. Si alguna etapa quedo contra el riel. Una etapa saturada no transmite, y
     eso no se compensa con que las otras esten bien.
  3. El `ok` que devuelve el firmware. Va tercero a proposito: un ok=1 con el LP
     lejos de Vref es un fracaso, y un ok=0 con la cadena centrada es informacion
     util sobre el criterio de aceptacion del firmware, no sobre la cadena.

Y RECONSTRUYE LA TRAYECTORIA, que es lo que convierte un "no anduvo" en un
diagnostico. El PSoC emite la medida de cada paso; con la serie se ve en que
paso salio de la zona ciega, cuantos pasos le llevo desde ahi, y si al final se
quedo quieto o seguia corrigiendo. Sin esto, el fin de semana del 2026-09-05 se
habrian perdido varias horas mirando solo el veredicto final.

Uso:  python -m lunes.t3_cal_firmware [--pga N] [--pgaout N]
"""
from __future__ import annotations
import sys
import re
import time

from .comun import (abrir_banco, cerar_idacs, esperar_quieto, describir_taps,
                    leer_taps, guardar, en_riel, a_voltios, lectura_valida,
                    VREF_V, GANANCIA, CAMPO_PGA, CAMPO_PGAOUT, TAU_S,
                    TAP_LP, TAP_SIGNAL_CHANNELS)

#: Cuentas -> mV de banco, con la escala del propio ADC (18 bits sobre +-2,5 V).
LSB_UV = 2_500_000 / 131072

RE_EV = re.compile(r"<psoc (0x[0-9A-Fa-f]+|[A-Z_]+) val=(\d+)")
EV_BEGIN, EV_MEAS32 = 0x15, 0x1D
#: Estos dos son los que contestan "¿por que dijo ok=0 si llego?". CAL_PI_STABLE
#: cuenta las muestras seguidas dentro de la banda muerta -el criterio de cierre
#: pide tres- y CAL_STAGE_OK es el veredicto por etapa. Sin ellos hay que
#: adivinar entre "no llego" y "llego pero no alcanzo a confirmarlo".
EV_STABLE, EV_STAGE_OK, EV_DEADBAND = 0x43, 0x16, 0x40

#: El firmware puede tardar ~950 s en el peor caso. El plazo del ESP es 1500 s.
PLAZO_S = 1500.0


def reconstruir_trayectoria(lineas):
    """Los eventos de un byte del PSoC -> (traza, cierre).

    traza  = [(etapa, cuentas), ...], la serie que uso el lazo para decidir.
    cierre = {etapa: {"stable": n, "ok": v, "deadband": n}}, que es lo que
             distingue "el lazo no llego" de "llego pero no alcanzo a
             confirmarlo con las tres muestras seguidas que su criterio exige".
             Sin esto hay que adivinar entre esas dos cosas, que piden arreglos
             distintos.
    """
    etapa, bytes_meas, traza = None, [], []
    cierre = {}
    for ln in lineas:
        m = RE_EV.search(ln)
        if not m:
            continue
        ev = m.group(1)
        ev = int(ev, 16) if ev.startswith("0x") else None
        val = int(m.group(2))
        if ev == EV_BEGIN:
            etapa, bytes_meas = val, []
            cierre.setdefault(etapa, {})
        elif ev == EV_STABLE and etapa is not None:
            # El MAXIMO alcanzado, no el ultimo: interesa cuanto llego a
            # acercarse al criterio de cierre.
            cierre[etapa]["stable"] = max(cierre[etapa].get("stable", 0), val)
        elif ev == EV_STAGE_OK and etapa is not None:
            cierre[etapa]["ok"] = val
        elif ev == EV_DEADBAND and etapa is not None:
            cierre[etapa]["deadband"] = val
        elif ev == EV_MEAS32:
            bytes_meas.append(val)
            if len(bytes_meas) == 4:
                n = (bytes_meas[0] << 24) | (bytes_meas[1] << 16) | \
                    (bytes_meas[2] << 8) | bytes_meas[3]
                if n >= 1 << 31:
                    n -= 1 << 32
                traza.append((etapa, n))
                bytes_meas = []
    return traza, cierre


def correr(pga=CAMPO_PGA, pgaout=CAMPO_PGAOUT, log=print):
    log("=" * 70)
    log("T3  la calibracion del firmware, con PGA x%d / PGAout x%d"
        % (GANANCIA[pga], GANANCIA[pgaout]))
    log("=" * 70)

    c, s, lab = abrir_banco(pga, pgaout, log=log)
    try:
        mal = cerar_idacs(lab)
        if mal:
            raise SystemExit("ABORTA: el firmware rechazo los IDAC %s" % mal)

        for ln in s.raw("diag on", idle=1.5, timeout=8.0):
            log("  " + ln)

        log("")
        log("asentando con los IDAC en cero (este es el peor caso: la cadena")
        log("arranca donde quedaria un nodo recien encendido)...")
        antes, t_s, quieto = esperar_quieto(lab, log=lambda t: log("  " + t))
        log(describir_taps(antes))

        log("")
        log("corriendo `cal`...")
        t0 = time.time()
        crudo = []
        for ln in s.raw("cal", idle=PLAZO_S, timeout=PLAZO_S + 20,
                        until=lambda l: l.startswith("#CAL")):
            crudo.append(ln)
            if ln.startswith("#CAL") or ln.startswith("[ST]"):
                log("  " + ln)
        dur = time.time() - t0
        ok_fw = None
        for ln in crudo:
            if ln.startswith("#CAL"):
                try:
                    ok_fw = int(ln.split()[1])
                except (IndexError, ValueError):
                    pass
        log("  (%.0f s de reloj)" % dur)

        traza, cierre = reconstruir_trayectoria(crudo)
        if traza:
            log("")
            log("TRAYECTORIA (cada fila es una medida que el lazo uso para decidir)")
            log("  %5s %6s %12s %12s  %s" % ("etapa", "paso", "cuentas", "banco mV", ""))
            prev, paso = None, 0
            for et, n in traza:
                if et != prev:
                    paso, prev = 0, et
                paso += 1
                banco = n * LSB_UV / 1000.0
                nota = "" if lectura_valida(banco) else "  <- zona ciega"
                log("  %5s %6d %12d %12.2f%s" % (et, paso, n, banco, nota))

        log("")
        log("esperando a que se asiente lo que dejo la calibracion...")
        despues, t_s2, quieto2 = esperar_quieto(lab, log=lambda t: log("  " + t))
        log(describir_taps(despues))
    finally:
        c.close()

    # ---- el veredicto, en el orden en que importa -------------------------
    lp = despues.get(TAP_LP)
    railadas = [k for k in TAP_SIGNAL_CHANNELS if en_riel(despues.get(k))]
    error_lp = None
    if lp is not None and lectura_valida(lp):
        error_lp = (a_voltios(lp) - VREF_V) * 1000.0

    log("")
    log("-" * 70)
    log("VEREDICTO")
    if error_lp is None:
        log("  1. el tap del LP quedo FUERA de la ventana observable: la cadena")
        log("     no captura. Es el peor resultado posible y no depende de con")
        log("     que criterio lo juzgue el firmware.")
    else:
        log("  1. el tap del LP quedo a %+.0f mV de Vref" % error_lp)
    # ch0 se excluye a proposito: a x50 su desvio es el offset de entrada
    # amplificado, se acepto explicitamente, y no lo corrige ninguna etapa.
    otras = [k for k in railadas if k != 0]
    if otras:
        log("  2. CONTRA EL RIEL: %s. Esas etapas no transmiten." % otras)
    else:
        log("  2. ninguna etapa de la cadena quedo contra el riel")
    log("  3. el firmware informo ok=%s en %.0f s" % (ok_fw, dur))
    for et in sorted(cierre):
        c_ = cierre[et]
        if not c_:
            continue
        log("     etapa %s: ok=%s, banda muerta %s cuentas, llego a %s muestras"
            % (et, c_.get("ok", "?"), c_.get("deadband", "?"), c_.get("stable", 0)))
    if ok_fw == 0 and error_lp is not None and abs(error_lp) < 100:
        estables = max((c_.get("stable", 0) for c_ in cierre.values()), default=0)
        log("")
        log("     ok=0 con el LP a menos de 100 mV de Vref. El lazo LLEGO. Lo que")
        log("     falta es el cierre: su criterio pide tres muestras seguidas")
        log("     dentro de la banda muerta y llego a %d." % estables)
        if estables >= 1:
            log("     Que llegue a %d y no a 3 dice que entro en banda y se quedo" % estables)
            log("     sin presupuesto de pasos, no que no supiera llegar: el")
            log("     arreglo es subir CAL_PI_TIMEOUT_SAMPLES de esa etapa.")
        else:
            log("     Que no llegue a ninguna dice otra cosa: nunca entro en la")
            log("     banda muerta, asi que el problema es la banda o la")
            log("     resolucion del actuador, no el presupuesto.")

    ruta = guardar("t3_cal_firmware",
                   {"pga_x": GANANCIA[pga], "pgaout_x": GANANCIA[pgaout],
                    "antes_mv": {str(k): antes[k] for k in antes},
                    "despues_mv": {str(k): despues[k] for k in despues},
                    "ok_firmware": ok_fw, "segundos": round(dur, 1),
                    "error_lp_mv_reales": error_lp,
                    "railadas": railadas,
                    "traza": [{"etapa": e, "cuentas": n} for e, n in traza]})
    log("")
    log("datos -> %s" % ruta)
    return ruta


if __name__ == "__main__":
    args = sys.argv[1:]
    def opc(nombre, defecto):
        return int(args[args.index(nombre) + 1]) if nombre in args else defecto
    correr(pga=opc("--pga", CAMPO_PGA), pgaout=opc("--pgaout", CAMPO_PGAOUT))
