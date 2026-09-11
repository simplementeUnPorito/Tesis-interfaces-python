"""Mapea IDAC1/IDAC2 sobre OPA_SUMo y SUMo sin contaminar la pendiente rapida.

Cada lectura es la mediana de tres ventanas DC. Las filas se escriben y se
vacian al disco una por una; las saturaciones se conservan como datos no
validos. El runner no selecciona nunca AMuxCapacitor (ch5) ni guarda
calibracion persistente en el equipo.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from escala_banco import BANCO_VREF_MV, FACTOR
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab, TAP_NAMES
from testbench.core.session import Session
from verificar_deriva_ch0 import (ConsolaSimulada, LaboratorioSimulado,
                                  Lectura, PlazoAgotado, RelojCampana)


CANALES = (0, 1, 2, 3, 4)
IDAC1_IDA = (-255, -192, -128, -64, 0, 64, 128, 192, 255)
IDAC2_MALLA = (-255, -128, -64, 0, 64, 128, 255)
DELTAS_ABBA = (8, 4, 2)
DELTAS_ABBA_IDAC0 = (2, 4)
VENTANAS_POR_LECTURA = 3
REINTENTOS_POR_VENTANA = 4
ESPERA_IDAC_RAPIDO_S = 1.5
ESPERA_CENTRADO_S = 2.0
INTERVALO_ASENTAMIENTO_S = 5.0
PISO_ASENTAMIENTO_S = 60.0
TECHO_ASENTAMIENTO_S = 240.0
ESTIMADO_TOTAL_S = 45.0 * 60.0

CAMPOS = (
    "t_s", "pasada", "idac0", "idac1", "idac2", "fase", "posicion",
    "asentamiento_incompleto", "ch", "tap",
    "media_banco_uv", "pp_banco_uv", "ok", "valido", "clavado",
    "real_mv_vs_vref",
)


def resolver_salidas(output: Path | None,
                     simular: bool = False) -> tuple[Path, Path]:
    if output is None:
        base = (Path(__file__).resolve().parents[3] / "lab" /
                "calibracion_nueva" /
                # una corrida simulada no puede ensuciar la evidencia real
                ("simulacion" if simular else "."))
        csv_path = base / f"mapear_alcance_sumador_{datetime.now():%Y%m%d_%H%M%S}.csv"
    else:
        csv_path = output if output.suffix.lower() == ".csv" else output.with_suffix(".csv")
    return csv_path, csv_path.with_suffix(".json")


def regresion_lineal(puntos: list[tuple[float, float]]) -> dict[str, Any] | None:
    """Pendiente, ordenada y desvio RMS para un tramo fino."""
    if len(puntos) < 2:
        return None
    xs = [p[0] for p in puntos]
    ys = [p[1] for p in puntos]
    xm = statistics.mean(xs)
    ym = statistics.mean(ys)
    denominador = sum((x - xm) ** 2 for x in xs)
    if denominador == 0:
        return None
    pendiente = sum((x - xm) * (y - ym) for x, y in puntos) / denominador
    intercepto = ym - pendiente * xm
    residuos = [y - (pendiente * x + intercepto) for x, y in puntos]
    rms = math.sqrt(sum(r * r for r in residuos) / len(residuos))
    return {"n": len(puntos), "pendiente_uv_por_codigo": pendiente,
            "intercepto_uv": intercepto, "desvio_rms_uv": rms,
            "signo": "positivo" if pendiente > 0 else
                     ("negativo" if pendiente < 0 else "nulo")}


def resolver_sistema(matriz: list[list[float]], vector: list[float]) -> list[float] | None:
    """Eliminacion gaussiana con pivoteo parcial para una matriz pequena."""
    n = len(vector)
    aumentada = [matriz[i][:] + [vector[i]] for i in range(n)]
    escala = max((abs(v) for fila in matriz for v in fila), default=1.0)
    for columna in range(n):
        pivote = max(range(columna, n), key=lambda f: abs(aumentada[f][columna]))
        if abs(aumentada[pivote][columna]) <= escala * 1e-12:
            return None
        aumentada[columna], aumentada[pivote] = aumentada[pivote], aumentada[columna]
        divisor = aumentada[columna][columna]
        for j in range(columna, n + 1):
            aumentada[columna][j] /= divisor
        for fila in range(n):
            if fila == columna:
                continue
            factor = aumentada[fila][columna]
            for j in range(columna, n + 1):
                aumentada[fila][j] -= factor * aumentada[columna][j]
    return [aumentada[i][n] for i in range(n)]


def minimos_cuadrados(muestras: list[tuple[float, float, float, float]]) -> dict[str, Any]:
    """Ajusta ch2=A*IDAC2+B*IDAC1+C*ch0+K usando solo listas.

    Las tres variables se centran y normalizan antes de formar las ecuaciones
    normales. Esto evita que ch0 (~1e6 uV) destruya numericamente las columnas
    de codigos (~1e2), sin cambiar la solucion de minimos cuadrados.
    """
    if len(muestras) < 4:
        return {"disponible": False, "motivo": "menos de cuatro filas", "n": len(muestras)}
    columnas = [[m[i] for m in muestras] for i in range(3)]
    medias = [statistics.mean(columna) for columna in columnas]
    escalas = []
    for columna, media in zip(columnas, medias):
        escala = math.sqrt(sum((v - media) ** 2 for v in columna) / len(columna))
        if escala < 1e-9:
            return {"disponible": False, "motivo": "columna sin variacion",
                    "n": len(muestras)}
        escalas.append(escala)
    x_normalizada = [
        [(m[i] - medias[i]) / escalas[i] for i in range(3)] + [1.0]
        for m in muestras
    ]
    ys = [m[3] for m in muestras]
    gram = [[sum(f[i] * f[j] for f in x_normalizada)
             for j in range(4)] for i in range(4)]
    termino = [sum(f[i] * y for f, y in zip(x_normalizada, ys))
               for i in range(4)]
    beta = resolver_sistema(gram, termino)
    if beta is None:
        return {"disponible": False, "motivo": "sistema singular", "n": len(muestras)}
    coeficientes = [beta[i] / escalas[i] for i in range(3)]
    intercepto = beta[3] - sum(coeficientes[i] * medias[i] for i in range(3))
    residuos = [m[3] - (sum(coeficientes[i] * m[i] for i in range(3)) + intercepto)
                for m in muestras]
    rms = math.sqrt(sum(r * r for r in residuos) / len(residuos))
    return {
        "disponible": True, "n": len(muestras),
        "A_uv_por_codigo_idac2": coeficientes[0],
        "B_uv_por_codigo_idac1": coeficientes[1],
        "C_uv_por_uv_ch0": coeficientes[2], "K_uv": intercepto,
        "residuo_rms_uv": rms,
        "ecuacion": "ch2_uv = A*idac2 + B*idac1 + C*ch0_uv + K_uv",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, choices=GAIN_CODES, default=50)
    ap.add_argument("--pgaout", type=int, choices=GAIN_CODES, default=1)
    ap.add_argument("--max-s", type=float, default=5400.0,
                    help="duracion maxima total; un corte devuelve codigo 1")
    ap.add_argument(
        "--espera-lenta-s", type=float,
        help=("fuerza una espera fija despues de mover IDAC1; si se omite, "
              "se usa el asentamiento adaptativo"),
    )
    ap.add_argument("--sin-fino", action="store_true",
                    help="omite los tramos ABBA de IDAC2")
    ap.add_argument("--simular", action="store_true")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    if args.max_s <= 0:
        ap.error("--max-s debe ser positivo")
    if args.espera_lenta_s is not None and args.espera_lenta_s < 0:
        ap.error("--espera-lenta-s no puede ser negativo")

    csv_path, json_path = resolver_salidas(args.output, args.simular)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    reloj = RelojCampana(args.max_s, args.simular)
    eventos: list[dict[str, Any]] = []
    filas: list[dict[str, Any]] = []
    capturas: list[dict[str, Any]] = []
    resumen: dict[str, Any] | None = None
    resultado_abba_idac0: dict[str, Any] | None = None
    estado = "iniciando"
    codigo_idac0 = 0

    def guardar_json() -> None:
        contenido = {
            "script": Path(__file__).name, "simular": args.simular,
            "pga": args.pga, "pgaout": args.pgaout, "csv": str(csv_path),
            "estado": estado, "eventos": eventos, "resumen": resumen,
        }
        json_path.write_text(json.dumps(contenido, indent=2, ensure_ascii=False),
                             encoding="utf-8")

    def evento(tipo: str, mensaje: str, **datos: Any) -> None:
        eventos.append({"t_s": round(reloj.transcurrido(), 3), "tipo": tipo,
                        "mensaje": mensaje, **datos})
        guardar_json()

    def progreso(mensaje: str) -> None:
        falta = max(0.0, ESTIMADO_TOTAL_S - reloj.transcurrido())
        print(f"[{datetime.now():%H:%M:%S}] t={reloj.transcurrido():7.1f}s "
              f"{mensaje} | faltan ~{falta / 60.0:.1f} min", flush=True)

    console: Console | ConsolaSimulada
    lab: Lab | LaboratorioSimulado | None = None
    console = ConsolaSimulada() if args.simular else Console(args.port)
    completada = False
    error_final: str | None = None
    archivo = csv_path.open("w", newline="", encoding="utf-8-sig")
    escritor = csv.DictWriter(archivo, fieldnames=CAMPOS)
    escritor.writeheader()
    archivo.flush()

    def escribir_fila(fila: dict[str, Any]) -> None:
        escritor.writerow(fila)
        archivo.flush()
        filas.append(fila)

    def operacion_comprobada(llamada: Callable[[], bool], descripcion: str) -> None:
        for intento in range(1, 5):
            reloj.verificar()
            if llamada():
                return
            if intento < 4:
                reloj.esperar(0.25)
        raise RuntimeError(f"sin ACK: {descripcion}")

    def medir(ch: int) -> Lectura:
        medias: list[float] = []
        picos: list[float] = []
        for ventana in range(1, VENTANAS_POR_LECTURA + 1):
            punto = None
            for intento in range(1, REINTENTOS_POR_VENTANA + 1):
                reloj.verificar()
                punto = lab.measure_dc(ch, 2)  # type: ignore[union-attr]
                if punto is not None and punto.ok:
                    break
                if intento < REINTENTOS_POR_VENTANA:
                    reloj.esperar(0.10)
            if punto is None or not punto.ok:
                evento("lectura_fallida", f"ch{ch}, ventana {ventana} sin dato",
                       ch=ch, ventana=ventana)
                return Lectura(None, None, False)
            medias.append(float(punto.mean_uv))
            picos.append(float(punto.pp_uv))
        return Lectura(statistics.median(medias), statistics.median(picos), True)

    def medir_canales(pasada: int, idac0: int, idac1: int, idac2: int,
                      fase: str, posicion: str = "",
                      asentamiento_incompleto: bool = False,
                      canales: tuple[int, ...] = CANALES,
                      **datos_captura: Any) -> dict[str, Any]:
        captura: dict[str, Any] = {
            "pasada": pasada, "idac0": idac0, "idac1": idac1,
            "idac2": idac2, "fase": fase, "posicion": posicion,
            "asentamiento_incompleto": asentamiento_incompleto,
            "lecturas": {}, **datos_captura,
        }
        for ch in canales:
            lectura = medir(ch)
            captura["lecturas"][ch] = lectura
            real_mv = None
            if lectura.media_uv is not None:
                real_mv = FACTOR * (lectura.media_uv / 1000.0 - BANCO_VREF_MV)
            escribir_fila({
                "t_s": round(reloj.transcurrido(), 3), "pasada": pasada,
                "idac0": idac0, "idac1": idac1, "idac2": idac2,
                "fase": fase, "posicion": posicion,
                "asentamiento_incompleto": asentamiento_incompleto,
                "ch": ch, "tap": TAP_NAMES[ch],
                "media_banco_uv": lectura.media_uv,
                "pp_banco_uv": lectura.pp_uv, "ok": lectura.ok,
                "valido": lectura.valida, "clavado": lectura.clavada,
                "real_mv_vs_vref": real_mv,
            })
        capturas.append(captura)
        return captura

    def lectura_util_abba(captura: dict[str, Any], ch: int) -> Lectura | None:
        lectura = captura["lecturas"].get(ch)
        # En ABBA un pp pequeno no invalida por si solo el punto: puede ser una
        # senal genuinamente quieta. La excursion A/B permite detectar una
        # falta de respuesta; fuera de rango o sin lectura si son "sin dato".
        if lectura is None or not lectura.valida or lectura.media_uv is None:
            return None
        return lectura

    def pendiente_abba(capturas_delta: list[dict[str, Any]],
                       ch: int, delta: int) -> float | None:
        por_posicion = {c["posicion"]: lectura_util_abba(c, ch)
                        for c in capturas_delta}
        if any(por_posicion.get(p) is None for p in ("A1", "B1", "B2", "A2")):
            return None
        medias_a = [por_posicion[p].media_uv for p in ("A1", "A2")]
        medias_b = [por_posicion[p].media_uv for p in ("B1", "B2")]
        return ((statistics.mean(medias_b) - statistics.mean(medias_a)) /
                (2.0 * delta))  # type: ignore[arg-type]

    def esperar_asentamiento_idac1(pasada: int, idac1: int) -> bool:
        """Devuelve True si se alcanzo el techo sin verificar asentamiento."""
        if args.espera_lenta_s is not None:
            reloj.esperar(args.espera_lenta_s)
            evento("asentamiento_fijo", "se aplico la espera lenta forzada",
                   pasada=pasada, idac1=idac1,
                   espera_s=args.espera_lenta_s)
            return False

        inicio = reloj.transcurrido()
        anterior: float | None = None
        diferencias_bajas = 0
        muestras = 0
        ultima_diferencia: float | None = None
        ultimo_umbral: float | None = None
        while reloj.transcurrido() - inicio < TECHO_ASENTAMIENTO_S:
            restante = TECHO_ASENTAMIENTO_S - (reloj.transcurrido() - inicio)
            reloj.esperar(min(INTERVALO_ASENTAMIENTO_S, restante))
            if reloj.transcurrido() - inicio >= TECHO_ASENTAMIENTO_S:
                break
            lectura = medir(1)
            muestras += 1
            if lectura.media_uv is None or lectura.pp_uv is None:
                anterior = None
                diferencias_bajas = 0
                continue
            ultima_diferencia = (None if anterior is None else
                                 abs(lectura.media_uv - anterior))
            ultimo_umbral = lectura.pp_uv / 2.0
            if ultima_diferencia is not None and ultima_diferencia < ultimo_umbral:
                diferencias_bajas += 1
            else:
                diferencias_bajas = 0
            anterior = lectura.media_uv
            transcurrido = reloj.transcurrido() - inicio
            if (transcurrido >= PISO_ASENTAMIENTO_S and
                    diferencias_bajas >= 2):
                evento("asentamiento_completo", "ch1 se considero asentado",
                       pasada=pasada, idac1=idac1,
                       espera_s=round(transcurrido, 3), muestras=muestras,
                       ultima_diferencia_uv=ultima_diferencia,
                       ultimo_umbral_uv=ultimo_umbral)
                return False

        transcurrido = reloj.transcurrido() - inicio
        evento("asentamiento_incompleto",
               "ch1 no cumplio el criterio antes del techo; se continua",
               pasada=pasada, idac1=idac1,
               espera_s=round(transcurrido, 3), muestras=muestras,
               ultima_diferencia_uv=ultima_diferencia,
               ultimo_umbral_uv=ultimo_umbral)
        return True

    def centrar_idac0() -> int:
        muestras: dict[int, Lectura] = {}
        for codigo in (-2, 2):
            operacion_comprobada(lambda c=codigo: lab.set_idac(0, c),  # type: ignore[union-attr]
                                 f"IDAC0={codigo:+d}")
            reloj.esperar(ESPERA_CENTRADO_S)
            muestras[codigo] = medir(0)
        izquierda, derecha = muestras[-2], muestras[2]
        if izquierda.media_uv is None or derecha.media_uv is None:
            operacion_comprobada(lambda: lab.set_idac(0, 0), "IDAC0=0")  # type: ignore[union-attr]
            evento("centrado_incompleto", "sin pendiente local; se congela IDAC0=0")
            return 0
        pendiente = (derecha.media_uv - izquierda.media_uv) / 4.0
        if abs(pendiente) < 1e-9:
            operacion_comprobada(lambda: lab.set_idac(0, 0), "IDAC0=0")  # type: ignore[union-attr]
            evento("centrado_incompleto", "pendiente IDAC0 nula; se congela en 0")
            return 0
        intercepto = (izquierda.media_uv + derecha.media_uv) / 2.0
        codigo = max(-32, min(32, round((BANCO_VREF_MV * 1000.0 - intercepto) /
                                       pendiente)))
        operacion_comprobada(lambda c=codigo: lab.set_idac(0, c),  # type: ignore[union-attr]
                             f"salto Newton IDAC0={codigo:+d}")
        reloj.esperar(ESPERA_CENTRADO_S)
        lectura = medir(0)
        # El escalon del propio IDAC0 sobre ch0 con PGA x50 es de decenas de mV
        # fisicos: pedir un error menor que medio escalon es pedir algo que el
        # actuador no puede dar, y el lazo termina oscilando entre dos codigos
        # vecinos y quedandose en el ultimo por azar. Se recorre un entorno
        # chico y se conserva el codigo que MENOS error deja de los visitados.
        visitados: dict[int, Lectura] = {codigo: lectura}
        medio_paso = abs(pendiente) / 2.0
        pasos = 0
        while (lectura.media_uv is not None and
               abs(lectura.media_uv - BANCO_VREF_MV * 1000.0) >= medio_paso and
               pasos < 6):
            direccion = 1 if ((BANCO_VREF_MV * 1000.0 - lectura.media_uv) /
                              pendiente) > 0 else -1
            nuevo = max(-255, min(255, codigo + direccion))
            if nuevo == codigo or nuevo in visitados:
                break
            codigo = nuevo
            operacion_comprobada(lambda c=codigo: lab.set_idac(0, c),  # type: ignore[union-attr]
                                 f"correccion IDAC0={codigo:+d}")
            reloj.esperar(ESPERA_CENTRADO_S)
            lectura = medir(0)
            visitados[codigo] = lectura
            pasos += 1
        medidos = {c: l for c, l in visitados.items() if l.media_uv is not None}
        if medidos:
            codigo = min(medidos, key=lambda c: abs(
                medidos[c].media_uv - BANCO_VREF_MV * 1000.0))
            operacion_comprobada(lambda c=codigo: lab.set_idac(0, c),  # type: ignore[union-attr]
                                 f"mejor codigo IDAC0={codigo:+d}")
            reloj.esperar(ESPERA_CENTRADO_S)
            lectura = medir(0)
        evento("idac0_congelado", "centrado terminado; IDAC0 queda fijo",
               codigo=codigo, pendiente_uv_por_codigo=pendiente,
               error_final_uv=(None if lectura.media_uv is None else
                               lectura.media_uv - BANCO_VREF_MV * 1000.0),
               correcciones_unitarias=pasos)
        return codigo

    def medir_abba_idac0(centro: int,
                         asentamiento_incompleto: bool) -> dict[str, Any]:
        resultados_delta: list[dict[str, Any]] = []
        try:
            for delta in DELTAS_ABBA_IDAC0:
                capturas_delta: list[dict[str, Any]] = []
                secuencia = (("A1", centro - delta), ("B1", centro + delta),
                             ("B2", centro + delta), ("A2", centro - delta))
                if any(not -255 <= codigo <= 255 for _, codigo in secuencia):
                    resultados_delta.append({
                        "delta": delta, "pendiente_ch0_uv_por_codigo": None,
                        "pendiente_ch2_uv_por_codigo": None,
                        "pendiente_ch0_mv_fisicos_por_codigo": None,
                        "pendiente_ch2_mv_fisicos_por_codigo": None,
                        "motivo": "codigos fuera del alcance del IDAC0",
                    })
                    continue
                for posicion, codigo in secuencia:
                    operacion_comprobada(lambda c=codigo: lab.set_idac(0, c),  # type: ignore[union-attr]
                                         f"IDAC0 ABBA={codigo:+d}")
                    reloj.esperar(ESPERA_IDAC_RAPIDO_S)
                    capturas_delta.append(medir_canales(
                        0, codigo, 0, 0, "abba_idac0", posicion,
                        asentamiento_incompleto, (0, 2),
                        delta=delta, centro_idac0=centro,
                    ))
                pendiente_ch0 = pendiente_abba(capturas_delta, 0, delta)
                pendiente_ch2 = pendiente_abba(capturas_delta, 2, delta)
                resultados_delta.append({
                    "delta": delta,
                    "pendiente_ch0_uv_por_codigo": pendiente_ch0,
                    "pendiente_ch2_uv_por_codigo": pendiente_ch2,
                    "pendiente_ch0_mv_fisicos_por_codigo": (
                        None if pendiente_ch0 is None else
                        FACTOR * pendiente_ch0 / 1000.0),
                    "pendiente_ch2_mv_fisicos_por_codigo": (
                        None if pendiente_ch2 is None else
                        FACTOR * pendiente_ch2 / 1000.0),
                    "motivo_ch2_sin_dato": (None if pendiente_ch2 is not None else
                                             "ch2 fuera de rango, clavado o sin lectura"),
                })
        finally:
            # El brazo es diagnostico: siempre se vuelve al codigo centrado
            # antes de iniciar la malla, incluso si una lectura falla.
            if lab is not None:
                lab.set_idac(0, centro)

        pendientes_ch0 = [r["pendiente_ch0_uv_por_codigo"]
                          for r in resultados_delta
                          if r["pendiente_ch0_uv_por_codigo"] is not None]
        pendientes_ch2 = [r["pendiente_ch2_uv_por_codigo"]
                          for r in resultados_delta
                          if r["pendiente_ch2_uv_por_codigo"] is not None]
        mediana_ch0 = statistics.median(pendientes_ch0) if pendientes_ch0 else None
        mediana_ch2 = statistics.median(pendientes_ch2) if pendientes_ch2 else None
        ganancia = None
        if mediana_ch0 is not None and mediana_ch2 is not None and abs(mediana_ch0) > 1e-12:
            ganancia = mediana_ch2 / mediana_ch0
        resultado = {
            "centro_idac0": centro, "pendientes_por_delta": resultados_delta,
            "pendiente_ch0_mediana_uv_por_codigo": mediana_ch0,
            "pendiente_ch2_mediana_uv_por_codigo": mediana_ch2,
            "pendiente_ch0_mediana_mv_fisicos_por_codigo": (
                None if mediana_ch0 is None else FACTOR * mediana_ch0 / 1000.0),
            "pendiente_ch2_mediana_mv_fisicos_por_codigo": (
                None if mediana_ch2 is None else FACTOR * mediana_ch2 / 1000.0),
            "ganancia_sumador_desde_seo": ganancia,
        }
        evento("abba_idac0", "brazo ABBA de IDAC0 terminado", **resultado)
        return resultado

    def crear_resumen() -> dict[str, Any]:
        malla = [c for c in capturas if c["fase"] == "malla"]
        tabla_celdas: list[dict[str, Any]] = []
        for pasada in (1, 2):
            for idac1 in IDAC1_IDA:
                for idac2 in IDAC2_MALLA:
                    coincidencias = [c for c in malla if c["pasada"] == pasada and
                                     c["idac1"] == idac1 and c["idac2"] == idac2]
                    if not coincidencias:
                        continue
                    lecturas = coincidencias[0]["lecturas"]
                    tabla_celdas.append({
                        "pasada": pasada, "idac1": idac1, "idac2": idac2,
                        "ch2_valido": lecturas[2].valida,
                        "ch3_valido": lecturas[3].valida,
                    })

        pendientes_locales: list[dict[str, Any]] = []
        for pasada in (1, 2):
            for idac1 in IDAC1_IDA:
                grupo = [c for c in capturas if c["fase"] == "abba" and
                         c["pasada"] == pasada and c["idac1"] == idac1]
                if not grupo:
                    continue
                por_delta: list[dict[str, Any]] = []
                for delta in DELTAS_ABBA:
                    capturas_delta = [c for c in grupo if c.get("delta") == delta]
                    pendiente = pendiente_abba(capturas_delta, 2, delta)
                    por_delta.append({
                        "delta": delta,
                        "pendiente_uv_por_codigo": pendiente,
                        "pendiente_mv_fisicos_por_codigo": (
                            None if pendiente is None else
                            FACTOR * pendiente / 1000.0),
                    })
                valores = [p["pendiente_uv_por_codigo"] for p in por_delta
                           if p["pendiente_uv_por_codigo"] is not None]
                if not valores:
                    continue
                mediana = statistics.median(valores)
                pendientes_locales.append({
                    "pasada": pasada, "idac1": idac1,
                    "pendientes_por_delta": por_delta,
                    "pendiente_mediana_uv_por_codigo": mediana,
                    "pendiente_mediana_mv_fisicos_por_codigo": (
                        FACTOR * mediana / 1000.0),
                    "signo": ("positivo" if mediana > 0 else
                              ("negativo" if mediana < 0 else "nulo")),
                })

        datos_ajuste: list[tuple[float, float, float, float]] = []
        descarte = {
            "candidatas": len(malla), "incluidas": 0,
            "descartadas_total": 0,
            "descartadas_por_motivo": {
                **{f"ch{ch}_sin_lectura": 0 for ch in range(4)},
                **{f"ch{ch}_fuera_de_rango": 0 for ch in range(4)},
                **{f"ch{ch}_clavado": 0 for ch in range(4)},
                "error_ch2_mayor_o_igual_1500_mv": 0,
            },
            "nota": "los motivos pueden solaparse en una misma captura",
        }
        for captura in malla:
            motivos: set[str] = set()
            for ch in range(4):
                lectura = captura["lecturas"].get(ch)
                if lectura is None or not lectura.ok or lectura.media_uv is None:
                    motivos.add(f"ch{ch}_sin_lectura")
                    continue
                if not lectura.valida:
                    motivos.add(f"ch{ch}_fuera_de_rango")
                if lectura.clavada:
                    motivos.add(f"ch{ch}_clavado")
            ch2 = captura["lecturas"].get(2)
            if ch2 is not None and ch2.media_uv is not None:
                error_ch2_mv = FACTOR * (ch2.media_uv / 1000.0 - BANCO_VREF_MV)
                if abs(error_ch2_mv) >= 1500.0:
                    motivos.add("error_ch2_mayor_o_igual_1500_mv")
            if motivos:
                descarte["descartadas_total"] += 1
                for motivo in motivos:
                    descarte["descartadas_por_motivo"][motivo] += 1
                continue
            ch0 = captura["lecturas"][0]
            datos_ajuste.append((float(captura["idac2"]),
                                 float(captura["idac1"]),
                                 ch0.media_uv, ch2.media_uv))
            descarte["incluidas"] += 1
        ajuste_malla = minimos_cuadrados(datos_ajuste)

        # Se conserva el calculo sin limitar para mostrar, no ocultar, por que
        # el ajuste global anterior era enganoso frente a las derivadas ABBA.
        datos_sin_limitar: list[tuple[float, float, float, float]] = []
        for captura in malla:
            ch0 = captura["lecturas"][0]
            ch2 = captura["lecturas"][2]
            if (ch0.valida and ch2.valida and ch0.media_uv is not None and
                    ch2.media_uv is not None):
                datos_sin_limitar.append((float(captura["idac2"]),
                                          float(captura["idac1"]),
                                          ch0.media_uv, ch2.media_uv))
        ajuste_sin_limitar = minimos_cuadrados(datos_sin_limitar)

        histeresis_celdas: list[dict[str, Any]] = []
        for idac1 in IDAC1_IDA:
            for idac2 in IDAC2_MALLA:
                por_pasada = {c["pasada"]: c for c in malla
                              if c["idac1"] == idac1 and c["idac2"] == idac2}
                if 1 not in por_pasada or 2 not in por_pasada:
                    continue
                celda: dict[str, Any] = {"idac1": idac1, "idac2": idac2}
                hay_diferencia = False
                for ch in (2, 3):
                    a = por_pasada[1]["lecturas"][ch]
                    b = por_pasada[2]["lecturas"][ch]
                    diferencia = None
                    if (a.valida and b.valida and a.media_uv is not None and
                            b.media_uv is not None):
                        diferencia = b.media_uv - a.media_uv
                        hay_diferencia = True
                    celda[f"ch{ch}_pasada2_menos_pasada1_uv"] = diferencia
                if hay_diferencia:
                    histeresis_celdas.append(celda)
        histeresis_resumen: dict[str, Any] = {}
        for ch in (2, 3):
            valores = [float(c[f"ch{ch}_pasada2_menos_pasada1_uv"])
                       for c in histeresis_celdas
                       if c[f"ch{ch}_pasada2_menos_pasada1_uv"] is not None]
            histeresis_resumen[f"ch{ch}"] = {
                "n": len(valores),
                "media_uv": statistics.mean(valores) if valores else None,
                "rms_uv": (math.sqrt(sum(v * v for v in valores) / len(valores))
                           if valores else None),
                "max_abs_uv": max((abs(v) for v in valores), default=None),
            }
        return {
            "codigo_idac0_final": codigo_idac0,
            "abba_idac0": resultado_abba_idac0,
            "tabla_validez_celdas": tabla_celdas,
            "pendiente_local_idac2_sobre_ch2": pendientes_locales,
            "ajuste_malla": ajuste_malla,
            "filtro_region_lineal": descarte,
            "ajuste_malla_sin_limitar": ajuste_sin_limitar,
            "advertencia_ajuste_global": (
                "El ajuste historico sobre todo el mapa dio un residuo de "
                "aproximadamente 103 mV y coeficientes casi a la mitad de los "
                "ABBA. No debe interpretarse como la ganancia local del sumador."
            ),
            "histeresis_por_celda": histeresis_celdas,
            "histeresis_resumen": histeresis_resumen,
            "filas": len(filas),
            "filas_sin_lectura": sum(not bool(f["ok"]) for f in filas),
            "filas_saturadas": sum(bool(f["ok"]) and not bool(f["valido"])
                                    for f in filas),
        }

    try:
        if not console.open(wait_ready=True, timeout=25.0):
            raise RuntimeError("la consola no anuncio que estaba lista")
        lab = LaboratorioSimulado(reloj) if args.simular else Lab(Session(console))  # type: ignore[arg-type]
        estado = "configurando"
        evento("inicio", "campana iniciada")
        operacion_comprobada(lambda: lab.set_adc_config(1), "ADC +/-2,5 V")
        operacion_comprobada(lambda: lab.set_gain("pga", GAIN_CODES.index(args.pga)),
                             f"PGA x{args.pga}")
        operacion_comprobada(
            lambda: lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout)),
            f"PGAout x{args.pgaout}")
        for etapa in range(4):
            operacion_comprobada(lambda e=etapa: lab.set_idac(e, 0),
                                 f"IDAC{etapa}=0")
        progreso("puesta a punto; asentando referencias")
        asentamiento_inicial_incompleto = esperar_asentamiento_idac1(0, 0)

        estado = "centrando_idac0"
        codigo_idac0 = centrar_idac0()
        progreso(f"IDAC0 congelado en {codigo_idac0:+d}; IDAC3 queda en cero")

        estado = "abba_idac0"
        resultado_abba_idac0 = medir_abba_idac0(
            codigo_idac0, asentamiento_inicial_incompleto)
        progreso("brazo ABBA de IDAC0 terminado; IDAC0 restaurado al centro")

        estado = "malla"
        secuencias = (IDAC1_IDA, tuple(reversed(IDAC1_IDA)))
        for pasada, codigos_idac1 in enumerate(secuencias, start=1):
            evento("pasada", f"comienza pasada {pasada}")
            for indice_i1, idac1 in enumerate(codigos_idac1, start=1):
                # IDAC2 vuelve a Vref antes de mover la referencia lenta; sus
                # 1,5 s quedan holgadamente contenidos en los 90 s siguientes.
                operacion_comprobada(lambda: lab.set_idac(2, 0), "IDAC2=0")
                operacion_comprobada(lambda c=idac1: lab.set_idac(1, c),
                                     f"IDAC1={idac1:+d}")
                evento("idac1", f"IDAC1={idac1:+d}; comienza espera lenta",
                       pasada=pasada, idac1=idac1)
                asentamiento_incompleto = esperar_asentamiento_idac1(pasada, idac1)
                medir_canales(pasada, codigo_idac0, idac1, 0, "referencia",
                              asentamiento_incompleto=asentamiento_incompleto)
                progreso(f"pasada {pasada}/2, IDAC1 {indice_i1}/9={idac1:+d}, referencia")

                capturas_malla_punto: list[dict[str, Any]] = []
                for idac2 in IDAC2_MALLA:
                    operacion_comprobada(lambda c=idac2: lab.set_idac(2, c),
                                         f"IDAC2={idac2:+d}")
                    reloj.esperar(ESPERA_IDAC_RAPIDO_S)
                    captura = medir_canales(
                        pasada, codigo_idac0, idac1, idac2, "malla",
                        asentamiento_incompleto=asentamiento_incompleto)
                    capturas_malla_punto.append(captura)
                    progreso(f"pasada {pasada}/2, IDAC1={idac1:+d}, IDAC2={idac2:+d}")

                candidatos = []
                for captura in capturas_malla_punto:
                    lectura = captura["lecturas"][2]
                    if lectura.ok and lectura.media_uv is not None:
                        candidatos.append((abs(lectura.media_uv - BANCO_VREF_MV * 1000.0),
                                           captura, lectura))
                if candidatos:
                    _, mejor, lectura_mejor = min(candidatos, key=lambda item: item[0])
                    if lectura_mejor.valida and not args.sin_fino:
                        centro_original = int(mejor["idac2"])
                        centro = max(-255 + max(DELTAS_ABBA),
                                     min(255 - max(DELTAS_ABBA), centro_original))
                        evento("tramo_abba", f"ABBA alrededor de IDAC2={centro:+d}",
                               pasada=pasada, idac1=idac1,
                               centro_idac2=centro,
                               centro_malla_original=centro_original)
                        for delta in DELTAS_ABBA:
                            secuencia = (("A1", centro - delta),
                                         ("B1", centro + delta),
                                         ("B2", centro + delta),
                                         ("A2", centro - delta))
                            for posicion, idac2_abba in secuencia:
                                operacion_comprobada(
                                    lambda c=idac2_abba: lab.set_idac(2, c),
                                    f"IDAC2 ABBA={idac2_abba:+d}")
                                reloj.esperar(ESPERA_IDAC_RAPIDO_S)
                                medir_canales(
                                    pasada, codigo_idac0, idac1, idac2_abba,
                                    "abba", posicion, asentamiento_incompleto,
                                    delta=delta, centro_idac2=centro,
                                )
                    elif args.sin_fino:
                        evento("tramo_abba_omitido", "se solicito --sin-fino",
                               pasada=pasada, idac1=idac1)
                    else:
                        evento("tramo_abba_omitido",
                               "el punto de malla mas cercano a Vref estaba saturado",
                               pasada=pasada, idac1=idac1,
                               centro_idac2=int(mejor["idac2"]))
                else:
                    evento("tramo_abba_omitido", "ch2 no tuvo lecturas en la malla",
                           pasada=pasada, idac1=idac1)

            operacion_comprobada(lambda: lab.set_idac(1, 0), "IDAC1=0 al cerrar pasada")
            operacion_comprobada(lambda: lab.set_idac(2, 0), "IDAC2=0 al cerrar pasada")
            evento("pasada", f"pasada {pasada} terminada; IDAC1 e IDAC2 en cero")

        resumen = crear_resumen()
        estado = "completada"
        evento("resumen", "campana completada")
        completada = True
    except (PlazoAgotado, KeyboardInterrupt) as exc:
        error_final = "interrupcion de teclado" if isinstance(exc, KeyboardInterrupt) else str(exc)
        estado = "abortada"
        evento("aborto", error_final)
    except Exception as exc:
        error_final = f"{type(exc).__name__}: {exc}"
        estado = "abortada"
        evento("error", error_final)
    finally:
        if lab is not None:
            try:
                lab.set_gain("pgaout", GAIN_CODES.index(1))
            except Exception:
                pass
            for etapa in range(4):
                try:
                    lab.set_idac(etapa, 0)
                except Exception:
                    pass
        try:
            console.close()
        finally:
            archivo.flush()
            archivo.close()
            guardar_json()

    if completada and resumen is not None:
        print("\nRESUMEN FINAL", flush=True)
        print(f"  IDAC0 final: {codigo_idac0:+d}", flush=True)
        brazo = resumen["abba_idac0"]
        print("  Brazo ABBA IDAC0:", flush=True)
        for dato in brazo["pendientes_por_delta"]:
            p0 = dato["pendiente_ch0_mv_fisicos_por_codigo"]
            p2 = dato["pendiente_ch2_mv_fisicos_por_codigo"]
            texto_p0 = "sin dato" if p0 is None else f"{p0:+.4f} mV/codigo"
            texto_p2 = "sin dato" if p2 is None else f"{p2:+.4f} mV/codigo"
            print(f"    delta={dato['delta']}: ch0={texto_p0}, ch2={texto_p2}",
                  flush=True)
        ganancia_seo = brazo["ganancia_sumador_desde_seo"]
        print("    ganancia sumador desde SEo: " +
              ("sin dato" if ganancia_seo is None else f"{ganancia_seo:+.5f}"),
              flush=True)
        print("  Validez por celda (2=ch2 valido, 3=ch3 valido, -=saturado/sin dato):",
              flush=True)
        tabla = resumen["tabla_validez_celdas"]
        for pasada in (1, 2):
            for idac1 in (IDAC1_IDA if pasada == 1 else tuple(reversed(IDAC1_IDA))):
                celdas = [c for c in tabla if c["pasada"] == pasada and c["idac1"] == idac1]
                texto = " ".join(
                    f"{c['idac2']:+d}:{'2' if c['ch2_valido'] else '-'}"
                    f"{'3' if c['ch3_valido'] else '-'}" for c in celdas)
                print(f"    P{pasada} IDAC1={idac1:+4d}  {texto}", flush=True)
        print("  Pendiente local IDAC2 -> ch2:", flush=True)
        for pendiente in resumen["pendiente_local_idac2_sobre_ch2"]:
            detalles = ", ".join(
                f"d={p['delta']}:" + (
                    "sin dato" if p["pendiente_mv_fisicos_por_codigo"] is None
                    else f"{p['pendiente_mv_fisicos_por_codigo']:+.4f}")
                for p in pendiente["pendientes_por_delta"])
            print(f"    P{pendiente['pasada']} IDAC1={pendiente['idac1']:+4d}: "
                  f"{detalles}; mediana="
                  f"{pendiente['pendiente_mediana_mv_fisicos_por_codigo']:+.4f} "
                  "mV/codigo "
                  f"({pendiente['signo']})", flush=True)
        ajuste = resumen["ajuste_malla"]
        if ajuste["disponible"]:
            print(f"  Ajuste region lineal ({ajuste['n']} capturas): "
                  f"A={ajuste['A_uv_por_codigo_idac2']:+.3f}, "
                  f"B={ajuste['B_uv_por_codigo_idac1']:+.3f}, "
                  f"C={ajuste['C_uv_por_uv_ch0']:+.5f}, K={ajuste['K_uv']:+.1f} uV, "
                  f"RMS={ajuste['residuo_rms_uv']:.2f} uV", flush=True)
        else:
            print(f"  Ajuste no disponible: {ajuste['motivo']}", flush=True)
        filtro = resumen["filtro_region_lineal"]
        print(f"  Filtro del ajuste: {filtro['incluidas']} dentro, "
              f"{filtro['descartadas_total']} descartadas de {filtro['candidatas']}",
              flush=True)
        for motivo, cantidad in filtro["descartadas_por_motivo"].items():
            print(f"    {motivo}: {cantidad}", flush=True)
        print(f"  ADVERTENCIA: {resumen['advertencia_ajuste_global']}", flush=True)
        h2 = resumen["histeresis_resumen"]["ch2"]
        h3 = resumen["histeresis_resumen"]["ch3"]
        print(f"  Histeresis RMS: ch2={h2['rms_uv']:.1f} uV, ch3={h3['rms_uv']:.1f} uV",
              flush=True)
        print(f"  filas={resumen['filas']}, sin lectura={resumen['filas_sin_lectura']}, "
              f"saturadas={resumen['filas_saturadas']}", flush=True)
        print(f"  CSV: {csv_path}\n  JSON: {json_path}", flush=True)
        return 0
    print(f"Campana abortada: {error_final or 'causa desconocida'}", flush=True)
    print(f"Evidencia parcial: {csv_path} y {json_path}", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
