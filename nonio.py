"""Nonio de dos actuadores gruesos: la resolucion sale de la DIFERENCIA.

EL PROBLEMA

A ganancia alta ningun actuador solo puede aterrizar LPo. La cuenta, con los
numeros medidos el 2026-09-13:

    IDAC2 -> ch2      +12,2 mV/codigo
    etapa LP          x(-17,95) desde SUMo
    => IDAC2 -> LPo   -219 * G mV/codigo

o sea -3.504 mV por codigo a PGAout x16, contra una tolerancia de 200 mV. El
actuador se pasa de largo diecisiete veces la tolerancia con el paso mas chico
que tiene. Y no es un problema de rango: le sobra recorrido. Es de granularidad.

IDAC3 es el vernier natural -6,68 mV por codigo- pero su recorrido total es de
solo +-1.703 mV, asi que solo sirve si el grueso ya dejo el error por debajo de
eso. A x16 el error de cuantizacion del grueso llega a +-1.752 mV: no entra, por
poco.

LA SALIDA

IDAC1 e IDAC2 mueven ch2 con pasos DISTINTOS e inconmensurables -8,3 y
12,2 mV/codigo-, que es exactamente la condicion de un nonio. La combinacion

    3 codigos de IDAC1  contra  -2 de IDAC2

deja 3*(-2.384) - 2*(-3.504) = -144 mV a x16: veinticuatro veces mas fina que el
paso de cualquiera de los dos por separado. Con eso el grueso entrega el error
por debajo del recorrido de IDAC3, y IDAC3 termina.

Este modulo es solo la aritmetica de elegir el par. No toca hardware, y por eso
se puede probar sin la placa.

EL PRECIO

IDAC1 es la referencia del pasabanda y arrastra la constante de 44 s del
capacitor de 680 uF. Por eso la busqueda PENALIZA moverlo: entre dos pares que
dejan un error parecido, elige el que mueve menos IDAC1. Un paso de IDAC2 es
gratis; uno de IDAC1 cuesta un minuto de espera.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Cuanto mas caro es mover un codigo del actuador lento que uno del rapido.
#: No es un tiempo: es el peso con el que la busqueda desempata.
COSTO_LENTO = 40.0
#: Y el peso del rapido, que no es cero para que entre dos pares igual de
#: buenos gane el que se mueve menos.
COSTO_RAPIDO = 1.0
#: Escalon que separa las soluciones que entran en tolerancia de las que no.
#: Mas grande que cualquier costo de movimiento alcanzable, a proposito.
FUERA_DE_TOLERANCIA = 1.0e6


@dataclass(frozen=True)
class Par:
    """Un movimiento conjunto de los dos actuadores gruesos."""

    d_lento: int
    d_rapido: int
    error_previsto: float

    def __str__(self) -> str:
        return (f"IDAC1 {self.d_lento:+d}, IDAC2 {self.d_rapido:+d} "
                f"-> queda {self.error_previsto:+.0f} mV")


def buscar_par(error_mv: float, k_lento: float, k_rapido: float,
               codigo_lento: int, codigo_rapido: int,
               alcance_lento: int = 6, alcance_rapido: int = 8,
               limite: int = 255,
               objetivo_mv: float | None = None) -> Par | None:
    """Elige el par de pasos que mejor corrige ``error_mv``.

    ``error_mv`` es lo que le falta al tap para llegar a su consigna, o sea
    consigna menos medido. ``k_lento`` y ``k_rapido`` son mV del tap por codigo
    de cada actuador, CON SIGNO.

    ``objetivo_mv`` es el error residual que alcanza con lograr; si se lo da, la
    busqueda deja de premiar la precision por debajo de ese umbral y pasa a
    premiar el movimiento chico. Sin eso, la busqueda gastaria pasos de IDAC1
    -que cuestan un minuto cada uno- para bajar de 30 mV a 5 mV de error, que
    para nada sirve.

    Devuelve None si ningun par mejora lo que ya hay.
    """
    if not k_lento or not k_rapido:
        return None
    umbral = abs(objetivo_mv) if objetivo_mv else 0.0

    def puntaje(residual: float, d_lento: int, d_rapido: int) -> float:
        """Quedar fuera de tolerancia es incomparablemente peor que moverse.

        La primera version sumaba el error y el costo en la misma moneda, y con
        eso la busqueda se negaba a gastar tres pasos lentos para corregir
        300 mV a x16: preferia quedarse fuera de tolerancia por cien milivolts
        antes que esperar tres minutos. El escalon de FUERA_DE_TOLERANCIA hace
        que cualquier solucion que entra le gane a cualquiera que no entra, y
        el costo del movimiento solo desempata entre las que entran.
        """
        if abs(residual) <= umbral:
            castigo = 0.0
        else:
            castigo = FUERA_DE_TOLERANCIA + 10.0 * abs(residual)
        return (castigo
                + COSTO_LENTO * abs(d_lento)
                + COSTO_RAPIDO * abs(d_rapido))

    mejor = Par(0, 0, error_mv)
    mejor_puntaje = puntaje(error_mv, 0, 0)
    for d_lento in range(-alcance_lento, alcance_lento + 1):
        nuevo_lento = codigo_lento + d_lento
        if not -limite <= nuevo_lento <= limite:
            continue
        for d_rapido in range(-alcance_rapido, alcance_rapido + 1):
            nuevo_rapido = codigo_rapido + d_rapido
            if not -limite <= nuevo_rapido <= limite:
                continue
            residual = error_mv - (d_lento * k_lento + d_rapido * k_rapido)
            p = puntaje(residual, d_lento, d_rapido)
            if p < mejor_puntaje:
                mejor_puntaje = p
                mejor = Par(d_lento, d_rapido, residual)
    if mejor.d_lento == 0 and mejor.d_rapido == 0:
        return None
    return mejor


def resolucion_efectiva(k_lento: float, k_rapido: float,
                        alcance_lento: int = 6,
                        alcance_rapido: int = 8) -> float:
    """El escalon mas chico distinto de cero que el par puede dar.

    Sirve para saber, ANTES de tocar la placa, si una ganancia es alcanzable:
    si esta resolucion supera la tolerancia y el vernier fino no llega a
    cubrir la diferencia, esa ganancia no entra y no tiene sentido probarla.
    """
    mejor = float("inf")
    for d_lento in range(-alcance_lento, alcance_lento + 1):
        for d_rapido in range(-alcance_rapido, alcance_rapido + 1):
            if d_lento == 0 and d_rapido == 0:
                continue
            paso = abs(d_lento * k_lento + d_rapido * k_rapido)
            if 0.0 < paso < mejor:
                mejor = paso
    return mejor
