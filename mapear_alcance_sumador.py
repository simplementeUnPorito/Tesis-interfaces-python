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
DESPLAZAMIENTOS_FINOS = (-8, -4, -2, 0, 2, 4, 8)
VENTANAS_POR_LECTURA = 3
REINTENTOS_POR_VENTANA = 4
ESPERA_IDAC_LENTO_S = 90.0
ESPERA_IDAC_RAPIDO_S = 1.5
ESPERA_CENTRADO_S = 2.0
ESTIMADO_TOTAL_S = 45.0 * 60.0

CAMPOS = (
    "t_s", "pasada", "idac0", "idac1", "idac2", "fase", "ch", "tap",
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
    ap.add_argument("--simular", action="store_true")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    if args.max_s <= 0:
        ap.error("--max-s debe ser positivo")

    csv_path, json_path = resolver_salidas(args.output, args.simular)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    reloj = RelojCampana(args.max_s, args.simular)
    eventos: list[dict[str, Any]] = []
    filas: list[dict[str, Any]] = []
    capturas: list[dict[str, Any]] = []
    resumen: dict[str, Any] | None = None
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

    def medir_cinco(pasada: int, idac1: int, idac2: int,
                    fase: str) -> dict[str, Any]:
        captura: dict[str, Any] = {"pasada": pasada, "idac1": idac1,
                                   "idac2": idac2, "fase": fase,
                                   "lecturas": {}}
        for ch in CANALES:
            lectura = medir(ch)
            captura["lecturas"][ch] = lectura
            real_mv = None
            if lectura.media_uv is not None:
                real_mv = FACTOR * (lectura.media_uv / 1000.0 - BANCO_VREF_MV)
            escribir_fila({
                "t_s": round(reloj.transcurrido(), 3), "pasada": pasada,
                "idac0": codigo_idac0, "idac1": idac1, "idac2": idac2,
                "fase": fase, "ch": ch, "tap": TAP_NAMES[ch],
                "media_banco_uv": lectura.media_uv,
                "pp_banco_uv": lectura.pp_uv, "ok": lectura.ok,
                "valido": lectura.valida, "clavado": lectura.clavada,
                "real_mv_vs_vref": real_mv,
            })
        capturas.append(captura)
        return captura

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

        pendientes_pasada: list[dict[str, Any]] = []
        for pasada in (1, 2):
            for idac1 in IDAC1_IDA:
                puntos = []
                for captura in capturas:
                    if (captura["fase"] == "fino" and captura["pasada"] == pasada and
                            captura["idac1"] == idac1):
                        lectura = captura["lecturas"][2]
                        if lectura.valida and lectura.media_uv is not None:
                            puntos.append((float(captura["idac2"]), lectura.media_uv))
                ajuste = regresion_lineal(puntos)
                if ajuste is not None:
                    pendientes_pasada.append({"pasada": pasada, "idac1": idac1,
                                               **ajuste})
        pendientes_locales: list[dict[str, Any]] = []
        for idac1 in IDAC1_IDA:
            ajustes = [p for p in pendientes_pasada if p["idac1"] == idac1]
            if not ajustes:
                continue
            valores = [float(p["pendiente_uv_por_codigo"]) for p in ajustes]
            media = statistics.mean(valores)
            desvio = statistics.stdev(valores) if len(valores) > 1 else 0.0
            pendientes_locales.append({
                "idac1": idac1, "pendiente_media_uv_por_codigo": media,
                "signo": "positivo" if media > 0 else ("negativo" if media < 0 else "nulo"),
                "desvio_entre_pasadas_uv_por_codigo": desvio,
                "ajustes_por_pasada": ajustes,
            })

        datos_ajuste: list[tuple[float, float, float, float]] = []
        for captura in malla:
            ch0 = captura["lecturas"][0]
            ch2 = captura["lecturas"][2]
            if ch0.valida and ch2.valida and ch0.media_uv is not None and ch2.media_uv is not None:
                datos_ajuste.append((float(captura["idac2"]),
                                     float(captura["idac1"]),
                                     ch0.media_uv, ch2.media_uv))
        ajuste_malla = minimos_cuadrados(datos_ajuste)

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
            "tabla_validez_celdas": tabla_celdas,
            "pendiente_local_idac2_sobre_ch2": pendientes_locales,
            "ajuste_malla": ajuste_malla,
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
        reloj.esperar(ESPERA_IDAC_LENTO_S)

        estado = "centrando_idac0"
        codigo_idac0 = centrar_idac0()
        progreso(f"IDAC0 congelado en {codigo_idac0:+d}; IDAC3 queda en cero")

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
                reloj.esperar(ESPERA_IDAC_LENTO_S)
                medir_cinco(pasada, idac1, 0, "referencia")
                progreso(f"pasada {pasada}/2, IDAC1 {indice_i1}/9={idac1:+d}, referencia")

                capturas_malla_punto: list[dict[str, Any]] = []
                for idac2 in IDAC2_MALLA:
                    operacion_comprobada(lambda c=idac2: lab.set_idac(2, c),
                                         f"IDAC2={idac2:+d}")
                    reloj.esperar(ESPERA_IDAC_RAPIDO_S)
                    captura = medir_cinco(pasada, idac1, idac2, "malla")
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
                    if lectura_mejor.valida:
                        centro = int(mejor["idac2"])
                        codigos_finos: list[int] = []
                        for desplazamiento in DESPLAZAMIENTOS_FINOS:
                            codigo = max(-255, min(255, centro + desplazamiento))
                            if codigo not in codigos_finos:
                                codigos_finos.append(codigo)
                        evento("tramo_fino", f"tramo fino alrededor de IDAC2={centro:+d}",
                               pasada=pasada, idac1=idac1, centro_idac2=centro)
                        for idac2 in codigos_finos:
                            operacion_comprobada(lambda c=idac2: lab.set_idac(2, c),
                                                 f"IDAC2 fino={idac2:+d}")
                            reloj.esperar(ESPERA_IDAC_RAPIDO_S)
                            medir_cinco(pasada, idac1, idac2, "fino")
                    else:
                        evento("tramo_fino_omitido",
                               "el punto de malla mas cercano a Vref estaba saturado",
                               pasada=pasada, idac1=idac1,
                               centro_idac2=int(mejor["idac2"]))
                else:
                    evento("tramo_fino_omitido", "ch2 no tuvo lecturas en la malla",
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
            print(f"    IDAC1={pendiente['idac1']:+4d}: "
                  f"{pendiente['pendiente_media_uv_por_codigo']:+.2f} uV/codigo "
                  f"({pendiente['signo']}), desvio entre pasadas "
                  f"{pendiente['desvio_entre_pasadas_uv_por_codigo']:.2f}", flush=True)
        ajuste = resumen["ajuste_malla"]
        if ajuste["disponible"]:
            print(f"  Ajuste ({ajuste['n']} filas): A={ajuste['A_uv_por_codigo_idac2']:+.3f}, "
                  f"B={ajuste['B_uv_por_codigo_idac1']:+.3f}, "
                  f"C={ajuste['C_uv_por_uv_ch0']:+.5f}, K={ajuste['K_uv']:+.1f} uV, "
                  f"RMS={ajuste['residuo_rms_uv']:.2f} uV", flush=True)
        else:
            print(f"  Ajuste no disponible: {ajuste['motivo']}", flush=True)
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
