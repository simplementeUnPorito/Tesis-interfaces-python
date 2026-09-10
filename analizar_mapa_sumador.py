"""Analiza, sin acceder al hardware, el mapa parcial IDAC1/IDAC2 del sumador.

El argumento posicional es el CSV producido por ``mapear_alcance_sumador.py``.
El informe usa exclusivamente el CSV y, si existe, su JSON adyacente. No
importa ningun modulo de comunicacion con la placa.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from escala_banco import (BANCO_MAX_VALIDO_MV, BANCO_MIN_VALIDO_MV,
                          BANCO_VREF_MV, FACTOR, clavado, lectura_valida)


RAIZ_REPO = Path(__file__).resolve().parents[3]
CSV_PREDETERMINADO = (
    RAIZ_REPO / "lab" / "calibracion_nueva" /
    "mapear_alcance_sumador_20260910_122631.csv"
)
INFORME_PREDETERMINADO = (
    RAIZ_REPO / "lab" / "RESULTADOS_MAPA_SUMADOR_2026-09-10.md"
)

IDAC1_PLANEADOS = (-255, -192, -128, -64, 0, 64, 128, 192, 255)
IDAC2_MALLA = (-255, -128, -64, 0, 64, 128, 255)
PASADAS_PLANEADAS = (1, 2)
CANALES = (0, 1, 2, 3, 4)
NOMBRES = {
    0: "PGAgain (SEo)",
    1: "BPo",
    2: "OPA_SUMo",
    3: "SUMo",
    4: "LPo",
}
A_PREVIO_MV_POR_CODIGO = 9.56
RMS_PREVIO_MV = 15.0


@dataclass(frozen=True)
class Lectura:
    linea: int
    t_s: float
    pasada: int
    idac0: int
    idac1: int
    idac2: int
    fase: str
    ch: int
    tap: str
    media_uv: float | None
    pp_uv: float | None
    ok: bool
    valido: bool
    esta_clavado: bool
    real_mv_vs_vref: float | None

    @property
    def vivo(self) -> bool:
        return self.ok and self.valido and not self.esta_clavado

    @property
    def banco_mv(self) -> float | None:
        return None if self.media_uv is None else self.media_uv / 1000.0


@dataclass
class Captura:
    pasada: int
    idac1: int
    idac2: int
    fase: str
    lecturas: dict[int, Lectura]

    @property
    def completa(self) -> bool:
        return set(self.lecturas) == set(CANALES)


def booleano(texto: str) -> bool:
    normalizado = texto.strip().lower()
    if normalizado in {"true", "1", "si", "sí"}:
        return True
    if normalizado in {"false", "0", "no", ""}:
        return False
    raise ValueError(f"booleano no reconocido: {texto!r}")


def flotante(texto: str) -> float | None:
    texto = texto.strip()
    return None if not texto else float(texto)


def cargar_csv(ruta: Path) -> list[Lectura]:
    filas: list[Lectura] = []
    with ruta.open(newline="", encoding="utf-8-sig") as archivo:
        lector = csv.DictReader(archivo)
        esperadas = {
            "t_s", "pasada", "idac0", "idac1", "idac2", "fase", "ch",
            "tap", "media_banco_uv", "pp_banco_uv", "ok", "valido",
            "clavado", "real_mv_vs_vref",
        }
        faltantes = esperadas - set(lector.fieldnames or ())
        if faltantes:
            raise ValueError("faltan columnas: " + ", ".join(sorted(faltantes)))
        for linea, fila in enumerate(lector, start=2):
            lectura = Lectura(
                linea=linea,
                t_s=float(fila["t_s"]),
                pasada=int(fila["pasada"]),
                idac0=int(fila["idac0"]),
                idac1=int(fila["idac1"]),
                idac2=int(fila["idac2"]),
                fase=fila["fase"].strip(),
                ch=int(fila["ch"]),
                tap=fila["tap"].strip(),
                media_uv=flotante(fila["media_banco_uv"]),
                pp_uv=flotante(fila["pp_banco_uv"]),
                ok=booleano(fila["ok"]),
                valido=booleano(fila["valido"]),
                esta_clavado=booleano(fila["clavado"]),
                real_mv_vs_vref=flotante(fila["real_mv_vs_vref"]),
            )
            if lectura.ch not in CANALES:
                raise ValueError(f"canal inesperado en fila {linea}: {lectura.ch}")
            # El informe toma los flags grabados como evidencia. Esta comprobacion
            # impide analizarlos con una escala distinta a la que los genero.
            if lectura.ok and lectura.banco_mv is not None:
                if lectura.valido != lectura_valida(lectura.banco_mv):
                    raise ValueError(f"valido no coincide con escala_banco.py en fila {linea}")
                if lectura.esta_clavado != clavado(lectura.pp_uv):
                    raise ValueError(f"clavado no coincide con escala_banco.py en fila {linea}")
            filas.append(lectura)
    if not filas:
        raise ValueError("el CSV no contiene filas de datos")
    return filas


def agrupar_capturas(filas: Sequence[Lectura]) -> list[Captura]:
    grupos: dict[tuple[int, int, int, str], dict[int, Lectura]] = {}
    orden: list[tuple[int, int, int, str]] = []
    for lectura in filas:
        clave = (lectura.pasada, lectura.idac1, lectura.idac2, lectura.fase)
        if clave not in grupos:
            grupos[clave] = {}
            orden.append(clave)
        if lectura.ch in grupos[clave]:
            anterior = grupos[clave][lectura.ch]
            raise ValueError(
                f"canal {lectura.ch} duplicado para {clave}, filas "
                f"{anterior.linea} y {lectura.linea}"
            )
        grupos[clave][lectura.ch] = lectura
    return [Captura(*clave, grupos[clave]) for clave in orden]


def cargar_json_adyacente(ruta_csv: Path) -> dict[str, object] | None:
    ruta = ruta_csv.with_suffix(".json")
    if not ruta.exists():
        return None
    with ruta.open(encoding="utf-8") as archivo:
        dato = json.load(archivo)
    return dato if isinstance(dato, dict) else None


def resolver_sistema(matriz: list[list[float]], vector: list[float]) -> list[float] | None:
    """Eliminacion gaussiana con pivoteo parcial."""
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


def regresion_lineal(puntos: Sequence[tuple[float, float]]) -> dict[str, float | int] | None:
    if len(puntos) < 2:
        return None
    xs = [p[0] for p in puntos]
    ys = [p[1] for p in puntos]
    xm = statistics.mean(xs)
    ym = statistics.mean(ys)
    sxx = sum((x - xm) ** 2 for x in xs)
    if sxx == 0.0:
        return None
    pendiente = sum((x - xm) * (y - ym) for x, y in puntos) / sxx
    intercepto = ym - pendiente * xm
    residuos = [y - (pendiente * x + intercepto) for x, y in puntos]
    ss_res = sum(r * r for r in residuos)
    ss_tot = sum((y - ym) ** 2 for y in ys)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else 1.0
    return {
        "n": len(puntos),
        "pendiente": pendiente,
        "intercepto": intercepto,
        "rms": math.sqrt(ss_res / len(puntos)),
        "r2": r2,
    }


def minimos_cuadrados(muestras: Sequence[tuple[float, float, float, float]]) -> dict[str, object]:
    """Ajusta y=A*c2+B*c1+C*ch0+K con listas y ecuaciones normales."""
    if len(muestras) < 4:
        return {"disponible": False, "n": len(muestras), "motivo": "menos de cuatro filas"}
    columnas = [[m[i] for m in muestras] for i in range(3)]
    medias = [statistics.mean(columna) for columna in columnas]
    escalas: list[float] = []
    for columna, media in zip(columnas, medias):
        escala = math.sqrt(sum((v - media) ** 2 for v in columna) / len(columna))
        if escala < 1e-12:
            return {"disponible": False, "n": len(muestras), "motivo": "predictor sin variacion"}
        escalas.append(escala)
    diseno = [
        [(m[i] - medias[i]) / escalas[i] for i in range(3)] + [1.0]
        for m in muestras
    ]
    ys = [m[3] for m in muestras]
    gram = [[sum(f[i] * f[j] for f in diseno) for j in range(4)] for i in range(4)]
    termino = [sum(f[i] * y for f, y in zip(diseno, ys)) for i in range(4)]
    beta = resolver_sistema(gram, termino)
    if beta is None:
        return {"disponible": False, "n": len(muestras), "motivo": "sistema singular"}
    coef = [beta[i] / escalas[i] for i in range(3)]
    intercepto = beta[3] - sum(coef[i] * medias[i] for i in range(3))
    residuos = [
        m[3] - (coef[0] * m[0] + coef[1] * m[1] + coef[2] * m[2] + intercepto)
        for m in muestras
    ]
    return {
        "disponible": True,
        "n": len(muestras),
        "A": coef[0],
        "B": coef[1],
        "C": coef[2],
        "K": intercepto,
        "rms": math.sqrt(sum(r * r for r in residuos) / len(residuos)),
    }


def fmt(numero: float | int | None, decimales: int = 1, signo: bool = False) -> str:
    if numero is None:
        return "—"
    patron = f"{{:{'+' if signo else ''}.{decimales}f}}"
    return patron.format(float(numero)).replace(".", ",")


def fmt_entero(numero: int, signo: bool = False) -> str:
    return f"{numero:+d}" if signo else str(numero)


def duracion(segundos: float) -> str:
    minutos = int(segundos // 60)
    resto = segundos - 60 * minutos
    return f"{minutos} min {fmt(resto, 1)} s"


def signo(valor: float) -> str:
    if valor > 0.0:
        return "positivo"
    if valor < 0.0:
        return "negativo"
    return "nulo"


def lineas_csv(lecturas: Iterable[Lectura]) -> str:
    numeros = sorted({lectura.linea for lectura in lecturas})
    return ", ".join(str(n) for n in numeros) if numeros else "—"


def estado_canal(lectura: Lectura | None) -> str:
    if lectura is None or not lectura.ok or lectura.media_uv is None:
        return "—"
    if not lectura.valido:
        return "F*" if lectura.esta_clavado else "F"
    return "C" if lectura.esta_clavado else "V"


def tabla_markdown(encabezados: Sequence[str], filas: Sequence[Sequence[str]]) -> list[str]:
    resultado = ["| " + " | ".join(encabezados) + " |"]
    resultado.append("| " + " | ".join("---" for _ in encabezados) + " |")
    resultado.extend("| " + " | ".join(fila) + " |" for fila in filas)
    return resultado


def captura_malla(capturas: Sequence[Captura], pasada: int,
                  idac1: int, idac2: int) -> Captura | None:
    return next((c for c in capturas if c.fase == "malla" and
                 c.pasada == pasada and c.idac1 == idac1 and c.idac2 == idac2), None)


def generar_informe(ruta_csv: Path, filas: list[Lectura], capturas: list[Captura],
                    metadatos: dict[str, object] | None) -> str:
    informe: list[str] = [
        "# Resultados del mapa parcial del sumador — 2026-09-10",
        "",
        f"Fuente: `{ruta_csv.as_posix()}`. Las filas CSV citadas cuentan el encabezado como fila 1. "
        "Los valores eléctricos de los ajustes son desviaciones físicas respecto de Vref, tomadas "
        "de `real_mv_vs_vref`; los límites y la detección de clavado provienen de `escala_banco.py`.",
        "",
        "## 1. Qué se corrió y hasta dónde",
        "",
    ]

    malla = [c for c in capturas if c.fase == "malla"]
    finos = [c for c in capturas if c.fase == "fino"]
    referencias = [c for c in capturas if c.fase == "referencia"]
    celdas_malla_completas = [c for c in malla if c.completa]
    total_malla = len(PASADAS_PLANEADAS) * len(IDAC1_PLANEADOS) * len(IDAC2_MALLA)
    bloques_completos = 0
    for pasada in PASADAS_PLANEADAS:
        for idac1 in IDAC1_PLANEADOS:
            if all(captura_malla(capturas, pasada, idac1, idac2) is not None and
                   captura_malla(capturas, pasada, idac1, idac2).completa
                   for idac2 in IDAC2_MALLA):
                bloques_completos += 1
    t_min = min(f.t_s for f in filas)
    t_max = max(f.t_s for f in filas)
    pasadas_presentes = sorted({f.pasada for f in filas})
    idac0 = sorted({f.idac0 for f in filas})
    proporcion = 100.0 * len(celdas_malla_completas) / total_malla
    resumen_corrida = [
        ["Configuración", f"PGA x{metadatos.get('pga', 50) if metadatos else 50}; "
         f"PGAout x{metadatos.get('pgaout', 1) if metadatos else 1}; ADC ±2,5 V; "
         f"IDAC0={','.join(fmt_entero(x, True) for x in idac0)}; IDAC3=0"],
        ["Filas de canal", str(len(filas))],
        ["Capturas completas de cinco canales", f"{sum(c.completa for c in capturas)} de {len(capturas)} registradas"],
        ["Celdas de malla gruesa", f"{len(celdas_malla_completas)} de {total_malla} planeadas ({fmt(proporcion, 1)} %)"],
        ["Bloques IDAC1 completos", f"{bloques_completos} de {len(PASADAS_PLANEADAS) * len(IDAC1_PLANEADOS)}"],
        ["Capturas adicionales", f"{len(referencias)} de referencia y {len(finos)} del tramo fino"],
        ["Pasadas con filas", ", ".join(str(p) for p in pasadas_presentes)],
        ["Tiempo hasta la última fila", f"t_s={fmt(t_max, 3)} s ({duracion(t_max)})"],
        ["Intervalo cubierto por filas", f"{fmt(t_max - t_min, 3)} s ({duracion(t_max - t_min)})"],
    ]
    informe.extend(tabla_markdown(["Magnitud", "Resultado"], resumen_corrida))
    informe.append("")
    corrida_completa = len(celdas_malla_completas) == total_malla
    if corrida_completa:
        informe.append("Las 126 celdas de la malla están presentes; el informe trata la corrida como completa.")
    else:
        ultima = max(filas, key=lambda f: f.t_s)
        informe.append(
            "La corrida fue cortada a propósito y no es una campaña completa. "
            f"La última fila persistida es la {ultima.linea}: pasada {ultima.pasada}, "
            f"IDAC1={fmt_entero(ultima.idac1, True)}, IDAC2={fmt_entero(ultima.idac2, True)}, "
            f"fase `{ultima.fase}`, ch{ultima.ch}, t_s={fmt(ultima.t_s, 3)} s."
        )
        if metadatos is not None:
            informe.append(
                f"El JSON adyacente quedó con `estado={metadatos.get('estado')}` y "
                f"`resumen={metadatos.get('resumen')}`; esto sólo se usa para describir el corte."
            )

    informe.extend(["", "## 2. Mapa de validez", ""])
    informe.append(
        "Leyenda por canal: **V** = válido y vivo; **C** = válido pero clavado; "
        "**F** = fuera de rango; **F\\*** = fuera de rango y además clavado; **—** = celda no medida. "
        "Cada celda se escribe como `2estado/3estado/4estado`."
    )
    for pasada in PASADAS_PLANEADAS:
        if not any(c.pasada == pasada and c.fase == "malla" for c in capturas):
            informe.extend(["", f"**Pasada {pasada}:** no hay filas de malla en el CSV."])
            continue
        tabla: list[list[str]] = []
        for idac1 in IDAC1_PLANEADOS:
            fila_tabla = [fmt_entero(idac1, True)]
            for idac2 in IDAC2_MALLA:
                captura = captura_malla(capturas, pasada, idac1, idac2)
                if captura is None:
                    fila_tabla.append("—")
                else:
                    fila_tabla.append("/".join(
                        f"{ch}{estado_canal(captura.lecturas.get(ch))}" for ch in (2, 3, 4)
                    ))
            tabla.append(fila_tabla)
        informe.extend(["", f"**Pasada {pasada}.**"])
        informe.extend(tabla_markdown(
            ["IDAC1 \\ IDAC2"] + [fmt_entero(c, True) for c in IDAC2_MALLA], tabla
        ))

    informe.extend(["", "## 3. La diagonal de soluciones", ""])
    informe.append(
        "Se buscó, por pasada e IDAC1, el menor `abs(real_mv_vs_vref)` de ch4 entre "
        "capturas `malla` y `fino` donde ch4 es válido y no está clavado. No se incluyó "
        "la captura `referencia`."
    )
    diagonal: list[tuple[int, int, Captura, Lectura]] = []
    filas_diagonal: list[list[str]] = []
    for pasada in sorted({c.pasada for c in capturas}):
        for idac1_actual in IDAC1_PLANEADOS:
            candidatos: list[tuple[float, float, Captura, Lectura]] = []
            for captura in capturas:
                if (captura.pasada == pasada and captura.idac1 == idac1_actual and
                        captura.fase in {"malla", "fino"}):
                    ch4 = captura.lecturas.get(4)
                    if ch4 is not None and ch4.vivo and ch4.real_mv_vs_vref is not None:
                        candidatos.append((abs(ch4.real_mv_vs_vref), ch4.t_s, captura, ch4))
            if not candidatos:
                if any(c.pasada == pasada and c.idac1 == idac1_actual for c in capturas):
                    filas_diagonal.append([
                        str(pasada), fmt_entero(idac1_actual, True), "no se puede", "—", "—", "—", "—"
                    ])
                continue
            _, _, captura, ch4 = min(candidatos, key=lambda x: (x[0], x[1]))
            diagonal.append((pasada, idac1_actual, captura, ch4))
            ch2 = captura.lecturas.get(2)
            filas_diagonal.append([
                str(pasada), fmt_entero(idac1_actual, True), fmt_entero(captura.idac2, True),
                captura.fase, fmt(ch4.real_mv_vs_vref, 1, True),
                fmt(ch2.real_mv_vs_vref if ch2 else None, 1, True),
                f"{ch4.linea}/{ch2.linea if ch2 else '—'}",
            ])
    informe.extend(tabla_markdown(
        ["Pasada", "IDAC1", "IDAC2 óptimo", "Fase", "Error ch4 (mV)",
         "Error ch2 (mV)", "Filas CSV ch4/ch2"], filas_diagonal
    ))
    informe.extend(["", "Ajuste lineal de los códigos óptimos:", ""])
    filas_recta: list[list[str]] = []
    textos_linealidad: list[str] = []
    for pasada in sorted({d[0] for d in diagonal}):
        puntos = [(float(d[1]), float(d[2].idac2)) for d in diagonal if d[0] == pasada]
        ajuste = regresion_lineal(puntos)
        if ajuste is None:
            filas_recta.append([str(pasada), str(len(puntos)), "—", "—", "—", "—"])
            textos_linealidad.append(f"En la pasada {pasada} no hay puntos suficientes para decidir linealidad.")
            continue
        filas_recta.append([
            str(pasada), str(ajuste["n"]), fmt(ajuste["pendiente"], 4, True),
            fmt(ajuste["intercepto"], 2, True), fmt(ajuste["rms"], 2),
            fmt(ajuste["r2"], 4),
        ])
        if int(ajuste["n"]) < 3:
            conclusion = "no se puede decidir con menos de tres puntos"
        elif float(ajuste["r2"]) >= 0.95:
            conclusion = "es aproximadamente lineal en los puntos observados"
        else:
            conclusion = "no cumple el criterio descriptivo R² ≥ 0,95"
        textos_linealidad.append(
            f"Pasada {pasada}: {conclusion}; la pendiente observada es "
            f"{fmt(ajuste['pendiente'], 4, True)} códigos de IDAC2 por código de IDAC1."
        )
    informe.extend(tabla_markdown(
        ["Pasada", "N", "Pendiente ΔIDAC2/ΔIDAC1", "Ordenada (códigos)",
         "RMS (códigos)", "R²"], filas_recta
    ))
    informe.append("")
    informe.extend(textos_linealidad)
    informe.append(
        "El umbral R² ≥ 0,95 es sólo un criterio descriptivo del conjunto observado; "
        "no prueba la relación fuera del sector medido."
    )

    informe.extend(["", "## 4. Pendientes locales", ""])
    filas_pendientes: list[list[str]] = []
    for pasada in sorted({c.pasada for c in finos}):
        for idac1_actual in IDAC1_PLANEADOS:
            grupo = [c for c in finos if c.pasada == pasada and c.idac1 == idac1_actual]
            if not grupo:
                continue
            fila_local = [str(pasada), fmt_entero(idac1_actual, True)]
            for ch in (2, 4):
                lecturas_ch = [c.lecturas[ch] for c in grupo if ch in c.lecturas and
                               c.lecturas[ch].vivo and c.lecturas[ch].real_mv_vs_vref is not None]
                puntos = [(float(l.idac2), float(l.real_mv_vs_vref)) for l in lecturas_ch
                          if l.real_mv_vs_vref is not None]
                ajuste = regresion_lineal(puntos)
                if ajuste is None:
                    fila_local.extend(["no se puede", "—", str(len(puntos)), lineas_csv(lecturas_ch)])
                else:
                    pendiente = float(ajuste["pendiente"])
                    fila_local.extend([
                        fmt(pendiente, 3, True), signo(pendiente),
                        f"{fmt(ajuste['rms'], 2)}; N={ajuste['n']}", lineas_csv(lecturas_ch),
                    ])
            filas_pendientes.append(fila_local)
    informe.extend(tabla_markdown(
        ["Pasada", "IDAC1", "ch2 mV/código", "Signo ch2", "RMS ch2 (mV); N",
         "Filas ch2", "ch4 mV/código", "Signo ch4", "RMS ch4 (mV); N", "Filas ch4"],
        filas_pendientes,
    ))
    informe.append("")
    informe.append(
        "`no se puede` significa que quedaron menos de dos lecturas vivas del canal en ese tramo; "
        "las filas fuera de rango o clavadas no entraron en la recta."
    )

    filas_bp: list[list[str]] = []
    for pasada in sorted({c.pasada for c in malla}):
        lecturas_bp = [c.lecturas[1] for c in malla if c.pasada == pasada and
                       1 in c.lecturas and c.lecturas[1].vivo and
                       c.lecturas[1].real_mv_vs_vref is not None]
        ajuste = regresion_lineal([
            (float(l.idac1), float(l.real_mv_vs_vref)) for l in lecturas_bp
            if l.real_mv_vs_vref is not None
        ])
        if ajuste is None:
            filas_bp.append([str(pasada), "—", "—", str(len(lecturas_bp)), lineas_csv(lecturas_bp)])
        else:
            filas_bp.append([
                str(pasada), fmt(ajuste["pendiente"], 4, True),
                fmt(ajuste["rms"], 2), str(ajuste["n"]), lineas_csv(lecturas_bp),
            ])
    informe.extend(["", "Pendiente de BPo (ch1) contra IDAC1 en la malla:", ""])
    informe.extend(tabla_markdown(
        ["Pasada", "Pendiente ch1 (mV/código)", "RMS (mV)", "N", "Filas CSV"], filas_bp
    ))
    informe.append(
        "El módulo de esta pendiente es el cambio de headroom de BPo por código de IDAC1; "
        "el signo indica hacia qué lado se desplaza."
    )

    informe.extend(["", "## 5. Ajuste del modelo", ""])
    informe.append(
        "Se ajustó `salida = A·IDAC2 + B·IDAC1 + C·ch0 + K` únicamente con capturas de "
        "la malla gruesa. La salida y ch0 están en mV físicos respecto de Vref. Para cada "
        "salida se exigió que esa salida y ch0 fueran válidos y vivos."
    )
    filas_modelo: list[list[str]] = []
    ajustes_modelo: dict[int, tuple[dict[str, object], list[Lectura]]] = {}
    for salida in (2, 4):
        muestras: list[tuple[float, float, float, float]] = []
        usadas: list[Lectura] = []
        for captura in malla:
            ch0 = captura.lecturas.get(0)
            y = captura.lecturas.get(salida)
            if (ch0 is None or y is None or not ch0.vivo or not y.vivo or
                    ch0.real_mv_vs_vref is None or y.real_mv_vs_vref is None):
                continue
            muestras.append((float(captura.idac2), float(captura.idac1),
                             ch0.real_mv_vs_vref, y.real_mv_vs_vref))
            usadas.append(y)
        ajuste = minimos_cuadrados(muestras)
        ajustes_modelo[salida] = (ajuste, usadas)
        if not bool(ajuste["disponible"]):
            filas_modelo.append([
                f"ch{salida}", str(ajuste["n"]), "—", "—", "—", "—", "—", "—", "—"
            ])
            continue
        diferencia_a = float(ajuste["A"]) - A_PREVIO_MV_POR_CODIGO
        filas_modelo.append([
            f"ch{salida}", str(ajuste["n"]), fmt(ajuste["A"], 4, True),
            fmt(ajuste["B"], 4, True), fmt(ajuste["C"], 4, True),
            fmt(ajuste["K"], 1, True), fmt(ajuste["rms"], 2),
            fmt(diferencia_a, 4, True), lineas_csv(usadas),
        ])
    informe.extend(tabla_markdown(
        ["Salida", "N", "A (mV/cód. IDAC2)", "B (mV/cód. IDAC1)",
         "C (mV/mV ch0)", "K (mV)", "RMS (mV)", "A−9,56", "Filas de salida"],
        filas_modelo,
    ))
    informe.append("")
    ajuste_ch2 = ajustes_modelo[2][0]
    if bool(ajuste_ch2["disponible"]):
        informe.append(
            f"Para ch2, A cambia {fmt(float(ajuste_ch2['A']) - A_PREVIO_MV_POR_CODIGO, 4, True)} "
            f"mV/código respecto de +9,56 mV/código. El RMS previo era {fmt(RMS_PREVIO_MV, 1)} mV."
        )
    for salida in (2, 4):
        ajuste = ajustes_modelo[salida][0]
        if not bool(ajuste["disponible"]):
            informe.append(f"No se pudo ajustar ch{salida}: {ajuste['motivo']}.")
        elif float(ajuste["rms"]) > RMS_PREVIO_MV:
            informe.append(
                f"El residuo de ch{salida}, {fmt(ajuste['rms'], 2)} mV RMS, supera 15,0 mV. "
                "Es grande respecto del ajuste previo y señala que falta al menos un término o que "
                "la relación no es constante en el dominio observado."
            )
        else:
            informe.append(
                f"El residuo de ch{salida}, {fmt(ajuste['rms'], 2)} mV RMS, no supera los "
                "15,0 mV del ajuste previo."
            )

    informe.extend(["", "## 6. Ganancia de la etapa LP", ""])
    # La ganancia de etapa es una derivada local. Una recta global ch4~ch2
    # mezclaría los distintos niveles de IDAC1 y sus ordenadas. Se centran x e
    # y dentro de cada tramo fino y se ajusta una pendiente común, equivalente
    # a permitir una ordenada distinta para cada grupo pasada/IDAC1.
    grupos_lp: list[tuple[int, int, list[tuple[Lectura, Lectura]]]] = []
    filas_ganancia_local: list[list[str]] = []
    for pasada in sorted({c.pasada for c in finos}):
        for idac1_actual in IDAC1_PLANEADOS:
            pares_lectura: list[tuple[Lectura, Lectura]] = []
            for captura in finos:
                if captura.pasada != pasada or captura.idac1 != idac1_actual:
                    continue
                ch2 = captura.lecturas.get(2)
                ch4 = captura.lecturas.get(4)
                if (ch2 is None or ch4 is None or not ch2.vivo or not ch4.vivo or
                        ch2.real_mv_vs_vref is None or ch4.real_mv_vs_vref is None):
                    continue
                pares_lectura.append((ch2, ch4))
            if len(pares_lectura) < 2:
                continue
            grupos_lp.append((pasada, idac1_actual, pares_lectura))
            ajuste_local = regresion_lineal([
                (float(ch2.real_mv_vs_vref), float(ch4.real_mv_vs_vref))
                for ch2, ch4 in pares_lectura
                if ch2.real_mv_vs_vref is not None and ch4.real_mv_vs_vref is not None
            ])
            if ajuste_local is not None:
                filas_ganancia_local.append([
                    str(pasada), fmt_entero(idac1_actual, True), str(ajuste_local["n"]),
                    fmt(ajuste_local["pendiente"], 4, True),
                    fmt(ajuste_local["rms"], 2), fmt(ajuste_local["r2"], 4),
                    lineas_csv(par[0] for par in pares_lectura),
                    lineas_csv(par[1] for par in pares_lectura),
                ])
    if not grupos_lp:
        ganancia_lp = None
        informe.append(
            "No se puede calcular: ningún tramo fino tiene al menos dos pares ch2/ch4 válidos y vivos."
        )
    else:
        informe.append(
            "La ganancia se estimó dentro de cada tramo fino, usando sólo pares ch2/ch4 vivos. "
            "El centrado por pasada/IDAC1 permite una ordenada distinta para cada tramo y evita "
            "confundir cambios de nivel entre bloques con la derivada local de la etapa."
        )
        informe.extend(["", "Ganancias por tramo:", ""])
        informe.extend(tabla_markdown(
            ["Pasada", "IDAC1", "N", "Ganancia ch4/ch2 (mV/mV)", "RMS ch4 (mV)",
             "R²", "Filas CSV ch2", "Filas CSV ch4"], filas_ganancia_local,
        ))
        suma_xx = 0.0
        suma_xy = 0.0
        suma_yy = 0.0
        datos_centrados: list[tuple[float, float]] = []
        todas_ch2: list[Lectura] = []
        todas_ch4: list[Lectura] = []
        for _, _, pares_lectura in grupos_lp:
            xs = [float(par[0].real_mv_vs_vref) for par in pares_lectura
                  if par[0].real_mv_vs_vref is not None]
            ys = [float(par[1].real_mv_vs_vref) for par in pares_lectura
                  if par[1].real_mv_vs_vref is not None]
            xm = statistics.mean(xs)
            ym = statistics.mean(ys)
            for (ch2, ch4), x, y in zip(pares_lectura, xs, ys):
                dx = x - xm
                dy = y - ym
                datos_centrados.append((dx, dy))
                suma_xx += dx * dx
                suma_xy += dx * dy
                suma_yy += dy * dy
                todas_ch2.append(ch2)
                todas_ch4.append(ch4)
        ganancia_lp = suma_xy / suma_xx if suma_xx > 0.0 else None
        if ganancia_lp is None:
            informe.append("")
            informe.append("No se puede calcular la pendiente común: ch2 no varía dentro de los tramos útiles.")
        else:
            residuos = [dy - ganancia_lp * dx for dx, dy in datos_centrados]
            rms_lp = math.sqrt(sum(r * r for r in residuos) / len(residuos))
            r2_lp = 1.0 - sum(r * r for r in residuos) / suma_yy if suma_yy > 0.0 else 1.0
            informe.extend(["", "Pendiente común con ordenada absorbida por tramo:", ""])
            informe.extend(tabla_markdown(
                ["Tramos", "N", "Ganancia ch4/ch2 (mV/mV)", "RMS ch4 (mV)",
                 "R² dentro de tramos", "Filas CSV ch2", "Filas CSV ch4"],
                [[str(len(grupos_lp)), str(len(datos_centrados)), fmt(ganancia_lp, 4, True),
                  fmt(rms_lp, 2), fmt(r2_lp, 4), lineas_csv(todas_ch2), lineas_csv(todas_ch4)]],
            ))
            informe.append("")
            informe.append(
                f"Localmente, cada +1 mV de ch2 corresponde a {fmt(ganancia_lp, 4, True)} mV "
                "de ch4. Esto hace que el paso de IDAC2 llegue multiplicado en módulo a ch4. "
                "IDAC3 permaneció en cero, por lo que este archivo no contiene su pendiente: con "
                "este mapa solo no se puede decidir cuantitativamente si el trim final debe usar "
                "IDAC2 o IDAC3."
            )

    informe.extend(["", "## 7. Límites físicos observados", ""])
    informe.append(
        "Los extremos siguientes son lecturas de banco. `pp` también está en unidades del banco. "
        "Una lectura bajo 880,4 mV se conserva como evidencia de fuera de rango, no como tensión física."
    )
    filas_extremos: list[list[str]] = []
    for ch in CANALES:
        grupo = [f for f in filas if f.ch == ch and f.ok and f.media_uv is not None]
        minimo = min(grupo, key=lambda f: float(f.media_uv))
        maximo = max(grupo, key=lambda f: float(f.media_uv))
        filas_extremos.append([
            f"ch{ch} {NOMBRES[ch]}", fmt(minimo.banco_mv, 3), fmt(minimo.pp_uv / 1000.0 if minimo.pp_uv is not None else None, 3),
            estado_canal(minimo), f"{minimo.linea} (P{minimo.pasada}, {minimo.fase}, "
            f"c1={fmt_entero(minimo.idac1, True)}, c2={fmt_entero(minimo.idac2, True)})",
            fmt(maximo.banco_mv, 3), fmt(maximo.pp_uv / 1000.0 if maximo.pp_uv is not None else None, 3),
            estado_canal(maximo), f"{maximo.linea} (P{maximo.pasada}, {maximo.fase}, "
            f"c1={fmt_entero(maximo.idac1, True)}, c2={fmt_entero(maximo.idac2, True)})",
        ])
    informe.extend(tabla_markdown(
        ["Canal", "Mínimo (mV)", "pp mín. (mV)", "Estado", "Fila/clave del mínimo",
         "Máximo (mV)", "pp máx. (mV)", "Estado", "Fila/clave del máximo"],
        filas_extremos,
    ))
    clavadas = [f for f in filas if f.ok and f.esta_clavado and f.media_uv is not None]
    filas_rieles: list[list[str]] = []
    for ch in CANALES:
        for lado, seleccion in (
            ("bajo", [f for f in clavadas if f.ch == ch and f.banco_mv < BANCO_VREF_MV]),
            ("alto", [f for f in clavadas if f.ch == ch and f.banco_mv >= BANCO_VREF_MV]),
        ):
            if not seleccion:
                continue
            valores = [float(f.banco_mv) for f in seleccion if f.banco_mv is not None]
            pps = [float(f.pp_uv) / 1000.0 for f in seleccion if f.pp_uv is not None]
            filas_rieles.append([
                f"ch{ch} {NOMBRES[ch]}", lado, str(len(seleccion)),
                fmt(min(valores), 3), fmt(statistics.median(valores), 3), fmt(max(valores), 3),
                f"{fmt(min(pps), 3)}–{fmt(max(pps), 3)}", lineas_csv(seleccion),
            ])
    informe.extend(["", "Lecturas clavadas repetidas (`pp_banco_uv < 300`):", ""])
    if filas_rieles:
        informe.extend(tabla_markdown(
            ["Canal", "Lado", "N", "Mínimo (mV)", "Mediana (mV)", "Máximo (mV)",
             "Rango pp (mV)", "Filas CSV"], filas_rieles,
        ))
    else:
        informe.append("No hay filas marcadas como clavadas.")
    altos = [f for f in clavadas if f.banco_mv is not None and f.banco_mv >= BANCO_VREF_MV]
    if altos:
        # Se informa el grupo con mas repeticiones; en empate, el de mayor mediana.
        por_canal: dict[int, list[Lectura]] = {}
        for lectura in altos:
            por_canal.setdefault(lectura.ch, []).append(lectura)
        ch_riel, grupo_riel = max(
            por_canal.items(),
            key=lambda par: (len(par[1]), statistics.median(float(x.banco_mv) for x in par[1]
                                                             if x.banco_mv is not None)),
        )
        mediana_riel = statistics.median(float(x.banco_mv) for x in grupo_riel
                                          if x.banco_mv is not None)
        diferencia_limite = BANCO_MAX_VALIDO_MV - mediana_riel
        informe.append("")
        informe.append(
            f"El riel alto repetido aparece en ch{ch_riel}: N={len(grupo_riel)}, mediana "
            f"{fmt(mediana_riel, 3)} mV de banco. Está {fmt(diferencia_limite, 3)} mV de banco "
            f"por debajo de `BANCO_MAX_VALIDO_MV`={fmt(BANCO_MAX_VALIDO_MV, 1)} mV; por eso una "
            "lectura puede estar dentro de la ventana y, a la vez, clavada al riel de la etapa."
        )
    else:
        informe.append("")
        informe.append("No se observó un grupo clavado del lado alto.")

    informe.extend(["", "## 8. Qué queda sin contestar", ""])
    pendientes_ch4_imposibles = []
    for pasada in sorted({c.pasada for c in finos}):
        for idac1_actual in IDAC1_PLANEADOS:
            grupo = [c.lecturas[4] for c in finos if c.pasada == pasada and
                     c.idac1 == idac1_actual and 4 in c.lecturas and c.lecturas[4].vivo]
            if any(c.pasada == pasada and c.idac1 == idac1_actual for c in finos) and len(grupo) < 2:
                pendientes_ch4_imposibles.append(f"P{pasada}/IDAC1={fmt_entero(idac1_actual, True)}")
    pendientes_texto = ", ".join(pendientes_ch4_imposibles) or "ninguno"
    informe.extend([
        "- La segunda pasada no corrió: no hay pares ida/vuelta y no existe dato de histéresis. "
        "No se infiere histéresis ni ausencia de histéresis.",
        "",
        f"- La malla gruesa tiene {len(celdas_malla_completas)} de {total_malla} celdas. "
        "Los IDAC1 sin sus siete celdas no permiten extender las pendientes ni la diagonal fuera del sector observado.",
        "",
        f"- No se pudo obtener pendiente fina de ch4 en: {pendientes_texto}. En esos casos faltan "
        "al menos dos puntos válidos y vivos.",
        "",
        "- IDAC3 quedó fijo en cero. Falta medir `ch4/IDAC3` con ch4 vivo para comparar autoridad y "
        "resolución con la ruta `IDAC2 → ch2 → ch4`; por eso no se asigna el trim final con este archivo.",
        "",
        "- Los tramos finos se centraron alrededor del mejor ch2, no del mejor ch4. La diagonal de ch4 "
        "sólo identifica el mejor código entre los códigos efectivamente muestreados.",
    ])
    if bool(ajuste_ch2.get("disponible")) and float(ajuste_ch2["rms"]) > RMS_PREVIO_MV:
        informe.extend([
            "",
            "- El residuo del modelo de ch2 supera el del ajuste previo. Estos datos no identifican por "
            "sí solos cuál es el término faltante; hace falta otra variable o un modelo por regiones.",
        ])

    informe.extend([
        "",
        "## Criterios de cálculo",
        "",
        f"- Conversión de diferencias: 1 mV de banco = {fmt(FACTOR, 4)} mV físicos.",
        "",
        f"- Ventana válida: {fmt(BANCO_MIN_VALIDO_MV, 1)} a {fmt(BANCO_MAX_VALIDO_MV, 1)} mV de banco; "
        "objetivo: " + fmt(BANCO_VREF_MV, 1) + " mV de banco.",
        "",
        "- Vivo = `ok=True`, `valido=True` y `clavado=False` en la misma fila.",
        "",
        "- Las dispersiones informadas son RMS de los residuos respecto de la recta ajustada. "
        "Los mínimos cuadrados se resolvieron con ecuaciones normales, centrado y escalado de predictores, "
        "y eliminación gaussiana con pivoteo parcial; no se usaron NumPy ni pandas.",
        "",
    ])
    return "\n".join(informe)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="?", type=Path, default=CSV_PREDETERMINADO,
                        help=f"CSV a analizar (predeterminado: {CSV_PREDETERMINADO})")
    parser.add_argument("--salida", type=Path, default=INFORME_PREDETERMINADO,
                        help=f"informe Markdown (predeterminado: {INFORME_PREDETERMINADO})")
    args = parser.parse_args()

    ruta_csv = args.csv.resolve()
    if not ruta_csv.is_file():
        parser.error(f"no existe el CSV: {ruta_csv}")
    filas = cargar_csv(ruta_csv)
    capturas = agrupar_capturas(filas)
    metadatos = cargar_json_adyacente(ruta_csv)
    contenido = generar_informe(ruta_csv, filas, capturas, metadatos)
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(contenido, encoding="utf-8", newline="\n")
    print(f"Informe escrito: {args.salida.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
