# -*- coding: utf-8 -*-
"""T1 - La curva completa de un actuador sobre el tap que importa.

ES EL ENSAYO MAS IMPORTANTE DE LA BATERIA, y el que hay que correr primero
despues de cambiar cualquier resistencia, porque contesta las tres preguntas que
deciden todo lo demas de una sola vez:

  1. CUANTO MUEVE por codigo, en volts reales. Es lo que cambia al cambiar la
     resistencia, y es el numero que se quiere verificar.
  2. DONDE ESTA EL PUNTO UTIL, o sea con que codigo la cadena queda en Vref.
  3. CUANTO DEL RECORRIDO SIRVE. El 2026-09-06 este ensayo mostro que el 75 %
     del recorrido del ADDER se gastaba en llegar al punto de operacion, con el
     LP saturado y sin transmitir nada, y que el firmware cortaba justo un paso
     antes de que la cadena empezara a responder.

POR QUE EN LAZO ABIERTO
Porque un lazo cerrado sobre una cadena saturada no mide autoridad: mide su
propia frustracion. Aca no hay nada que interpretar -se pone un codigo, se
espera, se lee-, y por eso este ensayo destapo lo que dos ensayos con el lazo
cerrado no habian podido.

POR QUE BARRE TODO EL RECORRIDO
Porque el error que costo mas caro del fin de semana fue barrer hasta el limite
que ponia el firmware y leer el resultado como una propiedad del circuito. El
barrido llega a +-255, que es el limite del hardware, y punto.

Uso:
    python -m lunes.t1_autoridad [etapa] [--tap N] [--pga N] [--pgaout N]
                                  [--seed ETAPA CODIGO]

    etapa: 0 PGA, 1 BP, 2 ADDER, 3 LP   (por defecto 2, el ADDER)
    tap:   canal que se mira            (por defecto 3, el del LP, que es el
                                         que se captura y el unico cuyo error
                                         importa de verdad)

    --seed: deja otro IDAC en un codigo conocido antes del barrido. Es
            imprescindible para medir el LP cuando la cadena con todos los
            IDAC en cero esta saturada; por ejemplo, despues de localizar Vref
            del ADDER en -192:

            python -m lunes.t1_autoridad 3 --seed 2 -192
"""
from __future__ import annotations
import sys
import time

from .comun import (abrir_banco, poner_idac, cerar_idacs, leer_taps,
                    describir_taps, esperar_quieto, guardar,
                    lectura_valida, en_riel, delta_a_voltios, a_voltios,
                    VREF_V, GANANCIA, CAMPO_PGA, CAMPO_PGAOUT)

NOMBRES = {0: "PGA", 1: "BP", 2: "ADDER", 3: "LP"}

#: Grueso en todo el recorrido, y fino donde suele estar el codo. El signo se
#: barre en los dos sentidos porque cual sirve depende de la etapa y del signo
#: de su ganancia, y eso es justamente lo que se quiere averiguar.
def codigos_del_barrido(paso_grueso=32):
    negativos = list(range(-paso_grueso, -256, -paso_grueso)) + [-255]
    positivos = list(range(paso_grueso, 256, paso_grueso)) + [255]
    return [0] + negativos + [0] + positivos


def correr(etapa=2, tap=3, pga=CAMPO_PGA, pgaout=CAMPO_PGAOUT,
           paso_grueso=32, semillas=None, log=print):
    semillas = dict(semillas or {})
    nombre = NOMBRES.get(etapa, str(etapa))
    log("=" * 70)
    log("T1  autoridad del %s (IDAC %d) sobre el tap %d, en lazo abierto"
        % (nombre, etapa, tap))
    log("    PGA x%d, PGAout x%d" % (GANANCIA[pga], GANANCIA[pgaout]))
    log("=" * 70)

    c, s, lab = abrir_banco(pga, pgaout, log=log)
    try:
        mal = cerar_idacs(lab)
        if mal:
            raise SystemExit("ABORTA: el firmware rechazo los IDAC %s" % mal)

        for etapa_seed, code_seed in sorted(semillas.items()):
            if etapa_seed == etapa:
                raise SystemExit("ABORTA: --seed no puede fijar la misma etapa "
                                 "que se esta barriendo")
            if not poner_idac(lab, etapa_seed, code_seed):
                raise SystemExit("ABORTA: el firmware rechazo seed IDAC %d = %d"
                                 % (etapa_seed, code_seed))
            log("seed: IDAC %d = %+d" % (etapa_seed, code_seed))

        log("")
        if semillas:
            log("asentando con los IDAC no indicados en cero y las semillas aplicadas...")
        else:
            log("asentando con todos los IDAC en cero...")
        v, t_s, quieto = esperar_quieto(lab, log=lambda t: log("  " + t))
        log(describir_taps(v))
        partida = dict(v)

        filas = []
        for code in codigos_del_barrido(paso_grueso):
            if not poner_idac(lab, etapa, code):
                log("  el firmware rechazo el codigo %d; se corta el barrido" % code)
                break
            v, t_s, quieto = esperar_quieto(lab, log=None)
            mv = v.get(tap)
            marca = ""
            if mv is not None and not lectura_valida(mv):
                marca = "  <- fuera de la ventana observable"
            elif mv is not None and en_riel(mv):
                marca = "  ->  %.3f V  <- SIN GUARDA ANTIRRIEL" % a_voltios(mv)
            elif mv is not None:
                marca = "  ->  %.3f V  (%+.0f mV de Vref)" % (
                    a_voltios(mv), (a_voltios(mv) - VREF_V) * 1000)
            log("  IDAC %d = %+5d   tap %d = %s%s%s" % (
                etapa, code, tap,
                ("%9.2f" % mv) if mv is not None else "sin lectura", marca,
                "" if quieto else "   [no asentada]"))
            filas.append({"code": code, "taps_mv": {str(k): v[k] for k in v},
                          "asentada": quieto, "segundos": round(t_s, 1)})

        # ------------------------------------------------------------------
        # AFINAR EL UMBRAL, que es el numero que decide si el firmware llega.
        #
        # El barrido grueso dice ENTRE QUE DOS CODIGOS el tap entra o sale de la
        # ventana observable, y con pasos de 32 eso es una incertidumbre de 32
        # codigos. No alcanza: el 2026-09-06 la diferencia entre "el firmware
        # llega" y "no llega" fueron 16 codigos -su clamp estaba en -128 y el
        # tap empezaba a moverse entre -128 y -144-. Un umbral con +-32 de
        # incertidumbre no distingue esos dos mundos.
        #
        # Asi que donde el barrido grueso cruza la frontera, se vuelve con
        # pasos de a 4.
        cruces = []
        for a, b in zip(filas, filas[1:]):
            va = a["taps_mv"].get(str(tap))
            vb = b["taps_mv"].get(str(tap))
            if va is None or vb is None:
                continue
            if lectura_valida(va) != lectura_valida(vb):
                cruces.append((a["code"], b["code"]))
        for lo, hi in cruces[:2]:      # dos cruces como mucho: entrada y salida
            paso = 4 if abs(hi - lo) > 8 else 1
            log("")
            log("  afinando el umbral entre %+d y %+d, de a %d codigos" % (lo, hi, paso))
            avance = paso if hi > lo else -paso
            for code in range(lo + avance, hi, avance):
                if not poner_idac(lab, etapa, code):
                    break
                v, t_s, quieto = esperar_quieto(lab, log=None)
                mv = v.get(tap)
                dentro = mv is not None and lectura_valida(mv)
                log("  IDAC %d = %+5d   tap %d = %s   %s" % (
                    etapa, code, tap,
                    ("%9.2f" % mv) if mv is not None else "sin lectura",
                    "EN VENTANA" if dentro else "zona ciega"))
                filas.append({"code": code, "taps_mv": {str(k): v[k] for k in v},
                              "asentada": quieto, "segundos": round(t_s, 1),
                              "afinado": True})

    finally:
        # Un corte o excepcion a mitad del barrido no debe dejar ni el actuador
        # barrido ni los seeds aplicados. Restaurar los cuatro es mas seguro
        # que confiar en que se alcanzo el final normal del bucle.
        cerar_idacs(lab)
        c.close()

    filas.sort(key=lambda f: f["code"])

    # ---- lo que sale del barrido ------------------------------------------
    # La ventana observable del ADC es más ancha que la región donde la etapa
    # analógica todavía transmite. Una lectura a 4,81 V es real para el ADC,
    # pero es el amplificador pegado al riel y no demuestra autoridad del IDAC.
    observables = [f for f in filas
                   if lectura_valida(f["taps_mv"].get(str(tap)))]
    utiles = [f for f in observables
              if not en_riel(f["taps_mv"].get(str(tap)))]
    puntos = [(f["code"], f["taps_mv"].get(str(tap))) for f in utiles]
    n = len(puntos)
    if n >= 2:
        mx = sum(c for c, _ in puntos) / n
        my = sum(mv for _, mv in puntos) / n
        den = sum((c - mx) ** 2 for c, _ in puntos)
        m_banco = (sum((c - mx) * (mv - my) for c, mv in puntos) / den
                   if den else None)
        uv_reales = (delta_a_voltios(m_banco) * 1e6
                     if m_banco is not None else None)
    else:
        m_banco = uv_reales = None
    codigo_vref = None
    if utiles:
        codigo_vref = min(utiles,
                          key=lambda f: abs(a_voltios(f["taps_mv"][str(tap)]) - VREF_V))

    log("")
    log("-" * 70)
    if m_banco is None:
        log("NO HAY PENDIENTE MEDIBLE: el tap %d nunca entro en la region" % tap)
        log("operativa con guarda antirriel en el recorrido del %s. O la etapa no tiene" % nombre)
        log("autoridad sobre ese tap, o hay otra etapa saturada cortando el")
        log("camino. Correr T1 sobre las otras etapas para distinguir.")
    else:
        log("PENDIENTE      %.4f mV de banco por codigo = %.0f uV REALES por codigo"
            % (m_banco, uv_reales))
        log("               (ajustada sobre %d puntos operativos)" % n)
        log("RECORRIDO UTIL %d de %d codigos barridos conservan guarda antirriel"
            % (len(utiles), len(filas)))
        log("OBSERVABLE     %d de %d codigos son legibles por el ADC"
            % (len(observables), len(filas)))
        # La region util, con sus dos bordes. Es el numero contra el que hay que
        # comparar el clamp del firmware: si el clamp no llega al borde, el lazo
        # no puede alcanzar la region donde su medida significa algo.
        entran = sorted(f["code"] for f in filas
                        if lectura_valida(f["taps_mv"].get(str(tap)))
                        and not en_riel(f["taps_mv"].get(str(tap))))
        if entran:
            log("REGION UTIL    el tap esta en la ventana entre los codigos "
                "%+d y %+d" % (entran[0], entran[-1]))
            afinado = any(f.get("afinado") for f in filas)
            log("               (borde localizado con paso de %s codigos)"
                % ("4" if afinado else "32, SIN afinar"))
        if codigo_vref is not None:
            mv = codigo_vref["taps_mv"][str(tap)]
            log("PUNTO DE VREF  codigo %+d deja el tap en %.3f V (%.1f de banco)"
                % (codigo_vref["code"], a_voltios(mv), mv))
            gastado = abs(codigo_vref["code"]) / 255.0 * 100.0
            log("               o sea que el %.0f %% del recorrido se gasta en LLEGAR" % gastado)
            if gastado > 40.0:
                log("")
                log("               OJO: mas del 40 %% del recorrido se va en alcanzar el")
                log("               punto de operacion. Eso no se arregla cambiando la")
                log("               resistencia en serie -su alcance en volts es el que")
                log("               hace falta- sino corriendo el CERO de la referencia.")
                log("               Hacen falta %d codigos x 1875 uV = %.0f mV."
                    % (abs(codigo_vref["code"]), abs(codigo_vref["code"]) * 1.875))

    ruta = guardar("t1_autoridad_e%d_tap%d" % (etapa, tap),
                   {"etapa": etapa, "tap": tap,
                    "pga_x": GANANCIA[pga], "pgaout_x": GANANCIA[pgaout],
                    "semillas": {str(k): v for k, v in semillas.items()},
                    "partida_mv": {str(k): partida[k] for k in partida},
                    "mv_banco_por_codigo": m_banco,
                    "uv_reales_por_codigo": uv_reales,
                    "puntos_observables": len(observables),
                    "puntos_operativos": len(utiles),
                    "codigo_vref": codigo_vref["code"] if codigo_vref else None,
                    "filas": filas})
    log("")
    log("datos -> %s" % ruta)
    return ruta


if __name__ == "__main__":
    args = sys.argv[1:]
    etapa = int(args[0]) if args and not args[0].startswith("-") else 2
    def opc(nombre, defecto):
        return int(args[args.index(nombre) + 1]) if nombre in args else defecto
    semillas = {}
    if "--seed" in args:
        i = args.index("--seed")
        if i + 2 >= len(args):
            raise SystemExit("uso de --seed: --seed ETAPA CODIGO")
        semillas[int(args[i + 1])] = int(args[i + 2])
    correr(etapa=etapa, tap=opc("--tap", 3),
           pga=opc("--pga", CAMPO_PGA), pgaout=opc("--pgaout", CAMPO_PGAOUT),
           semillas=semillas)
