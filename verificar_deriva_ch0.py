"""Separa ganancia, orden de lectura y deriva temporal sobre PGAgain (ch0).

El runner no guarda calibracion en el equipo. Cada dato del CSV es la mediana
de tres ventanas DC y se persiste inmediatamente para que una interrupcion no
borre lo ya medido. El canal auxiliar AMuxCapacitor (ch5) no se selecciona.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from escala_banco import (BANCO_MAX_VALIDO_MV, BANCO_MIN_VALIDO_MV,
                          clavado,
                          BANCO_VREF_MV, FACTOR)
from testbench.core.console import Console
from testbench.core.lab import DcPoint, GAIN_CODES, Lab, TAP_NAMES
from testbench.core.session import Session


CANALES = (0, 1, 2, 3, 4)
ORDEN_ASC = CANALES
ORDEN_DESC = tuple(reversed(CANALES))
GANANCIAS = GAIN_CODES
VENTANAS_POR_LECTURA = 3
REINTENTOS_POR_VENTANA = 4
DURACION_CONTROL_S = 300.0
ESPERA_IDAC_LENTO_S = 90.0
ESPERA_CENTRADO_S = 2.0
ESPERA_PGAOUT_S = 3.0
ESTIMADO_TOTAL_S = 15.0 * 60.0

# Criterio deliberadamente expresado en unidades del banco. Un efecto se
# considera observable si supera 1 mV de banco (unos 19,92 mV fisicos). Para
# emitir una sola de las cuatro opciones se elige el efecto normalizado mas
# grande entre ganancia, orden y deriva; si ninguno cruza el umbral, es estable.
UMBRAL_EFECTO_BANCO_UV = 1_000.0

CAMPOS = (
    "t_s", "bloque", "repeticion", "pgaout", "orden", "ch", "tap",
    "media_banco_uv", "pp_banco_uv", "ok", "valido", "clavado",
    "real_mv_vs_vref",
    "codigos_idac",
)


class PlazoAgotado(RuntimeError):
    """Indica un corte limpio solicitado por ``--max-s``."""


class RelojCampana:
    """Reloj real o virtual; en simulacion no duerme tiempo de pared."""

    def __init__(self, max_s: float, simular: bool) -> None:
        self.max_s = max_s
        self.simular = simular
        self._inicio = time.monotonic()
        self._virtual_s = 0.0

    def transcurrido(self) -> float:
        if self.simular:
            return self._virtual_s
        return time.monotonic() - self._inicio

    def avanzar_simulacion(self, segundos: float) -> None:
        if self.simular:
            self._virtual_s += segundos
            self.verificar()

    def verificar(self) -> None:
        if self.transcurrido() >= self.max_s:
            raise PlazoAgotado(f"se alcanzo --max-s={self.max_s:g}")

    def esperar(self, segundos: float) -> None:
        self.verificar()
        disponible = self.max_s - self.transcurrido()
        if segundos > disponible:
            if self.simular:
                self._virtual_s += max(0.0, disponible)
            else:
                time.sleep(max(0.0, disponible))
            raise PlazoAgotado(f"se alcanzo --max-s={self.max_s:g}")
        if self.simular:
            self._virtual_s += segundos
        else:
            time.sleep(segundos)


class ConsolaSimulada:
    """Sustituto sin puerto serie para ejecutar el flujo completo."""

    def open(self, wait_ready: bool = True, timeout: float = 25.0) -> bool:
        del wait_ready, timeout
        return True

    def close(self) -> None:
        return None


class LaboratorioSimulado:
    """Planta reproducible, con saturacion y ruido pequeno de adquisicion."""

    GAIN_CODES = GAIN_CODES

    def __init__(self, reloj: RelojCampana) -> None:
        self.reloj = reloj
        self.idac = [0, 0, 0, 0]
        self.pga = 1
        self.pgaout = 1
        self._azar = random.Random(20260910)

    def set_adc_config(self, cfg: int) -> bool:
        return cfg == 1

    def set_gain(self, cual: str, code: int) -> bool:
        if cual not in ("pga", "pgaout") or not 0 <= code < len(GAIN_CODES):
            return False
        setattr(self, cual, GAIN_CODES[code])
        return True

    def set_idac(self, etapa: int, code: int) -> bool:
        if not 0 <= etapa <= 3 or not -255 <= code <= 255:
            return False
        self.idac[etapa] = code
        return True

    @staticmethod
    def _saturar(valor_uv: float) -> float:
        # Los rieles simulados quedan apenas fuera de la ventana fisica valida.
        return min(1_128_000.0, max(875_000.0, valor_uv))

    def measure_dc(self, ch: int, settle_sel: int = 2) -> DcPoint:
        if ch not in CANALES or settle_sel != 2:
            raise ValueError("la simulacion solo admite ch0..ch4 y settle_sel=2")
        self.reloj.avanzar_simulacion(0.120)
        t_s = self.reloj.transcurrido()
        deriva = 90.0 * math.sin(t_s / 240.0)
        p_uv = -750.0 + 61.2 * self.idac[0] + deriva
        s_uv = 90.1 * self.idac[1]
        v2_uv = 57.9 * self.idac[2]
        ch2_uv = 1_001_500.0 + 12.8 * v2_uv - 4.0 * s_uv - 7.8 * p_uv + 8_000.0
        valores = {
            0: 1_001_500.0 + p_uv,
            1: 1_001_500.0 + s_uv + 2_000.0,
            2: ch2_uv,
            3: 1_001_500.0 + (ch2_uv - 1_001_500.0) * self.pgaout + 2_000.0,
            4: 875_000.0,
        }
        media = self._saturar(valores[ch] + self._azar.gauss(0.0, 55.0))
        pp = max(20, round(240.0 + abs(self._azar.gauss(0.0, 35.0))))
        return DcPoint(ch, settle_sel, round(media), pp, True)


@dataclass
class Lectura:
    media_uv: float | None
    pp_uv: float | None
    ok: bool

    @property
    def clavada(self) -> bool:
        """Sin ruido propio: el tap esta pegado a un riel aunque la ventana
        de escala_banco lo declare dentro de rango. Ver PP_CLAVADO_UV."""
        return clavado(self.pp_uv)

    @property
    def valida(self) -> bool:
        if not self.ok or self.media_uv is None:
            return False
        mv = self.media_uv / 1000.0
        return BANCO_MIN_VALIDO_MV <= mv <= BANCO_MAX_VALIDO_MV


def percentil(valores: list[float], porcentaje: float) -> float | None:
    """Percentil lineal, sin dependencias externas."""
    if not valores:
        return None
    ordenados = sorted(valores)
    posicion = (len(ordenados) - 1) * porcentaje / 100.0
    bajo = math.floor(posicion)
    alto = math.ceil(posicion)
    if bajo == alto:
        return ordenados[bajo]
    fraccion = posicion - bajo
    return ordenados[bajo] * (1.0 - fraccion) + ordenados[alto] * fraccion


def estadisticas(valores: list[float]) -> dict[str, float | int | None]:
    if not valores:
        return {"n": 0, "p5_uv": None, "mediana_uv": None,
                "p95_uv": None, "max_menos_min_uv": None}
    return {
        "n": len(valores),
        "p5_uv": percentil(valores, 5.0),
        "mediana_uv": statistics.median(valores),
        "p95_uv": percentil(valores, 95.0),
        "max_menos_min_uv": max(valores) - min(valores),
    }


def resolver_salidas(output: Path | None,
                     simular: bool = False) -> tuple[Path, Path]:
    if output is None:
        base = (Path(__file__).resolve().parents[3] / "lab" /
                "calibracion_nueva" /
                # una corrida simulada no puede ensuciar la evidencia real
                ("simulacion" if simular else "."))
        csv_path = base / f"verificar_deriva_ch0_{datetime.now():%Y%m%d_%H%M%S}.csv"
    else:
        csv_path = output if output.suffix.lower() == ".csv" else output.with_suffix(".csv")
    return csv_path, csv_path.with_suffix(".json")


def etiqueta_orden(orden: tuple[int, ...]) -> str:
    return "-".join(str(ch) for ch in orden)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--max-s", type=float, default=2400.0,
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
    resumen: dict[str, Any] | None = None
    estado = "iniciando"
    codigo_idac0 = 0

    def guardar_json() -> None:
        contenido = {
            "script": Path(__file__).name,
            "simular": args.simular,
            "csv": str(csv_path),
            "estado": estado,
            "eventos": eventos,
            "resumen": resumen,
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
    if args.simular:
        console = ConsolaSimulada()
    else:
        console = Console(args.port)

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

    def fila_medicion(bloque: str, repeticion: int, ganancia: int,
                      orden: tuple[int, ...], ch: int) -> dict[str, Any]:
        lectura = medir(ch)
        real_mv = None
        if lectura.media_uv is not None:
            real_mv = FACTOR * (lectura.media_uv / 1000.0 - BANCO_VREF_MV)
        return {
            "t_s": round(reloj.transcurrido(), 3), "bloque": bloque,
            "repeticion": repeticion, "pgaout": ganancia,
            "orden": etiqueta_orden(orden), "ch": ch, "tap": TAP_NAMES[ch],
            "media_banco_uv": lectura.media_uv,
            "pp_banco_uv": lectura.pp_uv, "ok": lectura.ok,
            "valido": lectura.valida, "clavado": lectura.clavada,
                "real_mv_vs_vref": real_mv,
            "codigos_idac": json.dumps([codigo_idac0, 0, 0, 0]),
        }

    def centrar_idac0() -> int:
        """Identifica con +/-2, aplica Newton +/-32 y hasta 12 pasos unitarios."""
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
        evento("idac0_congelado", "centrado terminado; no se moveran mas IDAC",
               codigo=codigo, pendiente_uv_por_codigo=pendiente,
               error_final_uv=(None if lectura.media_uv is None else
                               lectura.media_uv - BANCO_VREF_MV * 1000.0),
               correcciones_unitarias=pasos)
        return codigo

    def crear_resumen() -> dict[str, Any]:
        control: dict[str, Any] = {}
        barrido: dict[str, Any] = {}
        for ch in CANALES:
            control[str(ch)] = {}
            for orden in (etiqueta_orden(ORDEN_ASC), etiqueta_orden(ORDEN_DESC)):
                valores = [float(f["media_banco_uv"]) for f in filas
                           if f["bloque"] == "CONTROL" and f["ch"] == ch and
                           f["orden"] == orden and f["ok"] and
                           f["media_banco_uv"] is not None]
                control[str(ch)][orden] = estadisticas(valores)
            barrido[str(ch)] = {}
            for ganancia in GANANCIAS:
                barrido[str(ch)][str(ganancia)] = {}
                for orden in (etiqueta_orden(ORDEN_ASC), etiqueta_orden(ORDEN_DESC)):
                    valores = [float(f["media_banco_uv"]) for f in filas
                               if f["bloque"] == "BARRIDO" and f["ch"] == ch and
                               f["pgaout"] == ganancia and f["orden"] == orden and
                               f["ok"] and f["media_banco_uv"] is not None]
                    barrido[str(ch)][str(ganancia)][orden] = estadisticas(valores)

        ch0_barrido = [f for f in filas if f["bloque"] == "BARRIDO" and
                       f["ch"] == 0 and f["ok"] and f["media_banco_uv"] is not None]
        medianas_ganancia = []
        for ganancia in GANANCIAS:
            vals = [float(f["media_banco_uv"]) for f in ch0_barrido
                    if f["pgaout"] == ganancia]
            if vals:
                medianas_ganancia.append(statistics.median(vals))
        efecto_ganancia = (max(medianas_ganancia) - min(medianas_ganancia)
                           if len(medianas_ganancia) >= 2 else 0.0)
        por_orden = {}
        for orden in (etiqueta_orden(ORDEN_ASC), etiqueta_orden(ORDEN_DESC)):
            vals = [float(f["media_banco_uv"]) for f in filas if f["ch"] == 0 and
                    f["orden"] == orden and f["ok"] and
                    f["media_banco_uv"] is not None]
            por_orden[orden] = statistics.median(vals) if vals else None
        orden_vals = [v for v in por_orden.values() if v is not None]
        efecto_orden = abs(orden_vals[0] - orden_vals[1]) if len(orden_vals) == 2 else 0.0
        control_ch0 = [f for f in filas if f["bloque"] == "CONTROL" and f["ch"] == 0
                       and f["ok"] and f["media_banco_uv"] is not None]
        control_ch0.sort(key=lambda f: float(f["t_s"]))
        tercio = max(1, len(control_ch0) // 3)
        efecto_deriva = 0.0
        if len(control_ch0) >= 3:
            inicial = statistics.median(float(f["media_banco_uv"])
                                        for f in control_ch0[:tercio])
            final = statistics.median(float(f["media_banco_uv"])
                                      for f in control_ch0[-tercio:])
            efecto_deriva = abs(final - inicial)
        efectos = {
            "depende de la ganancia": efecto_ganancia,
            "depende del orden de lectura": efecto_orden,
            "deriva temporal": efecto_deriva,
        }
        mayor_nombre, mayor_valor = max(efectos.items(), key=lambda par: par[1])
        veredicto = (mayor_nombre if mayor_valor >= UMBRAL_EFECTO_BANCO_UV
                     else "estable")
        return {
            "codigo_idac0_final": codigo_idac0,
            "dispersion_control_por_canal_y_orden": control,
            "dispersion_barrido_por_canal_ganancia_y_orden": barrido,
            "criterio_ch0": {
                "umbral_banco_uv": UMBRAL_EFECTO_BANCO_UV,
                "regla": ("se elige el mayor efecto que alcance el umbral; "
                          "ganancia=recorrido de medianas por ganancia, "
                          "orden=diferencia de medianas por orden, "
                          "deriva=diferencia entre primer y ultimo tercio del CONTROL"),
                "efecto_ganancia_uv": efecto_ganancia,
                "efecto_orden_uv": efecto_orden,
                "efecto_deriva_uv": efecto_deriva,
                "veredicto": veredicto,
            },
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
        operacion_comprobada(lambda: lab.set_gain("pga", GAIN_CODES.index(50)),
                             "PGA x50")
        operacion_comprobada(lambda: lab.set_gain("pgaout", GAIN_CODES.index(1)),
                             "PGAout x1")
        for etapa in range(4):
            operacion_comprobada(lambda e=etapa: lab.set_idac(e, 0),
                                 f"IDAC{etapa}=0")
        progreso("puesta a punto; asentando referencias")
        reloj.esperar(ESPERA_IDAC_LENTO_S)

        estado = "centrando_idac0"
        codigo_idac0 = centrar_idac0()
        progreso(f"IDAC0 congelado en {codigo_idac0:+d}")

        estado = "control"
        evento("bloque", "comienza CONTROL de cinco minutos")
        inicio_control = reloj.transcurrido()
        pasada = 0
        while reloj.transcurrido() - inicio_control < DURACION_CONTROL_S:
            reloj.verificar()
            orden = ORDEN_ASC if pasada % 2 == 0 else ORDEN_DESC
            for ch in orden:
                escribir_fila(fila_medicion("CONTROL", pasada + 1, 1, orden, ch))
            pasada += 1
            progreso(f"CONTROL pasada {pasada}, orden {etiqueta_orden(orden)}")
        evento("bloque", "CONTROL terminado", pasadas=pasada)

        estado = "barrido"
        evento("bloque", "comienza BARRIDO de PGAout")
        recorrido = GANANCIAS + tuple(reversed(GANANCIAS))
        for repeticion in range(1, 4):
            for indice, ganancia in enumerate(recorrido, start=1):
                operacion_comprobada(
                    lambda g=ganancia: lab.set_gain("pgaout", GAIN_CODES.index(g)),
                    f"PGAout x{ganancia}")
                reloj.esperar(ESPERA_PGAOUT_S)
                for orden in (ORDEN_ASC, ORDEN_DESC):
                    for ch in orden:
                        escribir_fila(fila_medicion("BARRIDO", repeticion,
                                                   ganancia, orden, ch))
                progreso(f"BARRIDO rep {repeticion}/3, punto {indice}/{len(recorrido)}, "
                         f"PGAout x{ganancia}")
        resumen = crear_resumen()
        estado = "completada"
        evento("resumen", "campana completada", veredicto=resumen["criterio_ch0"]["veredicto"])
        completada = True
    except (PlazoAgotado, KeyboardInterrupt) as exc:
        error_final = "interrupcion de teclado" if isinstance(exc, KeyboardInterrupt) else str(exc)
        estado = "abortada"
        evento("aborto", error_final)
    except Exception as exc:  # La evidencia parcial se conserva ante cualquier falla.
        error_final = f"{type(exc).__name__}: {exc}"
        estado = "abortada"
        evento("error", error_final)
    finally:
        # Esta neutralizacion no usa el plazo: tiene prioridad incluso al agotarlo.
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
        criterio = resumen["criterio_ch0"]
        print("\nRESUMEN FINAL", flush=True)
        print(f"  IDAC0 final: {codigo_idac0:+d}", flush=True)
        print(f"  ch0: {criterio['veredicto']} "
              f"(ganancia {criterio['efecto_ganancia_uv']/1000:.3f}, "
              f"orden {criterio['efecto_orden_uv']/1000:.3f}, "
              f"deriva {criterio['efecto_deriva_uv']/1000:.3f} mV banco)",
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
