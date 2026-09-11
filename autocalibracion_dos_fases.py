"""Autocalibración GEO en dos fases, guiada por margen y por ganancia.

Fase 1 elige IDAC0/IDAC1 mediante una optimización lineal con restricciones de
margen y deja IDAC2 cerca de cero. Fase 2 se repite por cada ganancia porque el
margen permitido en ch2 disminuye como 1/G.

Valores de respaldo medidos el 2026-09-10 (magnitudes reales respecto de Vref):

    SUM desde SEo: a=3,97 (27k/6,8k); SUM desde BPo: b=3,61 (27k/7,48k)
    etapa no inversora: n=8,58
    IDAC2 -> ch2: 5,1 mV/código (ABBA)
    IDAC0 -> ch0: 49,7 mV/código (ABBA)
    IDAC1 -> ch1: 2,0 mV/código
    LP desde ch2: -9,07 mV/mV (R2=0,993)
    R de IDAC2: 5,1 kOhm (ohmetro); tau BP dominante: ~29,5 s

SUM y LP tienen constantes de milisegundos. Un salto de IDAC0 obliga a esperar
el tau del acople desde SEo; Vref_BP (IDAC1) mueve BPo en forma inmediata.
Nunca se selecciona ch5.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
import unittest
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable

from escala_banco import (BANCO_MAX_VALIDO_MV, BANCO_MIN_VALIDO_MV,
                          BANCO_RIEL_ALTO_MEDIDO_MV, BANCO_VREF_MV, FACTOR,
                          clavado)
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab, TAP_NAMES
from testbench.core.session import Session

TARGET_UV = int(round(BANCO_VREF_MV * 1000.0))
VALID_MIN_UV = int(round(BANCO_MIN_VALIDO_MV * 1000.0))
VALID_MAX_UV = int(round(BANCO_MAX_VALIDO_MV * 1000.0))
PGA_X50_CODE = GAIN_CODES.index(50)
PGAOUT_X1_CODE = GAIN_CODES.index(1)
HISTORY_PATH = (Path(__file__).resolve().parents[3] / "lab" /
                "calibracion_nueva" / "autocal_history.json")

MARGEN_RIEL_MV = 250.0
# 1 V a cada riel es el requisito duro de fase 1 y 1,5 V es el objetivo
# deseable. Exigir 2 V sería inviable: aun con centrado perfecto sólo quedan
# unos 2,23 V a cada riel.
# 1,25 V es GRATIS: sobre el reposo medido el optimo no cambia entre exigir 1 y
# 1,25 V. Recien a 1,5 V se paga (la reserva minima de actuador baja de 58 % a
# 35 %) y a 1,75 V queda en 4 %, o sea sin autoridad para derivar. El techo
# fisico es 2,23 V, que es lo que deja el centrado perfecto entre 0,04 y 4,50 V.
MARGEN_FASE1_MV = 1250.0
# PRIORIDAD FIJADA POR ELIAS el 2026-09-10 de noche: las ganancias ALTAS
# importan mas que las bajas, porque son las que se usan con el martillo lejos,
# donde la senal es chica y la medicion es dificil. Se listan en ese orden para
# que una campana interrumpida haya cubierto primero lo que mas importa.
#
# Con IDAC3 en 10 kOhm esta prioridad NO cuesta nada: el error final de LPo es
# medio paso de IDAC3 y NO depende de la ganancia, porque IDAC3 actua DESPUES de
# PGAout. Son 22 mV tanto en x1 como en x50. Lo unico que se aprieta con la
# ganancia es cuan fino debe quedar ch2, y ahi sobra: en x50 el presupuesto es de
# 24 mV contra 2,65 de cuantizacion de IDAC2, o sea nueve veces de margen.
#
# x32 y x50 entran en el compromiso. Se los habia descartado por arrastrar el
# paso de IDAC3 mal medido, sobre un tap clavado.
#: SALTO DE ch2 AL CONMUTAR PGAout, en mV fisicos respecto de x1.
#:
#: Medido el 2026-09-10 sobre 475 ciclos con el punto congelado
#: (`lab/calibracion_nueva/salto_por_ganancia_20260910_224639.csv`). ch2 esta
#: ANTES de PGAout y aun asi se mueve cientos de mV cuando cambia esa ganancia.
#: El salto es REPETIBLE, con desvio de 13 a 161 mV segun la ganancia, y por eso
#: se puede anticipar en vez de perseguir.
#:
#: OJO: es propio de ESTA placa. Con varios nodos hay que aprenderlo por nodo, y
#: por eso el calibrador lo guarda en `saltos_ch2_por_ganancia` en vez de leerlo
#: de aca. Esto es solo el arranque.
SALTOS_CH2_MEDIDOS_MV = {
    1: +0.0,
    2: +144.0,
    4: +717.0,
    8: +759.0,
    16: +580.0,
    24: +383.0,
    32: +243.0,
    50: +122.0,
}

GANANCIAS_COMPROMETIDAS = (24, 16, 32, 50, 8, 4, 2, 1)
# Ya no hay ganancias de "mejor esfuerzo" separadas: con 10 kOhm entran todas.
# x48 queda afuera solo por no estar medida, no por ser inalcanzable.
GANANCIAS_MEJOR_ESFUERZO = (48,)
# 1,75 V útiles conservadores menos 250 mV de guarda dan 1500 mV en x1.
SWING_LPO_MV = 1750.0
# Relaciones del sumador inversor: Rf/Rin = 27 kOhm / 6,8 kOhm para
# SEo (ch0), y 27 kOhm / 7,48 kOhm para BPo (ch1). Son cocientes de
# resistencias estables entre placas; el signo inversor se aplica al derivar.
# Con el paso típico de ch1, 2,0 mV/código, b predice -7,22 mV/código:
# queda a aproximadamente 5 % de los -7,61 mV/código medidos en 52 tramos.
GANANCIA_SUM_DESDE_SEO = 27.0 / 6.8
GANANCIA_SUM_DESDE_BPO = 27.0 / 7.48
GANANCIA_NO_INVERSORA = 8.58
PASO_IDAC2_CH2_MV = 5.1
#: Ganancia de continua de la etapa LP vista desde ch2. Medida sobre el mapa del
#: 2026-09-10 con R2 = 0,993: LPo se mueve -9,07 mV por cada mV de ch2.
GANANCIA_LP_CH2_MV_POR_MV = -9.07
#: Paso de IDAC3 sobre LPo. MEDIDO el 2026-09-10 con 1 kOhm y ch2 centrado:
#: 4,3 mV/codigo cerca del punto de trabajo. El valor anterior, 16,9, era
#: DERIVADO de 1,25 mV en el nodo por una ganancia supuesta de 13,5; la ganancia
#: real de la etapa desde su referencia es ~37, no 13,5, y el paso escala con la
#: resistencia. Es un RESPALDO: la fase 2 lo mide con un ABBA local, que ademas
#: hace falta porque la respuesta es fuertemente NO lineal (11 mV/codigo en un
#: extremo y 0,9 en el otro).
PASO_IDAC3_CH4_MV = 4.3
#: Margen maximo que puede tener un tap: el centrado perfecto entre los rieles
#: medidos 0,040 V y 4,50 V deja 2,23 V a cada lado. Sirve para normalizar el
#: margen contra la reserva de actuador y poder compararlos como un solo recurso.
MARGEN_MAXIMO_MV = 2230.0
#: Mejora minima del recurso mas gastado que justifica mover un actuador LENTO.
#: Por debajo de esto conviene quedarse quieto: el salto cuesta un tau.
MEJORA_MINIMA_RECURSO = 0.15
#: Calibrado sobre tres casos: con 0,05 el optimizador todavia se movia para
#: ganar 0,16 V de margen cuando ch2 ya estaba centrado, y eso cuesta un tau.
#: Con 0,15 se queda quieto en ese caso y sigue moviendose en el reposo real,
#: donde quedarse quieto ni siquiera es factible porque IDAC2 no alcanza.
# Respaldos solamente: fase 1 intenta medir los dos primeros en cada corrida.
# Los signos de ch2 incluyen la inversión del sumador.
#
# ASIMETRIA DE POLARIDAD, medida en placa el 2026-09-10 por la noche.
# El paso de IDAC0 NO es el mismo hacia los dos lados, porque el signo se
# implementa con un bit externo (`polarity_reg`) que conmuta el camino de
# corriente. Cuatro mediciones caen en dos grupos limpios segun el signo:
#
#     hacia codigos NEGATIVOS   89 a 90 mV/codigo   (dos ensayos: 85,0 y 90,1)
#     hacia codigos POSITIVOS   50 a 53 mV/codigo   (dos ensayos: 49,7 y 52,4)
#
# Son 68 % de diferencia. El comentario de psoc_hw.h ya avisaba que la
# convencion del bit estaba sin confirmar en banco; queda confirmada, y ademas
# resulta que las dos ramas no tienen la misma escala.
#
# Consecuencia: **la pendiente hay que medirla en el mismo sentido en que se va
# a saltar**. Un ABBA simetrico alrededor del codigo 0 promedia las dos ramas y
# da un numero que no describe ninguna. El recalculo desde el salto realizado
# (ver `recalcular_modelo_fase1`) resuelve esto solo, porque el salto ya va en
# el sentido bueno.
PASO_IDAC0_CH0_NEGATIVO_MV = 89.5
PASO_IDAC0_CH0_POSITIVO_MV = 51.5
PASO_IDAC0_CH0_MV = 49.7
PASO_IDAC1_CH1_MV = 2.0
PASO_IDAC0_CH2_MV = -197.3
PASO_IDAC1_CH2_MV = -8.3
GANANCIA_LP_DESDE_CH2 = -9.07
R_IDAC2_KOHM = 5.1
TAU_BP_REFERENCIA_S = 29.5
IDAC_MIN, IDAC_MAX = -255, 255
BP_ESPERA_TAU_MIN, BP_ESPERA_TAU_MAX = 2.0, 5.0
OTRAS_ETAPAS_ESPERA_S = 30.0
ABBA_ESPERA_S = 1.5


def real_mv(bank_delta_uv: float) -> float:
    return bank_delta_uv * FACTOR / 1000.0


def bank_uv(real_delta_mv: float) -> float:
    return real_delta_mv * 1000.0 / FACTOR


#: Autoridad total de IDAC3 sobre LPo: 255 codigos por 16,9 mV cada uno.
AUTORIDAD_IDAC3_MV = 255.0 * PASO_IDAC3_CH4_MV


#: Offset propio de la etapa LP, en mV fisicos sobre LPo, con ch2 y SUMo
#: centrados. Medido el 2026-09-10: -1050 mV. Se atribuye a la corriente de
#: polarizacion del operacional por los 150 kOhm de realimentacion del MFB,
#: amplificada por la ganancia ~37 desde la referencia. Es un RESPALDO: la fase 2
#: lo mide en cada corrida leyendo LPo con ch2 ya centrado.
OFFSET_LP_RESPALDO_MV = -1050.0


def autoridad_idac3_mv(paso_lpo_mv_por_codigo: float | None = None) -> float:
    """Autoridad de IDAC3 sobre LPo, A CADA LADO del cero.

    El error que costo una corrida el 2026-09-10: calcular el alcance como el
    recorrido TOTAL y compararlo contra el offset. Lo que importa es cuanto
    puede mover a cada lado, o sea la mitad.
    """
    paso = (PASO_IDAC3_CH4_MV if paso_lpo_mv_por_codigo is None
            else abs(paso_lpo_mv_por_codigo))
    return abs(paso) * IDAC_MAX


def _usable(punto) -> bool:
    """Dentro de ventana Y con ruido propio.

    Solo comprobar `clavado` no alcanza: una lectura por DEBAJO del minimo del
    ADC tiene ruido de sobra y aun asi no significa nada. El 2026-09-10 una de
    esas produjo un offset de -11068 mV y desvio la consigna de ch2 en 300 mV.
    """
    return bool(getattr(punto, "valid", False)) and not punto.clavado


def objetivo_ch2_mv(ganancia: int,
                    autoridad_idac3_lpo_mv: float | None = None,
                    offset_lp_mv: float | None = None) -> int:
    """Máximo |ch2-Vref| tolerable en una ganancia dada.

    Hay DOS restricciones y hay que quedarse con la mas apretada:

    1. SUMo no debe rielar. Como PGAout esta referenciado al mismo Vref,
       ``SUMo - Vref = G*(ch2 - Vref)``, asi que ``|ch2| <= (swing-margen)/G``.

    2. IDAC3 debe poder centrar LPo. La etapa LP amplifica ``-9,07`` veces, o
       sea que ``LPo = ch2 * G * (-9,07)``, y IDAC3 dispone de +-4,31 V. Si el
       desplazamiento supera esa autoridad, LPo queda apoyado contra el riel y
       el lazo que tendria que corregirlo no puede ni medirlo.

    La segunda es SIEMPRE la mas apretada, por un factor de aproximadamente 2,6.
    Haber usado solo la primera es lo que aborto la validacion del 2026-09-10:
    ch2 en +1204 mV entraba en el presupuesto de x1 (1750 mV) pero mandaba LPo a
    -10,9 V, seis veces fuera de su riel.
    """
    if ganancia <= 0:
        raise ValueError("la ganancia debe ser positiva")
    por_sumo = (SWING_LPO_MV - MARGEN_RIEL_MV) / ganancia
    # MEDIDO el 2026-09-10 por la noche: con ch2 en +2010 mV, IDAC3 barrido de
    # -239 a +225 deja LPo CLAVADO en todo el recorrido salvo un unico punto de
    # transicion, y ese punto tiene pico a pico de 568 mV, o sea que esta yendo y
    # no sentado. La etapa LP esta tan fuera de su zona lineal que IDAC3 actua
    # como llave y no como ajuste.
    #
    # Por eso NO alcanza con que IDAC3 pueda cancelar el desplazamiento: hace
    # falta que LPo se quede DENTRO de sus rieles por si solo, porque un LPo
    # saturado no se puede medir y el lazo que deberia corregirlo queda ciego.
    # La cota de LPo tiene TRES terminos, no uno:
    #
    #   lo que IDAC3 puede mover a cada lado
    #   menos el offset propio de la etapa, que hay que cancelar siempre
    #   menos el margen al riel
    #
    # y lo que sobra es lo unico que ch2 tiene permitido aportar.
    #
    # Ignorar el offset es lo que dejo pasar ch2 en +162 mV el 2026-09-10: entraba
    # en el objetivo de entonces (165 mV) y aun asi mandaba LPo a -2519, pasado el
    # riel de -2440, porque el offset ponia 1050 de los 2519.
    # El offset propio de la etapa LP NO entra aca: lo absorbe el CENTRO de ch2,
    # que la fase 2 calcula como -offset/(9,07*G). IDAC3 solo tiene que limpiar
    # el residuo de cuantizacion de IDAC2 alrededor de ese centro. Esa es la
    # diferencia entre pedirle a IDAC3 1050 mV de autoridad y pedirle 24.
    #
    # Este objetivo es una TOLERANCIA alrededor del centro, no alrededor de Vref.
    autoridad = (autoridad_idac3_mv() if autoridad_idac3_lpo_mv is None
                 else abs(autoridad_idac3_lpo_mv))
    disponible = autoridad - MARGEN_RIEL_MV
    if disponible <= 0.0:
        # IDAC3 no puede ni cancelar el offset: ninguna ganancia es viable y hay
        # que decirlo, no devolver un numero que finja que si.
        return 0
    por_lpo = disponible / (ganancia * abs(GANANCIA_LP_DESDE_CH2))
    return int(round(min(por_sumo, por_lpo)))


def toca_riel(media_banco_uv: float | None, pp_banco_uv: float | None,
              margen_mv: float = MARGEN_RIEL_MV) -> bool:
    """True si media +/- pp/2 entra en el margen reservado de algún riel."""
    if media_banco_uv is None or pp_banco_uv is None or clavado(pp_banco_uv):
        return True
    low = BANCO_MIN_VALIDO_MV * 1000.0 + bank_uv(margen_mv)
    high = (min(BANCO_MAX_VALIDO_MV, BANCO_RIEL_ALTO_MEDIDO_MV) * 1000.0 -
            bank_uv(margen_mv))
    return (media_banco_uv - pp_banco_uv / 2.0 <= low or
            media_banco_uv + pp_banco_uv / 2.0 >= high)


def margen_riel_real_mv(point: "Point | dict") -> float:
    mean = point.get("mean_bank_uv") if isinstance(point, dict) else point.mean_bank_uv
    pp = point.get("pp_bank_uv") if isinstance(point, dict) else point.pp_bank_uv
    high = min(BANCO_MAX_VALIDO_MV, BANCO_RIEL_ALTO_MEDIDO_MV) * 1000.0
    return min(real_mv(mean - pp / 2.0 - BANCO_MIN_VALIDO_MV * 1000.0),
               real_mv(high - mean - pp / 2.0))


def _normalizar_taps(taps: "dict | Iterable[Point | dict]") -> dict[int, Point | dict]:
    if isinstance(taps, dict):
        return {ch: taps.get(ch, taps.get(str(ch))) for ch in range(5)}
    items = list(taps)
    return {ch: items[ch] if ch < len(items) else None for ch in range(5)}


def puntaje(taps: "dict | Iterable[Point | dict]") -> float:
    """Menor margen físico al riel de los taps, sobre media +/- pp/2.

    Es positivo dentro de ambos rieles y negativo si al menos un tap toca un
    riel. Un tap ``clavado`` también puntúa negativo: su media no es una medida
    analógica utilizable aunque casualmente caiga dentro de la ventana.
    """
    points = _normalizar_taps(taps)
    if any(point is None for point in points.values()):
        raise ValueError("faltan canales para calcular el puntaje")
    margins = []
    for point in points.values():
        margin = margen_riel_real_mv(point)
        stuck = (point.get("clavado", False) if isinstance(point, dict)
                 else point.clavado)
        margins.append(min(margin, -1e-9) if stuck else margin)
    return min(margins)


def _error_lpo_abs_real_mv(taps: "dict | Iterable[Point | dict]") -> float:
    point = _normalizar_taps(taps)[4]
    if point is None:
        raise ValueError("falta LPo para desempatar")
    if isinstance(point, dict):
        if "error_real_mv" in point:
            return abs(float(point["error_real_mv"]))
        mean = point["mean_bank_uv"]
    else:
        return abs(point.error_real_mv)
    return abs(real_mv(float(mean) - TARGET_UV))


def _calidad(taps: "dict | Iterable[Point | dict]") -> tuple[float, float]:
    """Orden total: más puntaje y, en empate, menor |error de LPo|."""
    return puntaje(taps), -_error_lpo_abs_real_mv(taps)


def evaluar_aceptacion(final: dict, codes: list[int] | None = None) -> dict:
    """El único fallo duro es que uno de los cinco taps toque riel."""
    points = _normalizar_taps(final)
    if any(point is None for point in points.values()):
        raise ValueError("faltan canales en la verificación final")
    margins = {ch: margen_riel_real_mv(point)
               for ch, point in points.items()}
    # BUG CORREGIDO el 2026-09-10: esto comparaba el margen contra CERO, o sea
    # que solo fallaba cuando el tap ya habia LLEGADO al riel. Con
    # MARGEN_RIEL_MV = 250 exigido, un tap a 100 mV del riel daba toca_riel=True
    # en el helper y aun asi la aceptacion devolvia PASS. El criterio que se
    # definio hoy no se estaba aplicando. Ahora usa el MISMO helper que el resto
    # de los puntos de decision, que era el proposito de haberlo centralizado.
    channels = []
    for ch, point in points.items():
        media = (point.get("mean_bank_uv") if isinstance(point, dict)
                 else point.mean_bank_uv)
        pp = (point.get("pp_bank_uv") if isinstance(point, dict)
              else point.pp_bank_uv)
        if toca_riel(media, pp, MARGEN_RIEL_MV):
            channels.append(ch)
    failures = []
    if channels:
        failed_margins = {ch: margins[ch] for ch in channels}
        detail = ", ".join(f"ch{ch} {TAP_NAMES[ch]}={failed_margins[ch]:.1f} mV"
                           for ch in channels)
        failures.append({"condicion": "toca_riel", "canales": channels,
                         "margenes_reales_mv": failed_margins,
                         "detalle": "toca riel: " + detail})
    no_authority = [] if codes is None else [
        stage for stage, code in enumerate(codes) if abs(int(code)) >= IDAC_MAX]
    score = puntaje(points)
    return {"pass": not failures, "fallas": failures, "puntaje": score,
            "criterio": {"unico_fallo_duro": "ningun_tap_toca_riel",
                         "puntaje_real_mv": score,
                         "excursion": "media +/- pp/2",
                         "error_lpo_real_mv": real_mv(
                             (points[4]["mean_bank_uv"]
                              if isinstance(points[4], dict)
                              else points[4].mean_bank_uv) - TARGET_UV),
                         "lpo_solo_se_informa": True,
                         "idac_sin_autoridad_informativo": no_authority}}


def resumen_aceptacion(acceptance: dict) -> str:
    return "; ".join(item["detalle"] for item in acceptance["fallas"])


def correccion_discreta(error: float, slope: float, *, kp: float = 1.0
                        ) -> tuple[int, bool, float]:
    """Corrección entera; para antes de redondear si pide menos de 0,5 LSB."""
    if not math.isfinite(slope) or abs(slope) < 1.0:
        raise ValueError("pendiente no utilizable")
    raw = -error / slope
    if abs(raw) < 0.5:
        return 0, True, raw
    effort = kp * raw
    magnitude = max(1, int(round(abs(effort))))
    return (magnitude if effort > 0 else -magnitude), False, raw


@dataclass
class Point:
    elapsed_s: float
    phase: str
    stage: int
    code: int
    channel: int
    mean_bank_uv: int
    error_bank_uv: int
    error_real_mv: float
    pp_bank_uv: int
    valid: bool
    clavado: bool


def _margen_predicho_mv(point: Point, delta_real_mv: float) -> float:
    predicted = Point(
        point.elapsed_s, point.phase, point.stage, point.code, point.channel,
        int(round(point.mean_bank_uv + bank_uv(delta_real_mv))),
        int(round(point.error_bank_uv + bank_uv(delta_real_mv))),
        point.error_real_mv + delta_real_mv, point.pp_bank_uv,
        point.valid, point.clavado)
    return margen_riel_real_mv(predicted)


def _mejor_margen_actuador(point: Point, current_code: int,
                           slope_real_mv: float) -> dict:
    choices = [
        (_margen_predicho_mv(point, (code-current_code)*slope_real_mv), code)
        for code in range(IDAC_MIN, IDAC_MAX+1)]
    margin, code = max(choices)
    return {"code": code, "margen_real_mv": margin}


def modelo_pendientes_fase1(
        paso_idac0_ch0_mv: float = PASO_IDAC0_CH0_MV,
        paso_idac1_ch1_mv: float = PASO_IDAC1_CH1_MV,
        *, fuente_idac0: str = "respaldo",
        fuente_idac1: str = "respaldo") -> dict:
    """Construye el modelo de fase 1 y deriva ch2 por el sumador inversor."""
    return {
        "ch0_idac0": float(paso_idac0_ch0_mv),
        "ch1_idac1": float(paso_idac1_ch1_mv),
        "ch2_idac0": -GANANCIA_SUM_DESDE_SEO * float(paso_idac0_ch0_mv),
        "ch2_idac1": -GANANCIA_SUM_DESDE_BPO * float(paso_idac1_ch1_mv),
        "fuentes": {"ch0_idac0": fuente_idac0,
                    "ch1_idac1": fuente_idac1,
                    "ch2_idac0": "derivada: -27k/6,8k * ch0_idac0",
                    "ch2_idac1": "derivada: -27k/7,48k * ch1_idac1"},
    }


def modelo_respaldo_fase1() -> dict:
    """Modelo usado sólo cuando no existe una medición directa utilizable."""
    modelo = modelo_pendientes_fase1(
        PASO_IDAC0_CH0_MV, PASO_IDAC1_CH1_MV,
        fuente_idac0="respaldo", fuente_idac1="respaldo")
    modelo["ch2_idac0"] = PASO_IDAC0_CH2_MV
    modelo["ch2_idac1"] = PASO_IDAC1_CH2_MV
    modelo["fuentes"]["ch2_idac0"] = "respaldo"
    modelo["fuentes"]["ch2_idac1"] = "respaldo"
    return modelo


def pendiente_directa_utilizable(slope_real_mv: float | None) -> bool:
    """Una pendiente directa debe ser finita, positiva y superar 1 uV banco/LSB."""
    return bool(slope_real_mv is not None and math.isfinite(slope_real_mv) and
                slope_real_mv > 0.0 and abs(bank_uv(slope_real_mv)) >= 1.0)


def recalcular_modelo_fase1(
        modelo_anterior: dict, antes: dict[int, Point], despues: dict[int, Point],
        codigos_antes: tuple[int, int] | list[int],
        codigos_despues: tuple[int, int] | list[int],
        *, intento: int) -> tuple[dict, dict]:
    """Reestima pasos directos con el salto aplicado y vuelve a derivar ch2."""
    directas = {"ch0_idac0": float(modelo_anterior["ch0_idac0"]),
                "ch1_idac1": float(modelo_anterior["ch1_idac1"])}
    detalle: dict = {"intento": intento, "actualizadas": [], "taps": {}}
    for stage, channel, key in ((0, 0, "ch0_idac0"),
                                (1, 1, "ch1_idac1")):
        delta_code = int(codigos_despues[stage])-int(codigos_antes[stage])
        delta_mv = (despues[channel].error_real_mv -
                    antes[channel].error_real_mv)
        realizada = delta_mv/delta_code if delta_code else None
        utilizable = bool(
            pendiente_directa_utilizable(realizada) and
            antes[channel].valid and not antes[channel].clavado and
            despues[channel].valid and not despues[channel].clavado)
        detalle["taps"][f"ch{channel}"] = {
            "stage": stage, "delta_codigo": delta_code,
            "delta_observado_real_mv": delta_mv,
            "pendiente_realizada_mv_por_codigo": realizada,
            "utilizable": utilizable}
        if utilizable:
            directas[key] = float(realizada)
            detalle["actualizadas"].append(key)
    nuevo = dict(modelo_anterior)
    nuevo["fuentes"] = dict(modelo_anterior.get("fuentes", {}))
    for direct_key, sum_key, ratio in (
            ("ch0_idac0", "ch2_idac0", GANANCIA_SUM_DESDE_SEO),
            ("ch1_idac1", "ch2_idac1", GANANCIA_SUM_DESDE_BPO)):
        if direct_key in detalle["actualizadas"]:
            nuevo[direct_key] = directas[direct_key]
            nuevo[sum_key] = -ratio*directas[direct_key]
            nuevo["fuentes"][direct_key] = (
                f"salto realizado intento {intento}")
            nuevo["fuentes"][sum_key] = (
                f"derivada del salto realizado intento {intento}")
    detalle["modelo_anterior"] = dict(modelo_anterior)
    detalle["modelo_recalculado"] = dict(nuevo)
    return nuevo, detalle


def optimizar_fase1(taps: dict[int, Point],
                     codes: tuple[int, int] | list[int] = (0, 0),
                     modelo: dict | None = None) -> dict:
    """Resuelve la fase 1 discreta sin tocar hardware.

    El conjunto factible es el producto de los códigos que conservan al menos
    ``MARGEN_FASE1_MV`` en ch0 y ch1. Las pendientes directas llegan medidas y
    las de ch2 se derivan con las relaciones inversoras del sumador.

    Se enumera IDAC0 y, para cada valor, se despeja el IDAC1 continuo que anula
    ch2. Por convexidad sólo hay que probar sus dos enteros vecinos y los
    extremos factibles. El desempate maximiza el menor margen de ch0/ch1.
    """
    current0, current1 = int(codes[0]), int(codes[1])
    slopes = modelo_respaldo_fase1() if modelo is None else modelo
    slope00 = float(slopes["ch0_idac0"])
    slope11 = float(slopes["ch1_idac1"])
    slope20 = float(slopes["ch2_idac0"])
    slope21 = float(slopes["ch2_idac1"])
    required = (0, 1, 2)
    if any(ch not in taps for ch in required):
        raise ValueError("faltan ch0, ch1 o ch2 para optimizar fase 1")
    if any(taps[ch].clavado for ch in required):
        observed = {f"ch{ch}": (None if taps[ch].clavado else
                                 margen_riel_real_mv(taps[ch]))
                    for ch in (0, 1)}
        return {"feasible": False, "reason": "sin punto factible",
                "detail": "tap clavado; requiere rescate antes del modelo",
                "codes": None, "margen_requerido_real_mv": MARGEN_FASE1_MV,
                "margenes_observados_real_mv": observed}

    feasible0 = [code for code in range(IDAC_MIN, IDAC_MAX+1)
                 if _margen_predicho_mv(
                     taps[0], (code-current0)*slope00
                 ) >= MARGEN_FASE1_MV]
    feasible1 = [code for code in range(IDAC_MIN, IDAC_MAX+1)
                 if _margen_predicho_mv(
                     taps[1], (code-current1)*slope11
                 ) >= MARGEN_FASE1_MV]
    attainable = {
        "ch0": _mejor_margen_actuador(taps[0], current0,
                                      slope00),
        "ch1": _mejor_margen_actuador(taps[1], current1,
                                      slope11),
    }
    intervals = {
        "idac0": ([min(feasible0), max(feasible0)] if feasible0 else None),
        "idac1": ([min(feasible1), max(feasible1)] if feasible1 else None),
    }
    if not feasible0 or not feasible1:
        return {"feasible": False, "reason": "sin punto factible",
                "codes": None, "intervalos_factibles": intervals,
                "margen_requerido_real_mv": MARGEN_FASE1_MV,
                "mejores_margenes_alcanzables": attainable}

    best: tuple[tuple[float, float, int, int], dict] | None = None
    low1, high1 = feasible1[0], feasible1[-1]
    for code0 in feasible0:
        coarse_error = (taps[2].error_real_mv +
                        (code0-current0)*slope20)
        # Con IDAC2 participando del reparto, el mejor IDAC1 ya NO es el que
        # anula ch2: es el que equilibra margenes y reservas, y puede caer lejos
        # de ese punto. Se enumera el intervalo factible completo. Como ch0 tiene
        # paso grueso su intervalo es de pocas decenas de codigos, asi que el
        # producto queda en el orden de 10^4 evaluaciones aritmeticas.
        candidates1 = range(low1, high1 + 1)
        for code1 in candidates1:
            predicted_error = (coarse_error +
                               (code1-current1)*slope21)
            margin0 = _margen_predicho_mv(
                taps[0], (code0-current0)*slope00)
            margin1 = _margen_predicho_mv(
                taps[1], (code1-current1)*slope11)
            min_margin = min(margin0, margin1)
            # DESEMPATE POR REPARTO, y no por margen a secas.
            #
            # Minimizar |error| y despues maximizar margen elige, sobre el reposo
            # medido, [IDAC0,IDAC1,IDAC2] = [+5,-238,-201]: deja ch2 en cero pero
            # gasta IDAC1 al 93 % y IDAC2 al 79 % en el ARRANQUE. Ese nodo empieza
            # la jornada sin autoridad para seguir su propia deriva termica.
            #
            # Lo correcto es tratar margenes y rangos de actuador como un mismo
            # recurso y maximizar el que quede mas gastado. Sobre el mismo reposo
            # eso da [-3,-106,-106], con ch2 igual de centrado y ningun recurso
            # por debajo del 58 %. El error deja de minimizarse y pasa a ser una
            # condicion: alcanza con entrar en medio LSB de IDAC2, porque mas
            # precision que esa no la puede sostener el actuador que la corrige.
            code2 = round(-predicted_error / PASO_IDAC2_CH2_MV)
            # La condicion es que IDAC2 ALCANCE el residuo, no que el residuo
            # sea nulo. Exigir medio LSB obliga a IDAC0/IDAC1 a hacer todo el
            # trabajo y deja IDAC2 sin usar, que es justo lo contrario del
            # reparto: sobre el reposo medido daba [-2,-195] con IDAC2 en cero y
            # un recurso de 0,235, peor que el [-3,-106,-106] que reparte entre
            # los tres y deja 0,58. El costo de usar IDAC2 ya lo cobra el termino
            # de reserva de abajo.
            dentro = abs(code2) <= IDAC_MAX
            recurso = min(margin0 / MARGEN_MAXIMO_MV,
                          margin1 / MARGEN_MAXIMO_MV,
                          (IDAC_MAX - abs(code0)) / float(IDAC_MAX),
                          (IDAC_MAX - abs(code1)) / float(IDAC_MAX),
                          (IDAC_MAX - min(abs(code2), IDAC_MAX)) / float(IDAC_MAX))
            key = (not dentro, -recurso, abs(predicted_error),
                   abs(code0-current0)+abs(code1-current1), abs(code0))
            candidate = {"codes": [code0, code1],
                         "ch2_error_predicho_real_mv": predicted_error,
                         "idac2_previsto": code2,
                         "recurso_mas_gastado": recurso,
                         "margenes_predichos_real_mv": {
                             "ch0": margin0, "ch1": margin1,
                             "minimo": min_margin}}
            if best is None or key < best[0]:
                best = key, candidate
    assert best is not None

    # BANDA MUERTA SOBRE LOS ACTUADORES DE FASE 1.
    #
    # Mover IDAC0 obliga a esperar entre 2 y 5 tau del pasabanda. Sin banda
    # muerta el optimizador acepta cualquier mejora, por chica que sea: en el
    # caso de ch0 desplazado 1 V con ch2 ya centrado
    # elige moverse un codigo para ganar 2,2 puntos de recurso, y paga un tau por
    # eso. El reparto que SI vale la pena, el que lleva la reserva de 7 % a 58 %,
    # son 51 puntos. Cualquier umbral entre medio separa los dos casos.
    quieto = None
    if current0 in feasible0 and current1 in feasible1:
        error_quieto = taps[2].error_real_mv
        code2_quieto = round(-error_quieto / PASO_IDAC2_CH2_MV)
        if (abs(error_quieto) <= PASO_IDAC2_CH2_MV / 2.0
                and abs(code2_quieto) <= IDAC_MAX):
            m0 = _margen_predicho_mv(taps[0], 0.0)
            m1 = _margen_predicho_mv(taps[1], 0.0)
            quieto = {
                "codes": [current0, current1],
                "ch2_error_predicho_real_mv": error_quieto,
                "idac2_previsto": code2_quieto,
                "recurso_mas_gastado": min(
                    m0 / MARGEN_MAXIMO_MV, m1 / MARGEN_MAXIMO_MV,
                    (IDAC_MAX - abs(current0)) / float(IDAC_MAX),
                    (IDAC_MAX - abs(current1)) / float(IDAC_MAX),
                    (IDAC_MAX - min(abs(code2_quieto), IDAC_MAX)) / float(IDAC_MAX)),
                "margenes_predichos_real_mv": {
                    "ch0": m0, "ch1": m1, "minimo": min(m0, m1)},
            }
    if quieto is not None and (best[1]["recurso_mas_gastado"]
                               - quieto["recurso_mas_gastado"]
                               < MEJORA_MINIMA_RECURSO):
        quieto["motivo"] = ("se conserva el punto actual: moverse no mejora el "
                            "recurso mas gastado lo suficiente como para pagar "
                            "una espera de tau")
        best = (best[0], quieto)

    return {"feasible": True, "reason": "optimo factible",
            **best[1], "intervalos_factibles": intervals,
            "margen_requerido_real_mv": MARGEN_FASE1_MV,
            "mejores_margenes_alcanzables": attainable,
            "modelo_real_mv_por_codigo": dict(slopes)}


def fit_first_order(points: list[tuple[float, float]]) -> tuple[float | None, dict]:
    """Ajusta y_inf+A*exp(-t/tau) y rechaza respuestas no observables."""
    if len(points) < 8:
        return None, {"reason": "pocas muestras"}
    t0 = points[0][0]
    ts, ys = [t - t0 for t, _ in points], [y for _, y in points]
    noise = statistics.pstdev(ys[-min(5, len(ys)):])
    best = None
    for tenth in range(20, 1201):
        tau = tenth / 10.0
        xs = [math.exp(-t / tau) for t in ts]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        if den <= 0:
            continue
        amplitude = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        final = my - amplitude * mx
        sse = sum((y - final - amplitude * x) ** 2 for x, y in zip(xs, ys))
        if best is None or sse < best[0]:
            best = sse, tau, amplitude, final
    if best is None:
        return None, {"reason": "ajuste imposible"}
    sse, tau, amplitude, final = best
    total = sum((y - statistics.fmean(ys)) ** 2 for y in ys)
    r2 = 1.0 - sse / total if total > 0 else 0.0
    metrics = {"tau_s": tau, "amplitude_bank_uv": amplitude,
               "amplitude_real_mv": real_mv(amplitude), "final_bank_uv": final,
               "r2": r2, "noise_bank_uv": noise, "samples": len(points)}
    if abs(amplitude) < max(8.0 * max(noise, 1.0), bank_uv(2.0)):
        metrics["reason"] = "excursion insuficiente"
        return None, metrics
    if r2 < 0.70:
        metrics["reason"] = "respuesta no compatible con primer orden"
        return None, metrics
    return tau, metrics


def load_history(path: Path = HISTORY_PATH) -> dict:
    for attempt in range(3):
        try:
            return (json.loads(path.read_text(encoding="utf-8"))
                    if path.is_file() else {"version": 2, "phase1": {}, "gains": {}})
        except (OSError, ValueError):
            if attempt < 2:
                time.sleep(0.1)
    return {"version": 2, "phase1": {}, "gains": {}}


def save_history(history: dict, path: Path = HISTORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    history.update(version=2, updated_at=datetime.now().isoformat(timespec="seconds"))
    last_error = None
    for attempt in range(5):
        try:
            path.write_text(json.dumps(history, indent=2), encoding="utf-8")
            return
        except OSError as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(0.1)
    raise last_error  # type: ignore[misc]


class TwoPhaseCalibrator:
    def __init__(self, lab: Lab, output: Path, *, samples: int = 3) -> None:
        self.lab, self.output, self.samples = lab, output, samples
        self.started = time.monotonic()
        self.codes = [0, 0, 0, 0]
        self.rows: list[Point] = []
        self.events: list[dict] = []
        self.deadline: float | None = None
        self.current_gain = 1
        #: Salto MEDIDO de ch2 al conmutar a cada ganancia, en mV fisicos. Se
        #: aprende en la puesta a punto del nodo y es propio de cada placa.
        self.saltos_ch2_por_ganancia: dict[int, float] = dict(SALTOS_CH2_MEDIDOS_MV)
        #: Paso de IDAC2 sobre ch2 medido en la corrida, para traducir el salto
        #: a codigos.
        self.paso_idac2_mv: float | None = None
        self.tau_bp_s: float | None = None

    def event(self, kind: str, message: str, **data) -> None:
        self.events.append({"at": datetime.now().isoformat(timespec="milliseconds"),
                            "kind": kind, "message": message, **data})
        print(message, flush=True)
        self.save()

    def save(self) -> None:
        """Evidencia incremental CSV+JSON con cinco reintentos."""
        self.output.parent.mkdir(parents=True, exist_ok=True)
        fields = tuple(Point.__dataclass_fields__)
        latest_score = next((event["result"]["puntaje"]
                             for event in reversed(self.events)
                             if isinstance(event.get("result"), dict) and
                             "puntaje" in event["result"]), None)
        payload = {"algorithm": "GEO margen + fase2 por ganancia",
                   "codes": list(self.codes), "current_gain": self.current_gain,
                   "puntaje": latest_score,
                   "events": self.events,
                   "measurements": [asdict(row) for row in self.rows]}
        last_error = None
        for attempt in range(5):
            try:
                with Path(str(self.output) + ".csv").open(
                        "w", newline="", encoding="utf-8-sig") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(asdict(row) for row in self.rows)
                self.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                return
            except OSError as exc:
                last_error = exc
                if attempt < 4:
                    time.sleep(0.1)
        raise last_error  # type: ignore[misc]

    def set_code(self, stage: int, code: int) -> None:
        if stage not in range(4) or not IDAC_MIN <= code <= IDAC_MAX:
            raise RuntimeError(f"IDAC fuera de rango: etapa={stage}, código={code}")
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise RuntimeError("presupuesto temporal agotado")
        for attempt in range(3):
            if self.lab.set_idac(stage, int(code)):
                self.codes[stage] = int(code)
                return
            self.event("write_retry", f"reintento IDAC{stage}={code}", attempt=attempt+1)
            time.sleep(0.25)
        raise RuntimeError(f"sin ACK IDAC{stage}={code}")

    def read(self, channel: int, phase: str, stage: int, code: int,
             *, require_valid: bool = True) -> Point:
        if channel not in range(5):
            raise ValueError("sólo ch0..ch4; ch5 está prohibido")
        values = []
        for _ in range(self.samples):
            measured = None
            for attempt in range(4):
                measured = self.lab.measure_dc(channel, 2)
                if measured is not None and measured.ok:
                    break
                if attempt < 3:
                    time.sleep(0.2)
            if measured is None or not measured.ok:
                raise RuntimeError(f"sin lectura ch{channel} tras cuatro intentos")
            values.append(measured)
        mean = int(statistics.median(item.mean_uv for item in values))
        pp = int(statistics.median(item.pp_uv for item in values))
        point = Point(time.monotonic()-self.started, phase, stage, code, channel,
                      mean, mean-TARGET_UV, real_mv(mean-TARGET_UV), pp,
                      VALID_MIN_UV <= mean <= VALID_MAX_UV, clavado(pp))
        self.rows.append(point)
        self.save()
        if require_valid and not point.valid:
            raise RuntimeError(f"ch{channel} {TAP_NAMES[channel]} fuera de ventana")
        return point

    def configure_neutral(self) -> None:
        if not self.lab.set_adc_config(1):
            raise RuntimeError("sin ACK ADC +/-2,5 V")
        if not self.lab.set_gain("pga", PGA_X50_CODE):
            raise RuntimeError("sin ACK PGA x50")
        if not self.lab.set_gain("pgaout", PGAOUT_X1_CODE):
            raise RuntimeError("sin ACK PGAout x1")
        self.current_gain = 1
        for stage in range(4):
            self.set_code(stage, 0)

    def read_five_taps(self, phase: str, *, robust: bool = False) -> dict[int, Point]:
        old = self.samples
        self.samples = max(3, old) if robust else old
        try:
            return {ch: self.read(ch, phase, min(ch, 3),
                                  self.codes[min(ch, 3)], require_valid=False)
                    for ch in range(5)}
        finally:
            self.samples = old

    def wait_channel_stable(self, channel: int, *, stage: int, code: int,
                            timeout_s: float = 180.0, sample_period_s: float = 3.0,
                            required: int = 3) -> Point:
        """Estabilidad observada de la media, no una demora fija."""
        previous = self.read(channel, "espera estabilidad", stage, code,
                             require_valid=False)
        consecutive, started = 0, time.monotonic()
        while time.monotonic() - started < timeout_s:
            time.sleep(sample_period_s)
            current = self.read(channel, "espera estabilidad", stage, code,
                                require_valid=False)
            resolution = max(previous.pp_bank_uv, current.pp_bank_uv,
                             int(bank_uv(1.0))) / 2.0
            consecutive = consecutive + 1 if abs(
                current.mean_bank_uv-previous.mean_bank_uv) <= resolution else 0
            if consecutive >= required:
                self.event("stable", f"ch{channel} estable por observación",
                           elapsed_s=time.monotonic()-started)
                return current
            previous = current
        raise RuntimeError(f"ch{channel} no se estabilizó en {timeout_s:.0f} s")

    def _wait_bp(self, tau: float, stage: int) -> Point:
        time.sleep(BP_ESPERA_TAU_MIN * tau)
        return self.wait_channel_stable(
            1, stage=stage, code=self.codes[stage],
            timeout_s=(BP_ESPERA_TAU_MAX-BP_ESPERA_TAU_MIN)*tau,
            sample_period_s=max(2.0, min(5.0, 0.15*tau)), required=2)

    def medir_tau_bp(self, stage: int,
                     guard_point: Point | None = None) -> tuple[float, dict]:
        """Excita un IDAC lento, mide el tau, restaura y espera ese tau medido."""
        origin = self.codes[stage]
        # Un codigo de IDAC0 mueve SEo 49,7 mV, y el pasabanda le devuelve
        # -R5/R4 = -1,09 de eso a BPo como excursion inicial, o sea unos 54 mV
        # fisicos = 2,7 mV de banco. El ruido de ch1 es de ese mismo orden, asi
        # que con un codigo el ajuste no puede distinguir respuesta de ruido y
        # rechaza por "excursion insuficiente". Con cuatro codigos la excursion
        # inicial son ~216 mV fisicos, holgadamente por encima del ruido, y
        # sigue siendo reversible.
        magnitude = 4 if stage == 0 else 8
        known_slope = PASO_IDAC0_CH0_MV if stage == 0 else PASO_IDAC1_CH1_MV
        # Si el tap ya está desplazado, excitar hacia Vref y nunca más adentro
        # del riel. El signo medido de ambos pasos es positivo.
        delta = (-magnitude if guard_point is not None and
                 guard_point.error_real_mv * known_slope > 0 else magnitude)
        if origin + delta > IDAC_MAX:
            delta = -delta
        if origin + delta < IDAC_MIN:
            delta = abs(delta)
        series = []
        try:
            self.set_code(stage, origin+delta)
            started = time.monotonic()
            while time.monotonic()-started <= 3.2*TAU_BP_REFERENCIA_S:
                p = self.read(1, "medición tau BP", stage, origin+delta,
                              require_valid=False)
                series.append((time.monotonic()-started, float(p.mean_bank_uv)))
                time.sleep(3.0)
        finally:
            self.set_code(stage, origin)
        tau, metrics = fit_first_order(series)
        if tau is None:
            # Un diagnostico que no cierra NO puede tumbar la campana cuando hay
            # un valor de diseno conocido: tau = R4*C1 = 43k * 680uF = 29,2 s, y
            # lo medido en las corridas de septiembre cayo entre 24,9 y 42,3 s.
            # Se usa la referencia, se deja constancia, y la campana sigue. La
            # espera resultante es conservadora, que es el lado seguro del error.
            metrics = dict(metrics or {})
            metrics["fallback"] = True
            metrics["tau_s"] = TAU_BP_REFERENCIA_S
            self.event("tau_fallback",
                       f"Tau BP no medible ({metrics.get('reason')}); "
                       f"se usa la referencia de diseno {TAU_BP_REFERENCIA_S:.1f} s",
                       metrics=metrics)
            tau = TAU_BP_REFERENCIA_S
        self.tau_bp_s = tau
        self.event("tau", f"Tau BP medido: {tau:.1f} s", metrics=metrics)
        self._wait_bp(tau, stage)
        return tau, metrics

    def pendiente_abba(self, stage: int, channel: int,
                       deltas: Iterable[int] = (8, 4),
                       settle_s: float = ABBA_ESPERA_S) -> dict:
        """Pendiente A-B-B-A: los promedios cancelan una deriva lineal."""
        origin, estimates, records = self.codes[stage], [], []
        requested = tuple(deltas)
        precheck = self.read(channel, f"ABBA precheck IDAC{stage}", stage,
                             origin, require_valid=False)
        if precheck.clavado:
            result = {"stage": stage, "channel": channel, "origin": origin,
                      "deltas": list(requested), "method": "ABBA",
                      "slope_bank_uv_per_code": None,
                      "slope_real_mv_per_code": None, "records": [],
                      "reason": "tap clavado; ABBA prohibido",
                      "precheck": asdict(precheck)}
            self.event("abba_blocked_stuck",
                       f"IDAC{stage}->ch{channel}: tap clavado; ABBA no intentado",
                       result=result)
            return result
        try:
            for requested_delta in requested:
                delta = requested_delta
                if (stage in (0, 1) and channel == stage and
                        precheck.error_real_mv > 0.0):
                    # En los taps directos el paso esperado es positivo: si el
                    # punto ya está alto, prueba el mismo delta hacia Vref.
                    delta = -abs(delta)
                if origin + delta > IDAC_MAX:
                    delta = -delta
                other = origin + delta
                if other < IDAC_MIN:
                    continue
                a_points, b_points = [], []
                for label, code in (("A1", origin), ("B1", other),
                                    ("B2", other), ("A2", origin)):
                    changed = self.codes[stage] != code
                    self.set_code(stage, code)
                    # Los taps directos de fase 1 son rápidos: cada transición
                    # espera ABBA_ESPERA_S, nunca el tau. La segunda B se puede
                    # leer seguida porque el código no cambió.
                    if changed or stage not in (0, 1):
                        time.sleep(settle_s)
                    p = self.read(channel, f"ABBA {label} IDAC{stage}", stage,
                                  code, require_valid=False)
                    (a_points if label.startswith("A") else b_points).append(p)
                    records.append({"delta": delta, "label": label,
                                    "point": asdict(p)})
                if all(p.valid and not p.clavado for p in a_points+b_points):
                    a = statistics.fmean(p.mean_bank_uv for p in a_points)
                    b = statistics.fmean(p.mean_bank_uv for p in b_points)
                    estimates.append((b-a)/delta)
        finally:
            changed = self.codes[stage] != origin
            self.set_code(stage, origin)
            if changed:
                time.sleep(settle_s)
        slope = statistics.median(estimates) if estimates else None
        result = {"stage": stage, "channel": channel, "origin": origin,
                  "deltas": list(requested), "method": "ABBA",
                  "slope_bank_uv_per_code": slope,
                  "slope_real_mv_per_code": (None if slope is None
                                              else real_mv(slope)),
                  "records": records}
        self.event("abba", f"IDAC{stage}->ch{channel}: " +
                   ("ABBA no utilizable" if slope is None else
                    f"ABBA {real_mv(slope):+.3f} mV/código"), result=result)
        return result

    def medir_pendientes_fase1(self) -> dict:
        """Mide los pasos rápidos directos y deriva los del sumador."""
        abba0 = self.pendiente_abba(0, 0, (2, 4), ABBA_ESPERA_S)
        abba1 = self.pendiente_abba(1, 1, (2, 4), ABBA_ESPERA_S)
        medidas = (abba0.get("slope_real_mv_per_code"),
                   abba1.get("slope_real_mv_per_code"))
        respaldos = (PASO_IDAC0_CH0_MV, PASO_IDAC1_CH1_MV)
        respaldos_ch2 = (PASO_IDAC0_CH2_MV, PASO_IDAC1_CH2_MV)
        valores: list[float] = []
        fuentes: list[str] = []
        for stage, channel, medida, respaldo, respaldo_ch2 in zip(
                (0, 1), (0, 1), medidas, respaldos, respaldos_ch2):
            if pendiente_directa_utilizable(medida):
                valores.append(float(medida))
                fuentes.append("ABBA fase 1")
            else:
                valores.append(respaldo)
                fuentes.append("respaldo")
                self.event(
                    "phase1_slope_fallback",
                    f"IDAC{stage}->ch{channel}: pendiente ABBA no utilizable; "
                    f"se usa respaldo directo {respaldo:+.3f} y de sumador "
                    f"{respaldo_ch2:+.3f} mV/código",
                    stage=stage, channel=channel, measured=medida,
                    fallback_real_mv_per_code=respaldo,
                    fallback_ch2_real_mv_per_code=respaldo_ch2)
        modelo = modelo_pendientes_fase1(
            valores[0], valores[1], fuente_idac0=fuentes[0],
            fuente_idac1=fuentes[1])
        for stage, fuente, respaldo_ch2 in zip(
                (0, 1), fuentes, respaldos_ch2):
            if fuente == "respaldo":
                modelo[f"ch2_idac{stage}"] = respaldo_ch2
                modelo["fuentes"][f"ch2_idac{stage}"] = "respaldo"
        result = {"abba_idac0_ch0": abba0, "abba_idac1_ch1": abba1,
                  "modelo_real_mv_por_codigo": modelo,
                  "uso_respaldo": any(f == "respaldo" for f in fuentes)}
        self.event(
            "phase1_slopes",
            "Pendientes fase 1: "
            f"ch0/IDAC0={modelo['ch0_idac0']:+.3f}, "
            f"ch1/IDAC1={modelo['ch1_idac1']:+.3f}, "
            f"ch2/IDAC0={modelo['ch2_idac0']:+.3f}, "
            f"ch2/IDAC1={modelo['ch2_idac1']:+.3f} mV/código",
            result=result)
        return result

    def rescatar_ch2_desde_riel(self, point: Point | None = None,
                               slope_ch2_idac0: float = PASO_IDAC0_CH2_MV,
                               *, max_iterations: int = 4) -> dict:
        """Saca ch2 de un riel con IDAC0 y la pendiente disponible de fase 1.

        Mientras ch2 está clavado no se estima ninguna pendiente. Cada salto
        supone que el valor real está justo en el riel observado; por eso es
        una cota inferior de la corrección necesaria. Si sigue del mismo lado,
        se puede repetir hasta cuatro veces. Mientras siga clavado no se usa la
        magnitud de su lectura: sólo el lado del riel; aparecer en el opuesto
        aborta el rescate.
        """
        current = point or self.read(2, "rescate ch2 inicio", 0, self.codes[0],
                                     require_valid=False)
        attempts = []
        if not current.clavado:
            return {"converged": True, "reason": "ch2 no estaba clavado",
                    "iterations": 0, "attempts": attempts,
                    "final": asdict(current)}
        low_error = real_mv(BANCO_MIN_VALIDO_MV*1000.0 - TARGET_UV)
        high_error = real_mv(
            min(BANCO_MAX_VALIDO_MV, BANCO_RIEL_ALTO_MEDIDO_MV)*1000.0 -
            TARGET_UV)
        reason = "max_iterations"
        for iteration in range(max_iterations):
            before = self.codes[0]
            rail_error = high_error if current.error_real_mv >= 0 else low_error
            raw = -rail_error/slope_ch2_idac0
            delta = (math.ceil(abs(raw)) if raw > 0 else
                     -math.ceil(abs(raw)))
            candidate = max(IDAC_MIN, min(IDAC_MAX, before+delta))
            if candidate == before:
                reason = "IDAC0 sin autoridad para rescatar"
                break
            previous = current
            self.set_code(0, candidate)
            self._wait_bp(self.tau_bp_s or TAU_BP_REFERENCIA_S, 0)
            current = self.read(2, "rescate ch2 verifica", 0, candidate,
                                require_valid=False)
            previous_side = 1 if previous.error_real_mv >= 0 else -1
            current_side = 1 if current.error_real_mv >= 0 else -1
            commanded_toward = delta*slope_ch2_idac0*previous_side < 0
            if current.clavado:
                approached = commanded_toward and current_side == previous_side
                evidence = ("mismo lado y salto modelado hacia el centro"
                            if approached else "apareció en el riel opuesto")
            else:
                # Fuera del riel la media vuelve a tener significado analógico.
                approached = abs(current.error_real_mv) < abs(rail_error)
                evidence = "salió del riel" if approached else "salió alejándose"
            attempts.append({"iteration": iteration+1, "from": before,
                             "to": candidate, "delta": delta,
                             "coefficient_real_mv_per_code":
                                 slope_ch2_idac0,
                             "assumed_rail_error_real_mv": rail_error,
                             "approached": approached,
                             "approach_evidence": evidence,
                             "point": asdict(current)})
            if not approached:
                reason = "el rescate no acerca ch2"
                break
            if not current.clavado:
                reason = "ch2 salió del riel"
                result = {"converged": True, "reason": reason,
                          "iterations": len(attempts), "attempts": attempts,
                          "final": asdict(current)}
                self.event("rail_rescue_pass",
                           f"ch2 salió del riel en {len(attempts)} intento(s)",
                           result=result)
                return result
        result = {"converged": False, "reason": reason,
                  "iterations": len(attempts), "attempts": attempts,
                  "final": asdict(current)}
        self.event("rail_rescue_abort", f"Rescate ch2 abortado: {reason}",
                   result=result)
        return result

    def fase1_margen(self, history: dict | None = None) -> dict:
        """Mide y optimiza IDAC0/1 con margen duro en ch0/ch1."""
        self.configure_neutral()
        self.wait_channel_stable(1, stage=1, code=0)
        initial = self.read_five_taps("fase1 inicial", robust=True)
        status = {str(i): {"moved": False, "from": 0, "to": 0,
                           "reason": ("reservado para fase 2" if i >= 2 else "")}
                  for i in range(4)}
        tau = self.tau_bp_s
        tau_metrics = None
        rescue = None

        slope_measurement = self.medir_pendientes_fase1()
        modelo = slope_measurement["modelo_real_mv_por_codigo"]
        working = initial
        if initial[2].clavado:
            rescue = self.rescatar_ch2_desde_riel(
                initial[2], float(modelo["ch2_idac0"]))
            if self.codes[0] != 0:
                status["0"].update(
                    moved=True, to=self.codes[0],
                    reason="rescate de ch2 con pendiente derivada del ABBA")
            working = self.read_five_taps("fase1 post rescate", robust=True)

        optimizations, recalculations = [], []
        verified_points: dict[int, Point] | None = None
        optimization: dict = {"feasible": False,
                              "reason": "optimización no ejecutada"}
        for attempt in range(3):  # salto inicial + máximo dos reintentos
            optimization = optimizar_fase1(working, self.codes[:2], modelo)
            optimizations.append(optimization)
            if not optimization["feasible"]:
                self.event("phase1_no_feasible", "Fase 1: sin punto factible",
                           optimization=optimization, intento=attempt)
                break

            target0, target1 = optimization["codes"]
            before_codes = list(self.codes[:2])
            moved_stages = [stage for stage, target in
                            ((0, target0), (1, target1))
                            if target != self.codes[stage]]
            if 0 in moved_stages and tau is None:
                tau_stage = 0
                tau, tau_metrics = self.medir_tau_bp(
                    tau_stage, working[tau_stage])
            for stage, target in ((0, target0), (1, target1)):
                before = self.codes[stage]
                if target != before:
                    self.set_code(stage, target)
                    item = status[str(stage)]
                    item["moved"] = True
                    item["to"] = target
                    item["reason"] = ("óptimo restringido de fase 1" if attempt == 0
                                      else "reoptimización con salto realizado")
                    item.setdefault("jumps", []).append(
                        {"attempt": attempt+1, "from": before, "to": target})
            if 0 in moved_stages:
                self._wait_bp(tau or TAU_BP_REFERENCIA_S, 0)
            elif 1 in moved_stages:
                # Vref_BP desplaza BPo y el sumador al instante; no excita la
                # recuperación lenta del acople desde SEo.
                time.sleep(ABBA_ESPERA_S)

            verified_points = self.read_five_taps(
                f"fase1 verificación {attempt+1}", robust=True)
            margins_now = {ch: margen_riel_real_mv(verified_points[ch])
                           for ch in (0, 1)}
            idac2_needed = round(
                -verified_points[2].error_real_mv/PASO_IDAC2_CH2_MV)
            constraints_ok = bool(
                not verified_points[0].clavado and
                margins_now[0] >= MARGEN_FASE1_MV and
                not verified_points[1].clavado and
                margins_now[1] >= MARGEN_FASE1_MV and
                not verified_points[2].clavado and
                abs(idac2_needed) <= IDAC_MAX)
            if constraints_ok or attempt >= 2:
                break

            nuevo_modelo, recalculation = recalcular_modelo_fase1(
                modelo, working, verified_points, before_codes,
                self.codes[:2], intento=attempt+1)
            recalculations.append(recalculation)
            if not recalculation["actualizadas"]:
                self.event(
                    "phase1_realized_slopes_unusable",
                    "Fase 1: la verificación falló y el salto no permite "
                    "recalcular una pendiente utilizable",
                    recalculation=recalculation)
                break
            modelo = nuevo_modelo
            self.event(
                "phase1_slopes_recalculated",
                "Fase 1: restricciones no verificadas; se recalcula con el "
                f"salto realizado (reintento {attempt+1}/2)",
                recalculation=recalculation)
            working = verified_points

        if verified_points is None:
            verified_points = self.read_five_taps(
                "fase1 verificación sin óptimo", robust=True)
        verified = {ch: asdict(p) for ch, p in verified_points.items()}
        margins = {f"ch{ch}": margen_riel_real_mv(verified_points[ch])
                   for ch in (0, 1)}
        idac2_needed = round(
            -verified_points[2].error_real_mv/PASO_IDAC2_CH2_MV)
        criteria = {"ch0_margin": (not verified_points[0].clavado and
                                    margins["ch0"] >= MARGEN_FASE1_MV),
                    "ch1_margin": (not verified_points[1].clavado and
                                    margins["ch1"] >= MARGEN_FASE1_MV),
                    "ch2_not_stuck": not verified_points[2].clavado,
                    "ch2_idac2_reachable": abs(idac2_needed) <= IDAC_MAX,
                    "idac2_needed": idac2_needed,
                    "margen_requerido_real_mv": MARGEN_FASE1_MV,
                    "margenes_verificados_real_mv": margins,
                    "idac2_starts_near_zero": self.codes[2] == 0,
                    "history_replaces_measurement": False}
        passed = bool(optimization["feasible"] and
                      all((criteria["ch0_margin"], criteria["ch1_margin"],
                           criteria["ch2_not_stuck"],
                           criteria["ch2_idac2_reachable"])))
        score = puntaje(verified_points)
        result = {"pass": passed, "codes": list(self.codes), "idacs": status,
                  "puntaje": score,
                  "initial": {ch: asdict(p) for ch, p in initial.items()},
                  "verified": verified, "tau_s": tau,
                  "tau_metrics": tau_metrics,
                  "rail_rescue": rescue, "optimization": optimization,
                  "optimizations": optimizations,
                  "slope_measurement": slope_measurement,
                  "modelo_final_real_mv_por_codigo": modelo,
                  "recalculos_desde_salto": recalculations,
                  "criteria": criteria,
                  "reason": ("ok" if passed else
                             (optimization["reason"] if not optimization["feasible"]
                              else "el óptimo predicho no verificó restricciones"))}
        self.event("phase1_pass" if passed else "phase1_fail",
                   f"Fase 1 {'PASS' if passed else 'FAIL'}: "
                   f"IDAC0={self.codes[0]} IDAC1={self.codes[1]}", result=result)
        return result

    def _wait_fast(self, channel: int, stage: int) -> Point:
        try:
            return self.wait_channel_stable(
                channel, stage=stage, code=self.codes[stage],
                timeout_s=OTRAS_ETAPAS_ESPERA_S, sample_period_s=1.0, required=3)
        except RuntimeError:
            self.event("fast_settle_timeout",
                       f"ch{channel}: agotó {OTRAS_ETAPAS_ESPERA_S:.0f} s; se verifica")
            return self.read(channel, "fin espera rápida", stage,
                             self.codes[stage], require_valid=False)

    def ajustar_actuador(self, stage: int, channel: int, slope: float,
                         objetivo_abs_real_mv: float = 0.0,
                         max_iterations: int = 64,
                         consigna_real_mv: float = 0.0) -> dict:
        """Newton acotado + PI pequeño; el medio paso es parada, no objetivo.

        ``consigna_real_mv`` es el valor al que hay que llevar el tap, que NO
        siempre es Vref. Para ch2 la consigna es la que centra LPo, o sea
        ``-offset_LP/(9,07*G)``, porque la etapa LP tiene offset propio y
        cancelarlo desde ch2 sale mucho mas barato que desde IDAC3.
        """
        current = self.read(channel, f"IDAC{stage} inicio", stage,
                            self.codes[stage], require_valid=False)
        _consigna_uv0 = bank_uv(consigna_real_mv)
        best = (abs(current.error_bank_uv - _consigna_uv0),
                self.codes[stage], current)
        history, integral, stop = [], 0.0, "max_iterations"
        consigna_uv = bank_uv(consigna_real_mv)
        for iteration in range(max_iterations):
            target_abs = bank_uv(objetivo_abs_real_mv)
            desvio = float(current.error_bank_uv) - consigna_uv
            if abs(desvio) <= target_abs:
                stop = "objetivo"
                break
            controlled = desvio
            if target_abs:
                controlled -= math.copysign(target_abs, controlled)
            correction, resolution, raw = correccion_discreta(
                controlled, slope, kp=1.0 if iteration == 0 else 0.30)
            if resolution:
                stop = "resolucion_medio_paso"
                break
            integral = max(-16.0, min(16.0, integral+0.05*raw))
            if iteration:
                correction += int(round(0.05*integral))
                correction = correction or (1 if raw > 0 else -1)
            limit = 96 if iteration == 0 else 4
            correction = max(-limit, min(limit, correction))
            before = self.codes[stage]
            candidate = max(IDAC_MIN, min(IDAC_MAX, before+correction))
            if candidate == before:
                stop = "limite_actuador"
                break
            self.set_code(stage, candidate)
            time.sleep(ABBA_ESPERA_S)
            current = self.read(channel, f"IDAC{stage} lazo", stage, candidate,
                                require_valid=False)
            history.append({"iteration": iteration+1, "from": before,
                            "to": candidate, "raw_correction": raw,
                            "point": asdict(current)})
            # el "mejor" se mide contra la CONSIGNA, no contra Vref
            if abs(current.error_bank_uv - consigna_uv) < best[0]:
                best = (abs(current.error_bank_uv - consigna_uv),
                        candidate, current)
            if len(history) >= 3 and history[-1]["to"] == history[-3]["to"]:
                stop = "ciclo_discreto_resuelto"
                break
        if self.codes[stage] != best[1]:
            self.set_code(stage, best[1])
            time.sleep(ABBA_ESPERA_S)
            current = self.read(channel, f"IDAC{stage} mejor discreto", stage,
                                best[1], require_valid=False)
        else:
            current = best[2]
        result = {"stage": stage, "channel": channel, "code": self.codes[stage],
                  "slope_bank_uv_per_code": slope,
                  "objective_abs_real_mv": objetivo_abs_real_mv,
                  "stop_reason": stop, "iterations": len(history),
                  "final": asdict(current), "history": history}
        self.event("closed_loop", f"IDAC{stage}: {current.error_real_mv:+.1f} mV "
                   f"({stop})", result=result)
        return result

    @staticmethod
    def _prediction_sum(gain: int, ch2: Point) -> dict:
        mean, pp = TARGET_UV+gain*ch2.error_bank_uv, gain*ch2.pp_bank_uv
        return {"gain": gain, "mean_bank_uv": mean, "pp_bank_uv": pp,
                "error_real_mv": gain*ch2.error_real_mv,
                "toca_riel": toca_riel(mean, pp),
                "model": "SUMo-Vref=G*(ch2-Vref)"}

    def _fallback_guardado(self, remembered: dict | None,
                           degraded: dict[int, dict],
                           degraded_codes: list[int]) -> dict:
        """Restaura el último bueno sólo si ordena mejor que el degradado."""
        report = {"available": False, "used": False,
                  "degraded": {"codes": degraded_codes,
                               "puntaje": puntaje(degraded),
                               "lp_error_abs_real_mv":
                                   _error_lpo_abs_real_mv(degraded)}}
        if not remembered or len(remembered.get("codes", [])) != 4:
            report["reason"] = "sin punto guardado"
            return report
        saved = remembered.get("final")
        if not saved:
            report["reason"] = "historial antiguo sin taps ni puntaje"
            return report
        report["available"] = True
        report["saved"] = {"codes": remembered["codes"],
                           "puntaje": puntaje(saved),
                           "lp_error_abs_real_mv": _error_lpo_abs_real_mv(saved)}
        if _calidad(saved) <= _calidad(degraded):
            report["reason"] = "el guardado no es mejor que el degradado"
            return report

        for stage in (2, 3):
            self.set_code(stage, int(remembered["codes"][stage]))
            self._wait_fast(stage if stage == 2 else 4, stage)
        fallback_points = self.read_five_taps("fallback guardado", robust=True)
        fallback = {ch: asdict(point) for ch, point in fallback_points.items()}
        report["remeasured"] = {"codes": list(self.codes),
                                "puntaje": puntaje(fallback),
                                "lp_error_abs_real_mv":
                                    _error_lpo_abs_real_mv(fallback),
                                "final": fallback}
        if _calidad(fallback) > _calidad(degraded):
            report.update(used=True, reason="guardado revalidado es mejor",
                          selected="saved")
            return report

        for stage in (2, 3):
            self.set_code(stage, degraded_codes[stage])
            self._wait_fast(stage if stage == 2 else 4, stage)
        report.update(reason="guardado revalidado no mejora el degradado",
                      selected="degraded")
        return report

    def fase2_ganancia(self, gain: int, history: dict | None = None) -> dict:
        """Ajusta ch2, valida antes de conmutar y minimiza LPo para una G."""
        if gain not in GAIN_CODES:
            raise ValueError(f"ganancia no soportada: x{gain}")
        started, prior_gain, prior_codes = time.monotonic(), self.current_gain, list(self.codes)

        # SE CONMUTA LA GANANCIA PRIMERO, Y SE AJUSTA DESPUES.
        #
        # Medido el 2026-09-10 por la noche, y es lo que explica todos los
        # fracasos de esa sesion en ganancias altas: **ch2 se mueve cuando cambia
        # la ganancia de PGAout**, aunque OPA_SUMo este ANTES de esa etapa.
        #
        #     PGAout   x1     x2     x4      x8
        #     ch2    +16,9  -15,2  +212,5  +474,5  mV
        #
        # Ajustar ch2 en x1 y despues conmutar deja el punto invalidado por la
        # propia conmutacion, y arrastra LPo fuera del riel. Por eso x1 cerraba y
        # x4 no. Ahora se conmuta primero y todo el ajuste se hace en la ganancia
        # de destino, que es donde tiene que valer.
        #
        # Sobrevivir a la conmutacion con un punto malo es posible porque existen
        # los dos rescates desde riel, el de ch2 y el de LPo.
        if gain != self.current_gain:
            # COMPENSACION ANTICIPADA DEL SALTO DE GANANCIA.
            #
            # Medido sobre 475 ciclos el 2026-09-10: ch2 salta cientos de mV al
            # conmutar PGAout, pese a estar ANTES de esa etapa, y el salto es
            # REPETIBLE (desvio de 13 a 161 mV segun la ganancia). Al ser
            # repetible se puede anticipar: se mueve IDAC2 por adelantado para
            # que, despues de conmutar, ch2 caiga cerca de su consigna.
            #
            # Se hace con IDAC2 y no con IDAC0/IDAC1 porque IDAC2 es rapido: no
            # cuesta ningun tau. El salto tipico son 760 mV en x8, o sea unos
            # 138 codigos, que entran en el rango.
            # La tabla es relativa a x1, asi que el salto NETO al pasar de
            # la ganancia actual a la de destino es la diferencia.
            # COMPENSACION POR OFFSET: DESACTIVADA, quedo refutada.
            #
            # Medido el 2026-09-11: al subir la ganancia de PGAout la pendiente
            # de ch2 contra IDAC2 se DUPLICA (5,04 mV/codigo en x1, 10,89 en x2),
            # o sea que PGAout CARGA al sumador y la carga crece con la ganancia.
            # Cambia la planta, no solo el punto. Restar un offset fijo compensa
            # de mas de un lado y de menos del otro: al probarlo, ch2 termino a
            # -1979 mV cuando el objetivo era -29.
            #
            # Lo que si funciona es rescatar ch2 con IDAC2 y volver a medir la
            # pendiente en la ganancia de destino, que es lo de abajo.
            salto = None if True else self.saltos_ch2_por_ganancia.get(gain)
            desde = self.saltos_ch2_por_ganancia.get(self.current_gain, 0.0)
            if salto is not None:
                salto = salto - desde
            # En la PRIMERA ganancia todavia no se midio el paso de IDAC2,
            # porque su ABBA corre despues de conmutar. Sin respaldo, la
            # compensacion no actuaba justo cuando mas falta hace.
            paso2 = self.paso_idac2_mv or PASO_IDAC2_CH2_MV
            if salto is not None and paso2:
                correccion = int(round(-salto / paso2))
                destino = max(IDAC_MIN, min(IDAC_MAX, self.codes[2] + correccion))
                if destino != self.codes[2]:
                    antes = self.codes[2]
                    self.set_code(2, destino)
                    self._wait_fast(2, 2)
                    self.event("salto_compensado",
                               f"x{gain}: salto conocido {salto:+.0f} mV; "
                               f"IDAC2 {antes}->{destino} ANTES de conmutar",
                               gain=gain, salto_real_mv=salto,
                               desde_ganancia=self.current_gain,
                               codigo_previo=antes, codigo_nuevo=destino)
            if not self.lab.set_gain("pgaout", GAIN_CODES.index(gain)):
                raise RuntimeError(f"sin ACK PGAout x{gain}")
            self.current_gain = gain
            self._wait_fast(3, 2)
            self.event("ganancia_conmutada_primero",
                       f"x{gain}: PGAout conmutado ANTES de ajustar; ch2 se "
                       f"mueve con la ganancia y el punto hay que hacerlo aca",
                       gain=gain, ganancia_previa=prior_gain)

        # EL OBJETIVO DE ch2 NO ES CERO: ES EL VALOR QUE CENTRA LPo.
        #
        # La etapa LP tiene ~1050 mV de offset propio (corriente de polarizacion
        # por los 150 kOhm del MFB). Centrar ch2 y pedirle a IDAC3 que cancele
        # ese offset desperdicia el actuador GRUESO en un trabajo que el FINO
        # hace mejor: ch2 llega a LPo con ganancia -9,07, asi que un ch2 de
        # +116 mV pone LPo en cero sin gastar un solo codigo de IDAC3.
        #
        # Con esto IDAC3 deja de necesitar alcance para el offset y solo limpia
        # el residuo de cuantizacion de IDAC2. Es la diferencia entre necesitar
        # 1050 mV de autoridad y necesitar 24 mV en x1.
        #
        # El offset se MIDE aca, no se supone: se lee LPo y se le descuenta lo
        # que aporta el ch2 actual.
        ch2_ahora = self.read(2, f"x{gain} ch2 para objetivo", 2, self.codes[2],
                              require_valid=False)
        lpo_ahora = self.read(4, f"x{gain} LPo para objetivo", 3, self.codes[3],
                              require_valid=False)
        # UTILIZABLE = dentro de ventana Y con ruido propio. Solo mirar
        # `clavado` deja pasar lecturas FUERA DE RANGO: el 2026-09-10 una de
        # esas dio un offset de -11068 mV, imposible, y desvio la consigna de
        # ch2 en 300 mV. Fuera de rango el numero no significa nada aunque
        # tenga ruido.
        if (_usable(lpo_ahora) and _usable(ch2_ahora)):
            offset_lp = (lpo_ahora.error_real_mv -
                         ch2_ahora.error_real_mv * self.current_gain *
                         GANANCIA_LP_DESDE_CH2)
            fuente_offset = "medido"
        else:
            offset_lp = OFFSET_LP_RESPALDO_MV
            fuente_offset = "respaldo (LPo o ch2 clavado)"
        centro_ch2 = -offset_lp / (GANANCIA_LP_DESDE_CH2 * gain)
        objective = objetivo_ch2_mv(gain)
        self.event("objetivo_ch2",
                   f"x{gain}: offset LP {offset_lp:+.0f} mV ({fuente_offset}); "
                   f"ch2 debe apuntar a {centro_ch2:+.1f} mV, no a cero",
                   gain=gain, offset_lp_real_mv=offset_lp,
                   fuente_offset=fuente_offset,
                   centro_ch2_real_mv=centro_ch2,
                   tolerancia_real_mv=objective)
        self.centro_ch2_real_mv = centro_ch2
        # LA SEMILLA HISTORICA DE IDAC2/IDAC3 SE ELIMINA.
        #
        # Nunca ayudo y daño dos veces el 2026-09-10: primero llevo ch2 de -96 a
        # +1204 mV, y despues, ya con la ganancia conmutada, de su valor propio a
        # +1855. El historial guardado viene de la topologia y el criterio
        # viejos, y aca el punto se construye MIDIENDO en la ganancia de destino.
        #
        # El handoff permite que el historial INICIALICE, pero solo si mejora. Un
        # historial de otro regimen no mejora nada: lo unico que aporta es un
        # punto de partida peor que el que dejo la fase 1.
        remembered = None
        seed = None

        ch2_pre_abba = self.read(2, f"x{gain} pre-ABBA", 2, self.codes[2],
                                 require_valid=False)
        rail_rescue = None
        if ch2_pre_abba.clavado:
            rail_rescue = self.rescatar_ch2_desde_riel(ch2_pre_abba)
            if not rail_rescue["converged"]:
                result = {"gain": gain, "pass": False,
                          "reason": "ch2 clavado; rescate abortado",
                          "rail_rescue": rail_rescue, "codes": list(self.codes)}
                self.event("gain_rail_rescue_abort",
                           f"x{gain}: ch2 clavado y rescate abortado",
                           result=result)
                return result
        # RESCATE DE ch2 CON IDAC2, por biseccion, antes de identificar nada.
        #
        # Al conmutar la ganancia ch2 suele quedar clavado contra su riel, porque
        # PGAout lo carga. Sobre un tap clavado el ABBA no mide nada y cae al
        # respaldo, que en x4 esta cuatro veces errado porque la pendiente se
        # duplica con cada salto de ganancia. Con el respaldo malo el lazo se
        # pierde. Se lo rescata primero con IDAC2, que es rapido y tiene rango.
        ch2_previo = self.read(2, f"x{gain} ch2 antes del ABBA", 2,
                               self.codes[2], require_valid=False)
        if ch2_previo.clavado or not ch2_previo.valid:
            lo, hi, encontrado, visitados = IDAC_MIN, IDAC_MAX, None, []
            for _ in range(9):
                medio = (lo + hi) // 2
                self.set_code(2, medio)
                self._wait_fast(2, 2)
                p = self.read(2, f"x{gain} rescate ch2 I2={medio}", 2, medio,
                              require_valid=False)
                visitados.append({"codigo": medio, "ch2_real_mv": p.error_real_mv,
                                  "clavado": p.clavado, "valido": p.valid})
                if p.valid and not p.clavado:
                    encontrado = medio
                    break
                if p.mean_bank_uv > TARGET_UV:
                    hi = medio - 1      # esta alto: bajar IDAC2
                else:
                    lo = medio + 1
                if lo > hi:
                    break
            self.event("ch2_rescate_idac2" if encontrado is not None
                       else "ch2_rescate_idac2_fallido",
                       f"x{gain}: ch2 clavado tras conmutar; biseccion con IDAC2 "
                       + (f"lo saco en {encontrado}" if encontrado is not None
                          else f"no encontro punto vivo en {len(visitados)} pasos"),
                       gain=gain, visitados=visitados)

        abba2 = self.pendiente_abba(2, 2, (8, 4), ABBA_ESPERA_S)
        slope2, slope_source = abba2["slope_bank_uv_per_code"], "ABBA"
        if slope2 and abs(slope2) >= 1.0:
            self.paso_idac2_mv = real_mv(slope2)
        if slope2 is None or abs(slope2) < 1.0:
            slope2 = bank_uv(PASO_IDAC2_CH2_MV)
            slope_source = "constante medida para inicializar PI"
            self.event("abba_fallback", f"x{gain}: fallback IDAC2=5,1 mV/código")
        idac2 = self.ajustar_actuador(2, 2, float(slope2), float(objective),
                                      consigna_real_mv=centro_ch2)

        # SEGUNDA PASADA: remedir el offset ahora que LPo puede estar vivo.
        #
        # La primera consigna suele salir del RESPALDO, porque al entrar a fase 2
        # LPo viene clavado del arranque y no se puede medir. En la corrida del
        # 2026-09-10 ese respaldo valia -1050 mV y el offset real resulto -2389:
        # mas del doble. Con la consigna corta, LPo quedo en -1395 en vez de cerca
        # de cero. Una vez que IDAC2 movio el punto, LPo suele estar medible y
        # entonces el offset se puede medir de verdad y reapuntar.
        if fuente_offset != "medido":
            ch2_2 = self.read(2, f"x{gain} ch2 para reapuntar", 2,
                              self.codes[2], require_valid=False)
            lpo_2 = self.read(4, f"x{gain} LPo para reapuntar", 3,
                              self.codes[3], require_valid=False)
            if _usable(lpo_2) and _usable(ch2_2):
                offset_lp = (lpo_2.error_real_mv -
                             ch2_2.error_real_mv * self.current_gain *
                             GANANCIA_LP_DESDE_CH2)
                nuevo_centro = -offset_lp / (GANANCIA_LP_DESDE_CH2 * gain)
                self.event("objetivo_ch2_reapuntado",
                           f"x{gain}: offset LP remedido {offset_lp:+.0f} mV; "
                           f"ch2 pasa a apuntar a {nuevo_centro:+.1f} mV "
                           f"(antes {centro_ch2:+.1f})",
                           gain=gain, offset_lp_real_mv=offset_lp,
                           centro_previo_real_mv=centro_ch2,
                           centro_ch2_real_mv=nuevo_centro)
                if abs(nuevo_centro - centro_ch2) > objective:
                    centro_ch2 = nuevo_centro
                    self.centro_ch2_real_mv = centro_ch2
                    idac2 = self.ajustar_actuador(
                        2, 2, float(slope2), float(objective),
                        consigna_real_mv=centro_ch2)
        ch2 = self.read(2, f"x{gain} preconmutación", 2, self.codes[2],
                        require_valid=False)
        # La comprobacion "SUMo predicho tocaria riel" era PREVIA a la
        # conmutacion. Ahora la ganancia se conmuta al principio, asi que aca ya
        # no hay nada que prevenir: si SUMo quedo mal, lo arregla el ajuste de
        # IDAC2 en esta misma ganancia, y si quedo clavado lo saca el rescate.
        prediction = self._prediction_sum(gain, ch2)
        # PRECOMPENSACION DE IDAC3, ANTES DE CONMUTAR.
        #
        # Conmutar primero y buscar despues deja LPo muy cerca del riel en las
        # ganancias altas: con ch2 en su residuo tipico, a x50 LPo llegaria a
        # ~1,93 V de un rango util de 2,0, o sea al 96 %. Y un LPo rielado no se
        # puede medir, asi que el lazo que tiene que corregirlo queda ciego justo
        # cuando mas falta hace.
        #
        # Como ch2 ya esta MEDIDO y la ganancia de la etapa LP tambien, el
        # desplazamiento posterior es predecible: LPo ~ ch2 * G * GANANCIA_LP_CH2.
        # Se aplica esa correccion con IDAC3 antes de conmutar. La incertidumbre
        # que queda es la del propio ADC sobre ch2, decimas de mV, no el medio LSB
        # de IDAC2. IDAC3 es rapido: esto no cuesta ningun tau.
        # USA EL LPo MEDIDO, NO EL PREDICHO DESDE ch2.
        #
        # Predecirlo como ch2*G*(-9,07) supone que la etapa LP no tiene offset
        # propio. Medido el 2026-09-10: con ch2 en -7,4 mV y SUMo en -20, o sea
        # las dos etapas anteriores centradas, LPo estaba en -1111 mV. Ese offset
        # es de la etapa, no de ch2, y la prediccion daba el sentido contrario.
        #
        # Es un MFB: en continua los capacitores son abiertos, el nodo interno se
        # queda en LPm_ref y por eso IDAC3 llega a LPo amplificado por 37. El
        # offset viene de la corriente de polarizacion por los 150 k, o sea que
        # es fijo y se cancela una sola vez.
        lpo_actual = self.read(4, f"x{gain} LPo antes de precompensar", 3,
                               self.codes[3], require_valid=False)
        if _usable(lpo_actual):
            # SOLO ESCALA LA PARTE QUE VIENE DE ch2.
            #
            # LPo = ch2*G*(-9,07) + offset_LP. El offset es de la etapa y NO
            # depende de PGAout, asi que multiplicar LPo entero por G da
            # disparates: en la corrida del 2026-09-10 predijo -59062 mV al
            # pasar de x1 a x24, o sea catorce veces el riel.
            parte_ch2 = lpo_actual.error_real_mv - offset_lp
            factor = gain / float(max(1, self.current_gain))
            lpo_previsto = parte_ch2 * factor + offset_lp
            fuente_lpo = "medido, escalando solo la parte de ch2"
        else:
            lpo_previsto = ch2.error_real_mv * gain * GANANCIA_LP_CH2_MV_POR_MV
            fuente_lpo = "predicho desde ch2 porque LPo esta clavado"
        pre3 = int(round(-lpo_previsto / PASO_IDAC3_CH4_MV))
        pre3 = max(IDAC_MIN, min(IDAC_MAX, self.codes[3] + pre3))
        if pre3 != self.codes[3]:
            antes3 = self.codes[3]
            self.set_code(3, pre3)
            self.event("idac3_precompensado",
                       f"x{gain}: IDAC3 {antes3}->{pre3} antes de conmutar; "
                       f"LPo previsto {lpo_previsto:+.0f} mV",
                       gain=gain, ch2_real_mv=ch2.error_real_mv,
                       lpo_previsto_real_mv=lpo_previsto,
                       codigo_previo=antes3, codigo_nuevo=pre3)
        # la ganancia ya se conmuto al principio de la fase 2; aca no se repite
        self._wait_fast(3, 2)

        # RESCATE DE LPo DESDE EL RIEL, antes de intentar identificar nada.
        #
        # Es el mismo patron que ya existe para ch2 y por el mismo motivo: sobre
        # un tap clavado no hay pendiente que medir, y el ABBA devuelve basura o
        # nada. Aca hace falta porque la precompensacion puede dejar a IDAC3 en
        # un extremo y a LPo apoyado, que es lo que aborto x24 el 2026-09-10.
        #
        # Se sabe por medicion que IDAC3 recorre TODO el rango de LPo, asi que
        # una biseccion acotada siempre encuentra un punto vivo si existe.
        lpo_previo = self.read(4, f"x{gain} LPo antes del ABBA", 3,
                               self.codes[3], require_valid=False)
        rescate_lpo = None
        if lpo_previo.clavado:
            visitados = []
            lo, hi = IDAC_MIN, IDAC_MAX
            encontrado = None
            for _ in range(9):
                medio = (lo + hi) // 2
                self.set_code(3, medio)
                self._wait_fast(4, 3)
                p = self.read(4, f"x{gain} rescate LPo IDAC3={medio}", 3, medio,
                              require_valid=False)
                visitados.append({"codigo": medio,
                                  "lpo_real_mv": p.error_real_mv,
                                  "clavado": p.clavado})
                if not p.clavado:
                    encontrado = medio
                    break
                # clavado abajo -> subir IDAC3; clavado arriba -> bajarlo
                if p.mean_bank_uv < TARGET_UV:
                    lo = medio + 1
                else:
                    hi = medio - 1
                if lo > hi:
                    break
            rescate_lpo = {"convergio": encontrado is not None,
                           "codigo": encontrado, "visitados": visitados}
            self.event("lpo_rescate" if encontrado is not None
                       else "lpo_rescate_fallido",
                       f"x{gain}: LPo clavado; rescate por biseccion "
                       + (f"lo saco en IDAC3={encontrado}"
                          if encontrado is not None
                          else f"NO encontro punto vivo en {len(visitados)} pasos"),
                       gain=gain, result=rescate_lpo)
            if encontrado is None:
                result = {"gain": gain, "pass": False,
                          "reason": "LPo clavado y sin punto vivo alcanzable",
                          "rescate_lpo": rescate_lpo, "codes": list(self.codes)}
                return result

        abba3 = self.pendiente_abba(3, 4, (8, 4), ABBA_ESPERA_S)
        slope3 = abba3["slope_bank_uv_per_code"]
        if slope3 is None or abs(slope3) < 1.0:
            # Sin pendiente utilizable se sigue con la constante de respaldo: el
            # PI cierra igual y es preferible a abortar la ganancia entera.
            slope3 = bank_uv(PASO_IDAC3_CH4_MV)
            self.event("abba3_fallback",
                       f"x{gain}: IDAC3 sin pendiente ABBA; se usa el respaldo "
                       f"de {PASO_IDAC3_CH4_MV} mV/código para inicializar el PI",
                       gain=gain)
        idac3 = self.ajustar_actuador(3, 4, float(slope3))
        points = self.read_five_taps(f"x{gain} final", robust=True)
        final = {ch: asdict(p) for ch, p in points.items()}
        acceptance = evaluar_aceptacion(final, list(self.codes))
        degraded = final
        degraded_codes = list(self.codes)
        fallback = {"available": False, "used": False,
                    "reason": "no hizo falta"}
        if not acceptance["pass"]:
            fallback = self._fallback_guardado(remembered, degraded,
                                                degraded_codes)
            if fallback["used"]:
                final = fallback["remeasured"]["final"]
                acceptance = evaluar_aceptacion(final, list(self.codes))
        result = {"gain": gain, "pass": acceptance["pass"],
                  "class": ("comprometida" if gain in GANANCIAS_COMPROMETIDAS
                            else "mejor_esfuerzo"),
                  "codes": list(self.codes), "seed": seed,
                  "objective_ch2_real_mv": objective,
                  "rail_rescue": rail_rescue,
                  "abba_idac2": abba2, "idac2_slope_source": slope_source,
                  "idac2": idac2, "prediction_sum": prediction,
                  "abba_idac3": abba3, "idac3": idac3,
                  "final": final, "acceptance": acceptance,
                  "puntaje": acceptance["puntaje"],
                  "fallback_ultimo_bueno": fallback,
                  "lp_error_real_mv": final[4]["error_real_mv"],
                  "elapsed_s": time.monotonic()-started}
        self.event("gain_pass" if result["pass"] else "gain_fail",
                   f"x{gain} {'PASS' if result['pass'] else 'FAIL'}; "
                   f"LPo logrado={result['lp_error_real_mv']:+.1f} mV",
                   result=result)
        if result["pass"] and history is not None:
            history.setdefault("gains", {})[str(gain)] = {
                "codes": list(self.codes), "verified_at": datetime.now().isoformat(),
                "lp_error_real_mv": result["lp_error_real_mv"],
                "puntaje": result["puntaje"], "final": final,
                "source": "semilla verificada por ABBA y realimentación"}
            save_history(history)
        return result

    @staticmethod
    def _summary(requested: list[int], results: list[dict]) -> dict:
        passed, total = sum(bool(r.get("pass")) for r in results), len(requested)
        return {"requested": requested, "results": results, "passed": passed,
                "total": total, "completed": len(results),
                "percentage": 100.0*passed/total if total else None,
                "pass": len(results) == total and passed == total}

    def calibrate_gain_campaign(self, gains: list[int] | None = None,
                                *, budget_s: float = 600.0) -> dict:
        """Por defecto recorre x1..x24 y separa explícitamente mejor esfuerzo."""
        requested = list(GANANCIAS_COMPROMETIDAS if gains is None else gains)
        if any(g not in GAIN_CODES for g in requested):
            raise ValueError("ganancia no soportada en campaña")
        committed = [g for g in requested if g in GANANCIAS_COMPROMETIDAS]
        best_effort = [g for g in requested if g not in GANANCIAS_COMPROMETIDAS]
        history, started = load_history(), time.monotonic()
        self.deadline = started + budget_s
        phase1 = self.fase1_margen(history)
        self.deadline = None
        committed_results, best_results = [], []
        if phase1["pass"]:
            history["phase1"] = {
                "codes": self.codes[:2], "tau_s": phase1.get("tau_s"),
                "verified_at": datetime.now().isoformat(),
                "puntaje": phase1["puntaje"],
                "final": phase1["verified"],
                "source": "medición actual; no sustituye verificaciones"}
            save_history(history)
            for gain in committed:
                item = self.fase2_ganancia(gain, history)
                committed_results.append(item)
                if not item["pass"]:
                    break
            if len(committed_results) == len(committed) and all(
                    item["pass"] for item in committed_results):
                for gain in best_effort:
                    item = self.fase2_ganancia(gain, history)
                    best_results.append(item)
                    if not item["pass"]:
                        break
        committed_summary = self._summary(committed, committed_results)
        best_summary = self._summary(best_effort, best_results)
        overall = phase1["pass"] and (committed_summary["pass"] if committed
                                      else best_summary["pass"])
        all_gain_results = committed_results + best_results
        score = (all_gain_results[-1].get("puntaje", phase1["puntaje"])
                 if all_gain_results else phase1["puntaje"])
        result = {"pass": overall, "phase1": phase1,
                  "puntaje": score,
                  "comprometidas": committed_summary,
                  "mejor_esfuerzo": best_summary,
                  "total_elapsed_s": time.monotonic()-started}
        self.event("gain_campaign_result",
                   "Campaña: comprometidas "
                   f"{committed_summary['passed']}/{committed_summary['total']}; "
                   "mejor esfuerzo "
                   f"{best_summary['passed']}/{best_summary['total']}", result=result)
        return result

    def calibrate_full(self) -> dict:
        history = load_history()
        phase1 = self.fase1_margen(history)
        phase2 = self.fase2_ganancia(1, history) if phase1["pass"] else None
        return {"pass": bool(phase1["pass"] and phase2 and phase2["pass"]),
                "puntaje": (phase2.get("puntaje", phase1["puntaje"])
                            if phase2 else phase1["puntaje"]),
                "phase1": phase1, "phase2": phase2}


def _test_point(channel: int, error_real: float, pp: int = 1000) -> Point:
    error = int(round(bank_uv(error_real)))
    return Point(0.0, "test", min(channel, 3), 0, channel, TARGET_UV+error,
                 error, real_mv(error), pp, True, clavado(pp))


class _FakeLab:
    def __init__(self) -> None:
        self.idac_writes: list[tuple[int, int]] = []

    def set_adc_config(self, _config: int) -> bool:
        return True

    def set_gain(self, _name: str, _code: int) -> bool:
        return True

    def set_idac(self, stage: int, code: int) -> bool:
        self.idac_writes.append((stage, code))
        return True


class _TestCalibrator(TwoPhaseCalibrator):
    def __init__(self, lab: _FakeLab, output: Path,
                 points: dict[int, Point]) -> None:
        super().__init__(lab, output, samples=1)  # type: ignore[arg-type]
        self.test_points, self.read_count = points, 0

    def save(self) -> None:
        pass

    def event(self, kind: str, message: str, **data) -> None:
        self.events.append({"kind": kind, "message": message, **data})

    def read(self, channel: int, phase: str, stage: int, code: int,
             *, require_valid: bool = True) -> Point:
        if channel == 5:
            raise AssertionError("se intentó seleccionar ch5")
        self.read_count += 1
        p = self.test_points[channel]
        return Point(p.elapsed_s, phase, stage, code, channel, p.mean_bank_uv,
                     p.error_bank_uv, p.error_real_mv, p.pp_bank_uv,
                     p.valid, p.clavado)

    def wait_channel_stable(self, channel: int, *, stage: int, code: int,
                            timeout_s: float = 180.0, sample_period_s: float = 3.0,
                            required: int = 3) -> Point:
        return self.read(channel, "estable test", stage, code, require_valid=False)

    def medir_pendientes_fase1(self) -> dict:
        modelo = modelo_respaldo_fase1()
        return {"abba_idac0_ch0": {}, "abba_idac1_ch1": {},
                "modelo_real_mv_por_codigo": modelo,
                "uso_respaldo": True}


class _ScriptedSlopeCalibrator(_TestCalibrator):
    def __init__(self, lab: _FakeLab, output: Path,
                 points: dict[int, Point], slopes: tuple[float | None,
                                                        float | None]) -> None:
        super().__init__(lab, output, points)
        self.scripted_slopes = slopes

    def pendiente_abba(self, stage: int, channel: int,
                       deltas: Iterable[int] = (8, 4),
                       settle_s: float = ABBA_ESPERA_S) -> dict:
        slope = self.scripted_slopes[stage]
        return {"stage": stage, "channel": channel,
                "deltas": list(deltas), "method": "ABBA test",
                "slope_bank_uv_per_code": (None if slope is None
                                             else bank_uv(slope)),
                "slope_real_mv_per_code": slope, "records": []}

    def medir_pendientes_fase1(self) -> dict:
        return TwoPhaseCalibrator.medir_pendientes_fase1(self)


class _RealizedSlopeCalibrator(_TestCalibrator):
    """Planta sintética cuyo ABBA inicial subestima 70 % el paso de IDAC0."""
    def __init__(self, lab: _FakeLab, output: Path) -> None:
        super().__init__(lab, output,
                         {ch: _test_point(ch, 0.0) for ch in range(5)})
        self.base_errors = (-1000.0, -500.0, -2000.0)

    def read(self, channel: int, phase: str, stage: int, code: int,
             *, require_valid: bool = True) -> Point:
        error0, error1, error2 = self.base_errors
        errors = {
            0: error0 + 85.0*self.codes[0],
            1: error1 + 2.0*self.codes[1],
            2: (error2 - GANANCIA_SUM_DESDE_SEO*85.0*self.codes[0] -
                GANANCIA_SUM_DESDE_BPO*2.0*self.codes[1]),
            3: 0.0, 4: 0.0}
        source = _test_point(channel, errors[channel])
        self.read_count += 1
        return Point(source.elapsed_s, phase, stage, code, channel,
                     source.mean_bank_uv, source.error_bank_uv,
                     source.error_real_mv, source.pp_bank_uv,
                     source.valid, source.clavado)

    def medir_pendientes_fase1(self) -> dict:
        modelo = modelo_pendientes_fase1(
            50.0, 2.0, fuente_idac0="ABBA sintético errado",
            fuente_idac1="ABBA sintético")
        return {"abba_idac0_ch0": {}, "abba_idac1_ch1": {},
                "modelo_real_mv_por_codigo": modelo,
                "uso_respaldo": False}

    def medir_tau_bp(self, stage: int,
                     guard_point: Point | None = None) -> tuple[float, dict]:
        self.tau_bp_s = TAU_BP_REFERENCIA_S
        return self.tau_bp_s, {"synthetic": True}

    def _wait_bp(self, tau: float, stage: int) -> Point:
        return self.read(1, "tau sintético", stage, self.codes[stage],
                         require_valid=False)


class _ScriptedRescueCalibrator(_TestCalibrator):
    def __init__(self, lab: _FakeLab, output: Path,
                 ch2_points: list[Point]) -> None:
        centered = {ch: _test_point(ch, 0.0) for ch in range(5)}
        super().__init__(lab, output, centered)
        self.ch2_points = list(ch2_points)

    def read(self, channel: int, phase: str, stage: int, code: int,
             *, require_valid: bool = True) -> Point:
        if channel == 2 and self.ch2_points:
            source = self.ch2_points.pop(0)
            return Point(source.elapsed_s, phase, stage, code, channel,
                         source.mean_bank_uv, source.error_bank_uv,
                         source.error_real_mv, source.pp_bank_uv,
                         source.valid, source.clavado)
        return super().read(channel, phase, stage, code,
                            require_valid=require_valid)

    def _wait_bp(self, tau: float, stage: int) -> Point:
        return _test_point(1, 0.0)


class Autotest(unittest.TestCase):
    def test_objetivo_ch2(self) -> None:
        # El objetivo es el MENOR de dos presupuestos: que SUMo no riele y que
        # IDAC3 pueda centrar LPo. El segundo manda siempre, por ~2,6 veces.
        for ganancia in (1, 2, 4, 8, 16, 24, 32, 50):
            por_sumo = (SWING_LPO_MV - MARGEN_RIEL_MV) / ganancia
            disponible = autoridad_idac3_mv() - MARGEN_RIEL_MV
            por_lpo = disponible / (ganancia * abs(GANANCIA_LP_DESDE_CH2))
            self.assertEqual(objetivo_ch2_mv(ganancia),
                             int(round(min(por_sumo, por_lpo))))
            self.assertLess(por_lpo, por_sumo,
                            f"en x{ganancia} deberia mandar la cota de LPo")
        # y cae como 1/G
        # cae como 1/G, con el redondeo a entero como unica desviacion
        self.assertAlmostEqual(objetivo_ch2_mv(1) / objetivo_ch2_mv(24),
                               24.0, delta=1.5)
        with self.assertRaises(ValueError):
            objetivo_ch2_mv(0)

    def test_aceptacion_exige_el_margen_y_no_solo_el_riel(self) -> None:
        """Regresion del bug del 2026-09-10.

        La aceptacion comparaba el margen contra CERO, asi que un tap a 100 mV
        del riel, con MARGEN_RIEL_MV=250 exigido, daba PASS. El criterio no se
        estaba aplicando. Aca se comprueba que un tap DENTRO de la guarda pero
        sin haber llegado al riel hace fallar la aceptacion.
        """
        limite_alto = min(BANCO_MAX_VALIDO_MV, BANCO_RIEL_ALTO_MEDIDO_MV)*1000.0
        # 100 mV fisicos por debajo del riel: no lo toca, pero viola la guarda
        media = limite_alto - bank_uv(100.0)
        final = {ch: _test_point(ch, 0.0) for ch in range(5)}
        final[4] = Point(elapsed_s=0.0, phase="t", stage=3, code=0, channel=4,
                         mean_bank_uv=int(media),
                         error_bank_uv=int(media) - TARGET_UV,
                         error_real_mv=real_mv(int(media) - TARGET_UV),
                         pp_bank_uv=2000, valid=True, clavado=False)
        self.assertTrue(toca_riel(media, 2000, MARGEN_RIEL_MV))
        veredicto = evaluar_aceptacion(final)
        self.assertFalse(veredicto["pass"])
        self.assertTrue(any(f["condicion"] == "toca_riel"
                            for f in veredicto["fallas"]))
        self.assertIn(4, veredicto["fallas"][0]["canales"])

    def test_toca_riel_considera_excursion(self) -> None:
        high = (min(BANCO_MAX_VALIDO_MV, BANCO_RIEL_ALTO_MEDIDO_MV)*1000.0 -
                bank_uv(MARGEN_RIEL_MV))
        pp = int(2.0*(high-TARGET_UV+1.0))
        self.assertTrue(VALID_MIN_UV <= TARGET_UV <= VALID_MAX_UV)
        self.assertTrue(toca_riel(TARGET_UV, pp))

    def test_objetivo_sub_lsb_para_por_resolucion(self) -> None:
        with TemporaryDirectory() as tmp:
            points = {ch: _test_point(ch, 0.0) for ch in range(5)}
            # 2 uV de error / 10 uV por código = 0,2 códigos.
            points[4] = Point(0, "test", 3, 0, 4, TARGET_UV+2, 2,
                              real_mv(2), 1000, True, False)
            cal = _TestCalibrator(_FakeLab(), Path(tmp)/"t.json", points)
            result = cal.ajustar_actuador(3, 4, 10.0)
            self.assertEqual(result["stop_reason"], "resolucion_medio_paso")
            self.assertEqual(result["iterations"], 0)

    def test_fase1_no_mueve_idac0_con_margen(self) -> None:
        with TemporaryDirectory() as tmp:
            points = {ch: _test_point(ch, 0.0) for ch in range(5)}
            points[0] = _test_point(0, -1000.0)
            lab = _FakeLab()
            cal = _TestCalibrator(lab, Path(tmp)/"t.json", points)
            result = cal.fase1_margen({})
            self.assertTrue(result["pass"])
            self.assertFalse(result["idacs"]["0"]["moved"])
            self.assertFalse(any(stage == 0 and code != 0
                                 for stage, code in lab.idac_writes))

    def test_optimizador_fase1_encuentra_solucion(self) -> None:
        taps = {ch: _test_point(ch, 0.0) for ch in range(5)}
        taps[2] = _test_point(2, PASO_IDAC0_CH2_MV)
        result = optimizar_fase1(taps)
        self.assertTrue(result["feasible"])
        # No se afirma un punto literal: con el desempate por reparto el optimo
        # depende de como se distribuya el esfuerzo, y fijar [-1, 0] a mano
        # congelaba el criterio viejo. Se afirman los invariantes.
        residuo = (result["ch2_error_predicho_real_mv"]
                   + result["idac2_previsto"]*PASO_IDAC2_CH2_MV)
        self.assertLessEqual(abs(residuo), PASO_IDAC2_CH2_MV/2.0)
        self.assertLessEqual(abs(result["idac2_previsto"]), IDAC_MAX)
        self.assertGreaterEqual(
            result["margenes_predichos_real_mv"]["minimo"],
            MARGEN_FASE1_MV)
        # y que efectivamente REPARTE: ningun recurso puede quedar exhausto
        self.assertGreater(result["recurso_mas_gastado"], 0.5)

    def test_pendientes_derivadas_invierten_sentido_hacia_vref(self) -> None:
        taps = {ch: _test_point(ch, 0.0) for ch in range(5)}
        taps[2] = _test_point(2, 1000.0)
        modelo = modelo_pendientes_fase1(
            85.0, 2.1, fuente_idac0="ABBA test", fuente_idac1="ABBA test")
        self.assertLess(modelo["ch2_idac0"], 0.0)
        self.assertLess(modelo["ch2_idac1"], 0.0)
        result = optimizar_fase1(taps, modelo=modelo)
        self.assertTrue(result["feasible"])
        efecto = (result["codes"][0]*modelo["ch2_idac0"] +
                  result["codes"][1]*modelo["ch2_idac1"])
        self.assertLess(efecto*taps[2].error_real_mv, 0.0)
        self.assertLess(abs(result["ch2_error_predicho_real_mv"]),
                        abs(taps[2].error_real_mv))

    def test_pendiente_no_utilizable_usa_respaldo_y_lo_informa(self) -> None:
        with TemporaryDirectory() as tmp:
            points = {ch: _test_point(ch, 0.0) for ch in range(5)}
            cal = _ScriptedSlopeCalibrator(
                _FakeLab(), Path(tmp)/"t.json", points, (85.0, None))
            result = cal.medir_pendientes_fase1()
            modelo = result["modelo_real_mv_por_codigo"]
            self.assertEqual(modelo["ch0_idac0"], 85.0)
            self.assertEqual(modelo["ch1_idac1"], PASO_IDAC1_CH1_MV)
            self.assertEqual(modelo["ch2_idac1"], PASO_IDAC1_CH2_MV)
            fallback = [event for event in cal.events
                        if event["kind"] == "phase1_slope_fallback"]
            self.assertEqual(len(fallback), 1)
            self.assertIn("se usa respaldo", fallback[0]["message"])

    def test_recalculo_desde_salto_corrige_error_del_70_por_ciento(self) -> None:
        modelo_errado = modelo_pendientes_fase1(
            50.0, 2.0, fuente_idac0="ABBA inicial",
            fuente_idac1="ABBA inicial")
        antes = {ch: _test_point(ch, 0.0) for ch in range(5)}
        despues = {ch: _test_point(ch, 0.0) for ch in range(5)}
        # Se ordenaron 10 códigos esperando 500 mV, pero se realizaron 850 mV.
        despues[0] = _test_point(0, 850.0)
        recalculado, detalle = recalcular_modelo_fase1(
            modelo_errado, antes, despues, [0, 0], [10, 0], intento=1)
        self.assertAlmostEqual(recalculado["ch0_idac0"], 85.0, places=2)
        self.assertAlmostEqual(recalculado["ch2_idac0"],
                               (-GANANCIA_SUM_DESDE_SEO *
                                recalculado["ch0_idac0"]))
        self.assertEqual(detalle["actualizadas"], ["ch0_idac0"])
        self.assertEqual(recalculado["fuentes"]["ch0_idac0"],
                         "salto realizado intento 1")

    def test_fase1_reoptimiza_con_pendiente_realizada(self) -> None:
        with TemporaryDirectory() as tmp:
            cal = _RealizedSlopeCalibrator(
                _FakeLab(), Path(tmp)/"t.json")
            result = cal.fase1_margen({})
            self.assertTrue(result["pass"])
            self.assertEqual(len(result["recalculos_desde_salto"]), 1)
            self.assertAlmostEqual(
                result["modelo_final_real_mv_por_codigo"]["ch0_idac0"],
                85.0, places=2)
            self.assertEqual(result["codes"][:2], [-1, -135])
            self.assertTrue(any(
                event["kind"] == "phase1_slopes_recalculated"
                for event in cal.events))

    def test_optimizador_fase1_informa_sin_punto_factible(self) -> None:
        # Una excursión pp de 3 V no cabe entre dos guardas duras de 1 V.
        pp = int(round(bank_uv(3000.0)))
        taps = {ch: _test_point(ch, 0.0) for ch in range(5)}
        taps[0] = _test_point(0, 0.0, pp)
        result = optimizar_fase1(taps)
        self.assertFalse(result["feasible"])
        self.assertEqual(result["reason"], "sin punto factible")
        self.assertIsNone(result["codes"])
        self.assertIn("mejores_margenes_alcanzables", result)

    def test_rescate_desde_riel_converge(self) -> None:
        with TemporaryDirectory() as tmp:
            initial = _test_point(2, -2500.0, 100)
            closer = _test_point(2, -1500.0, 100)
            live = _test_point(2, -200.0, 1000)
            cal = _ScriptedRescueCalibrator(
                _FakeLab(), Path(tmp)/"t.json", [closer, live])
            result = cal.rescatar_ch2_desde_riel(initial)
            self.assertTrue(result["converged"])
            self.assertEqual(result["iterations"], 2)
            self.assertTrue(all(item["approached"]
                                for item in result["attempts"]))

    def test_rescate_desde_riel_aborta_si_no_acerca(self) -> None:
        with TemporaryDirectory() as tmp:
            initial = _test_point(2, -2500.0, 100)
            farther = _test_point(2, 2200.0, 100)
            cal = _ScriptedRescueCalibrator(
                _FakeLab(), Path(tmp)/"t.json", [farther])
            result = cal.rescatar_ch2_desde_riel(initial)
            self.assertFalse(result["converged"])
            self.assertEqual(result["reason"], "el rescate no acerca ch2")
            self.assertEqual(result["iterations"], 1)

    def test_abba_no_se_intenta_sobre_tap_clavado(self) -> None:
        with TemporaryDirectory() as tmp:
            points = {ch: _test_point(ch, 0.0) for ch in range(5)}
            points[2] = _test_point(2, 2100.0, 100)
            lab = _FakeLab()
            cal = _TestCalibrator(lab, Path(tmp)/"t.json", points)
            result = cal.pendiente_abba(2, 2)
            self.assertIsNone(result["slope_bank_uv_per_code"])
            self.assertEqual(result["reason"], "tap clavado; ABBA prohibido")
            self.assertEqual(lab.idac_writes, [])

    def test_puntaje_ordena_y_es_negativo_si_toca(self) -> None:
        better = {ch: _test_point(ch, 0.0) for ch in range(5)}
        worse = {ch: _test_point(ch, 0.0) for ch in range(5)}
        worse[3] = _test_point(3, 1500.0)
        touching = {ch: _test_point(ch, 0.0) for ch in range(5)}
        touching[1] = _test_point(1, 2200.0)
        self.assertGreater(puntaje(better), puntaje(worse))
        self.assertLess(puntaje(touching), 0.0)
        acceptance = evaluar_aceptacion(
            {ch: asdict(point) for ch, point in better.items()})
        self.assertEqual(acceptance["puntaje"], puntaje(better))

    def test_calidad_desempata_por_error_lpo(self) -> None:
        centered_lp = {ch: _test_point(ch, 0.0) for ch in range(5)}
        offset_lp = {ch: _test_point(ch, 0.0) for ch in range(5)}
        centered_lp[0] = _test_point(0, 1500.0)
        offset_lp[0] = _test_point(0, 1500.0)
        offset_lp[4] = _test_point(4, 500.0)
        self.assertAlmostEqual(puntaje(centered_lp), puntaje(offset_lp))
        self.assertGreater(_calidad(centered_lp), _calidad(offset_lp))


def run_autotest() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Autotest)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


def _restore_neutral(cal: TwoPhaseCalibrator) -> None:
    """Finally obligatorio: PGAout x1 y los cuatro IDAC en cero."""
    try:
        cal.lab.set_gain("pgaout", PGAOUT_X1_CODE)
        cal.current_gain = 1
    except Exception:
        pass
    for stage in range(4):
        try:
            cal.set_code(stage, 0)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=(
        "calibrate-upstream", "calibrate-full", "calibrate-gains"))
    parser.add_argument("--autotest", action="store_true",
                        help="pruebas sin abrir el puerto serie")
    parser.add_argument("--port", default="COM8")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--campaign-repeats", type=int, default=1)
    parser.add_argument("--budget-s", type=float, default=600.0,
                        help="presupuesto fase 1; cubre tau+ABBA lento si hace falta")
    parser.add_argument("--gains", default=",".join(
        str(g) for g in GANANCIAS_COMPROMETIDAS),
        help=("orden de PRIORIDAD, no de recorrido: x24 y x16 primero, porque son las que se usan con el martillo lejos"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--save-eeprom", action="store_true")
    parser.add_argument("--keep-on-pass", action="store_true",
                        help="obsoleto: finally siempre restaura neutro")
    args = parser.parse_args()
    if args.autotest:
        return run_autotest()
    if args.mode is None:
        parser.error("falta mode salvo con --autotest")
    if args.repeat < 1 or args.campaign_repeats < 1:
        parser.error("las repeticiones deben ser >=1")
    gains = [int(item.strip()) for item in args.gains.split(",") if item.strip()]
    if any(g not in GAIN_CODES for g in gains):
        parser.error("--gains contiene un valor no soportado")
    output = args.output or (Path(__file__).resolve().parents[3]/"lab"/
        "calibracion_nueva"/
        f"dos_fases_{args.mode}_{datetime.now():%Y%m%d_%H%M%S}.json")
    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    cal = TwoPhaseCalibrator(Lab(Session(console)), output, samples=args.samples)
    exit_code, runs = 1, []
    try:
        repeats = args.campaign_repeats if args.mode == "calibrate-gains" else args.repeat
        for index in range(1, repeats+1):
            cal.event("run_start", f"Corrida {index}/{repeats}")
            if args.mode == "calibrate-upstream":
                result = cal.fase1_margen(load_history())
            elif args.mode == "calibrate-full":
                result = cal.calibrate_full()
            else:
                result = cal.calibrate_gain_campaign(gains, budget_s=args.budget_s)
            runs.append({"run": index, "pass": result["pass"], "result": result})
            _restore_neutral(cal)
        exit_code = 0 if all(run["pass"] for run in runs) else 1
        cal.event("campaign_summary",
                  f"Corridas: {sum(r['pass'] for r in runs)}/{len(runs)} PASS",
                  runs=runs)
        if exit_code == 0 and args.save_eeprom:
            x1 = load_history().get("gains", {}).get("1")
            if not x1:
                raise RuntimeError("no hay punto x1 medido para EEPROM")
            for stage, code in enumerate(x1["codes"]):
                cal.set_code(stage, int(code))
            final = {ch: asdict(p) for ch, p in
                     cal.read_five_taps("EEPROM verificación", robust=True).items()}
            acceptance = evaluar_aceptacion(final, list(cal.codes))
            if not acceptance["pass"]:
                raise RuntimeError("x1 no pasó reverificación; EEPROM no escrita")
            if not cal.lab.commit_calibration():
                raise RuntimeError("PSoC rechazó guardado EEPROM")
            cal.event("eeprom_saved", "EEPROM guardada tras reverificación")
    except Exception as exc:
        cal.event("abort", f"ABORT: {type(exc).__name__}: {exc}")
        exit_code = 1
    finally:
        _restore_neutral(cal)
        cal.save()
        console.close()
        print(f"Evidencia: {output}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
