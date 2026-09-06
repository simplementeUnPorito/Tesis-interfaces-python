# -*- coding: utf-8 -*-
"""Esperar a que la cadena DEJE DE MOVERSE, en vez de esperar un tiempo fijo.

POR QUE HACE FALTA
Hasta ahora toda medida de continua esperaba 2 tau -unos 59 s- y se daba por
asentada. Eso vale cuando el transitorio es el polo de C1 contra R4, que es
exponencial con tau = 29,5 s: a 2 tau queda el 13,5 % del salto.

No vale cuando la cadena viene de estar SATURADA. Medido el 2026-09-05: despues
de una calibracion que dejo al LP contra el riel, poner los cuatro IDAC en cero
y esperar 2 tau NO devolvia la cadena a su punto natural -seguia railada-,
mientras que la misma configuracion medida despues de un reset del PSoC quedaba
a menos de 100 mV de Vref. Dos veces el mismo experimento nominal, dos estados
de partida opuestos, y la unica diferencia era de donde venia.

La recuperacion desde saturacion es mas lenta que tau: hay 71 s de constante
medidos el 2026-09-02, y encima absorcion dielectrica del electrolitico de
680 uF, que es un proceso con varias constantes y no una sola exponencial. Con
absorcion dielectrica no hay UN tiempo de espera correcto que se pueda calcular
de antemano.

QUE SE HACE EN VEZ
Se mide hasta que la propia medida deje de cambiar. Es la unica forma honesta:
en lugar de suponer cuanto tarda, se observa cuando termino.

COSTO
Cuando la cadena ya esta quieta esto es MAS RAPIDO que esperar 2 tau a ciegas.
Cuando viene de saturacion es mas lento, que es exactamente cuando corresponde.
"""
from __future__ import annotations
import time

# La ventana donde la lectura del banco es de verdad una medida del tap. Vive en
# escala_banco.py para que haya un solo lugar que sepa donde vale la recta.
from escala_banco import lectura_valida

TAU_S = 29.5

# Cuanto puede moverse entre dos miradas para considerarla quieta, en mV de
# banco. Un mV de banco son 19,9 mV reales, asi que 1,5 mV de banco son 30 mV
# reales: por debajo de eso ya no se distingue del ruido de la propia medida.
QUIETO_MV = 1.5

# Cada cuanto se mira. Un cuarto de tau: suficientemente seguido para no
# desperdiciar tiempo cuando ya esta quieta, y suficientemente espaciado para
# que entre dos miradas haya un cambio medible si todavia se esta moviendo.
PERIODO_S = TAU_S / 4.0

# Techo duro. Si a los seis tau todavia se mueve, no es el polo de C1: es otra
# cosa y hay que mirarla, no seguir esperando.
TECHO_S = 6.0 * TAU_S


def esperar_quieto(lab, canales=(0, 1, 2, 3), quieto_mv=QUIETO_MV,
                   periodo_s=PERIODO_S, techo_s=TECHO_S, log=None):
    """Mide hasta que los taps dejen de moverse. Devuelve (taps, segundos, quieto).

    `quieto` es False si se llego al techo todavia en movimiento: el que llama
    tiene que decidir si el dato sirve, pero se entera. Un dato tomado en pleno
    transitorio no se distingue por si solo de uno asentado, y esa es justamente
    la trampa que esto viene a cerrar.
    """
    t0 = time.time()
    previo = None
    while True:
        v = {}
        for ch in canales:
            p = lab.measure_dc(ch, 3)
            v[ch] = (p.mean_uv / 1000.0) if (p and p.ok) else None

        if previo is not None:
            # SOLO CUENTAN LOS CANALES QUE ESTAN MIDIENDO ALGO.
            #
            # Por debajo de 880 de banco la lectura no es una medida del tap: da
            # tensiones negativas contra masa, que son imposibles, y sin que el
            # ADC este saturado. Lo que devuelve ahi es ruido, y ese ruido no se
            # asienta nunca.
            #
            # Sin esta exclusion, cualquier configuracion con una etapa railada
            # -que son 12 de 14- agotaba el techo de 6 tau esperando a que se
            # quedara quieto algo que no es una senal. Medido el 2026-09-06: con
            # ch3 en la zona ciega el detector seguia viendo saltos de 6 a 26 mV
            # de banco despues de tres minutos.
            saltos = [abs(v[ch] - previo[ch]) for ch in canales
                      if v[ch] is not None and previo[ch] is not None
                      and lectura_valida(v[ch]) and lectura_valida(previo[ch])]
            # Si NINGUN canal esta en ventana no hay nada que observar: se espera
            # el tiempo fijo de la planta y se devuelve marcado como no asentado,
            # que es lo honesto.
            if not saltos and time.time() - t0 >= 2.0 * TAU_S:
                if log:
                    log("      ningun tap esta en la ventana observable; se "
                        "esperaron 2 tau a ciegas")
                return v, time.time() - t0, False
            peor = max(saltos) if saltos else None
            if peor is not None and peor <= quieto_mv:
                t = time.time() - t0
                if log:
                    log("      quieta tras %.0f s (ultimo salto %.2f mV de banco)"
                        % (t, peor))
                return v, t, True
            if log and peor is not None:
                log("      +%.0f s: todavia se mueve %.2f mV de banco" %
                    (time.time() - t0, peor))

        if time.time() - t0 > techo_s:
            if log:
                log("      TECHO de %.0f s y todavia se mueve; se anota igual, "
                    "marcada como no asentada" % techo_s)
            return v, time.time() - t0, False

        previo = v
        time.sleep(periodo_s)
