#!/usr/bin/env python3
"""Campaña desatendida de arranques en frío lógico de la autocalibración.

Un ``ToggleReset`` reinicia el PSoC, pero no corta su alimentación. Por eso esta
campaña randomiza IDAC0/IDAC1 antes de cada calibración: perturba el estado
analógico que conserva el capacitor de 680 uF, sin pretender que sea un ciclo
real de alimentación ni una prueba de la rampa de la fuente.

El calibrador siempre se ejecuta como subproceso. ``--simular`` no abre puertos,
no llama a KitProg y no busca ni mata procesos del calibrador.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


SCRIPT_PATH = Path(__file__).resolve()
PYTHON_DIR = SCRIPT_PATH.parent
REPO_ROOT = SCRIPT_PATH.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import pedido explícitamente: este módulo no ejecuta ppcli al importarse.
from scripts.autonomia.device_reset import PPCLI_DIR_DEFAULT, psoc_reset  # noqa: E402


CALIBRADOR = PYTHON_DIR / "autocalibracion_dos_fases.py"
LAB_DIR = REPO_ROOT / "lab"
DATOS_DIR = LAB_DIR / "arranque_frio"
GANANCIAS_DEFAULT = (1, 2, 4, 8, 16, 24)
NOMBRES_TAPS = ("PGAgain", "BPo", "OPA_SUMo", "SUMo", "LPo")

# Copias deliberadas de las constantes físicas usadas por el calibrador. No se
# importa ese módulo porque cada intento debe tener un proceso limpio.
FACTOR_BANCO = 19.9157
BANCO_MIN_VALIDO_MV = 880.4
BANCO_RIEL_ALTO_MV = 1106.1
MARGEN_RIEL_MV = 250.0
PP_CLAVADO_UV = 300.0
KITPROG_DEFAULT = "KitProg (CMSIS-DAP/246475)"

CSV_BASE_FIELDS = (
    "intento", "inicio", "fin", "reset_programado", "reset_realizado",
    "reset_ok", "reset_detalle", "modo", "idac0_sorteado", "idac1_sorteado",
    "preparacion_ok", "espera_psoc_s", "espera_random_s",
    "duracion_calibracion_s", "duracion_fase1_s", "duracion_fase2_s",
    "duracion_total_s", "exit_code", "exito", "puntaje_mv", "error",
    "huerfanos_matados", "ganancias_intento", "evidencia_json", "ganancias_json", "taps_json",
    "actuadores_json", "stdout", "stderr",
)


def log(mensaje: str) -> None:
    print(mensaje, flush=True)


def json_compacto(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, separators=(",", ":"))


def numero(valor: Any) -> float | None:
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        resultado = float(valor)
    except (TypeError, ValueError):
        return None
    return resultado if math.isfinite(resultado) else None


def fmt(valor: float | int | None, decimales: int = 1) -> str:
    """Número para el informe, con coma decimal."""
    if valor is None:
        return "sin dato"
    return f"{float(valor):.{decimales}f}".replace(".", ",")


def fmt_pct(valor: float | None) -> str:
    return "sin dato" if valor is None else f"{valor:.1f} %".replace(".", ",")


def percentil(valores: Iterable[float], porcentaje: float) -> float | None:
    datos = sorted(float(v) for v in valores)
    if not datos:
        return None
    if len(datos) == 1:
        return datos[0]
    posicion = (len(datos) - 1) * porcentaje / 100.0
    inferior = math.floor(posicion)
    superior = math.ceil(posicion)
    if inferior == superior:
        return datos[inferior]
    fraccion = posicion - inferior
    return datos[inferior] * (1.0 - fraccion) + datos[superior] * fraccion


def estadistica(valores: Iterable[float]) -> dict[str, float | int | None]:
    datos = [float(v) for v in valores]
    if not datos:
        return {"n": 0, "media": None, "desvio": None, "p95": None,
                "maximo": None, "mediana": None, "q1": None, "q3": None,
                "ric": None, "minimo": None}
    q1, q3 = percentil(datos, 25), percentil(datos, 75)
    return {
        "n": len(datos),
        "media": statistics.fmean(datos),
        "desvio": statistics.stdev(datos) if len(datos) > 1 else 0.0,
        "p95": percentil(datos, 95),
        "maximo": max(datos),
        "mediana": statistics.median(datos),
        "q1": q1,
        "q3": q3,
        "ric": None if q1 is None or q3 is None else q3 - q1,
        "minimo": min(datos),
    }


def wilson(exitos: int, total: int, z: float = 1.959963984540054) -> tuple[float, float] | None:
    if total <= 0:
        return None
    p = exitos / total
    divisor = 1.0 + z * z / total
    centro = (p + z * z / (2.0 * total)) / divisor
    radio = z * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total)) / divisor
    return max(0.0, centro - radio), min(1.0, centro + radio)


def intervalo_texto(exitos: int, total: int) -> str:
    intervalo = wilson(exitos, total)
    if intervalo is None:
        return "sin dato"
    return f"{fmt_pct(100.0 * intervalo[0])}–{fmt_pct(100.0 * intervalo[1])}"


def escritura_atomica(path: Path, texto: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_name(path.name + ".tmp")
    temporal.write_text(texto, encoding="utf-8")
    os.replace(temporal, path)


def ruta_registrable(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def guardar_json(path: Path, payload: dict[str, Any]) -> None:
    escritura_atomica(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _csv_valor(valor: Any) -> Any:
    if isinstance(valor, float):
        return repr(valor).replace(".", ",")
    if isinstance(valor, (dict, list)):
        return json_compacto(valor)
    if valor is None:
        return ""
    return valor


def guardar_csv(path: Path, intentos: list[dict[str, Any]], ganancias: list[int]) -> None:
    campos = list(CSV_BASE_FIELDS)
    for ganancia in ganancias:
        campos.extend((f"x{ganancia}_paso", f"x{ganancia}_error_lpo_mv",
                       f"x{ganancia}_duracion_s"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_name(path.name + ".tmp")
    with temporal.open("w", newline="", encoding="utf-8-sig") as archivo:
        writer = csv.DictWriter(archivo, fieldnames=campos, delimiter=";")
        writer.writeheader()
        for intento in intentos:
            fila = {campo: _csv_valor(intento.get(campo)) for campo in campos}
            writer.writerow(fila)
    os.replace(temporal, path)


def matar_arbol(pid: int) -> bool:
    hecho = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    return hecho.returncode == 0


def matar_calibradores_huerfanos() -> list[dict[str, Any]]:
    """Mata sólo Python cuyo command line nombra el calibrador objetivo."""
    consulta = r"""
$ErrorActionPreference = 'Stop'
$actual = %d
$items = Get-CimInstance Win32_Process | Where-Object {
  $_.ProcessId -ne $actual -and
  $_.Name -match '^(python|pythonw|py)(\.exe)?$' -and
  $_.CommandLine -match 'autocalibracion_dos_fases\.py'
} | Select-Object ProcessId, Name, CommandLine
@($items) | ConvertTo-Json -Compress
""" % os.getpid()
    hecho = subprocess.run(
        ["powershell", "-NoProfile", "-Command", consulta],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=30.0,
    )
    if hecho.returncode != 0:
        raise RuntimeError(f"no se pudieron enumerar huérfanos: {hecho.stderr.strip()}")
    candidatos = json.loads(hecho.stdout or "[]")
    if isinstance(candidatos, dict):
        candidatos = [candidatos]
    muertos = []
    for proceso in candidatos:
        pid = int(proceso["ProcessId"])
        proceso["matado"] = matar_arbol(pid)
        muertos.append(proceso)
    return muertos


def dormir_hasta(segundos: float, limite: float | None = None) -> bool:
    """Espera interrumpible; False indica que venció el límite de campaña."""
    fin = time.monotonic() + max(0.0, segundos)
    while time.monotonic() < fin:
        if limite is not None and time.monotonic() >= limite:
            return False
        restante = fin - time.monotonic()
        if limite is not None:
            restante = min(restante, limite - time.monotonic())
        time.sleep(max(0.0, min(1.0, restante)))
    return True


def elegir_estado(modo: str, rng: random.Random) -> tuple[str, int, int]:
    if modo == "aleatorio":
        # IDAC0 tiene mucha autoridad; ±48 códigos ya recorre ampliamente la
        # entrada sin salir del rango eléctrico admitido por el firmware.
        idac0 = rng.choice(tuple(range(-48, -7)) + tuple(range(8, 49)))
        idac1 = rng.choice(tuple(range(-220, -31)) + tuple(range(32, 221)))
        return modo, idac0, idac1
    if modo == "golpe":
        return modo, rng.choice((-220, 220)), 0
    raise ValueError(f"modo concreto desconocido: {modo}")


def modo_del_intento(modo: str, intento: int) -> str:
    if modo == "mixto":
        return "aleatorio" if intento % 2 else "golpe"
    return modo


def preparar_estado_real(
    port: str,
    idac0: int,
    idac1: int,
    espera_random_s: float,
    timeout_psoc_s: float,
    limite_campana: float,
) -> dict[str, Any]:
    """Espera ACK en cero, perturba el estado y vuelve a cero."""
    from testbench.core.console import Console
    from testbench.core.lab import Lab
    from testbench.core.session import Session

    iniciado = time.monotonic()
    console = Console(port)
    lab = None
    perturbado = False
    resultado: dict[str, Any] = {
        "ok": False, "espera_psoc_s": 0.0, "espera_random_s": 0.0,
        "detalle": "", "acks_cero": {},
    }
    try:
        console.open(wait_ready=True, timeout=25.0)
        lab = Lab(Session(console))
        pendientes = set(range(4))
        techo = min(iniciado + timeout_psoc_s, limite_campana)
        while pendientes and time.monotonic() < techo:
            for stage in tuple(sorted(pendientes)):
                restante = max(0.1, techo - time.monotonic())
                # Lab reintenta internamente dos veces: repartir el tiempo
                # restante impide que una llamada exceda por sí sola el techo.
                if lab.set_idac(stage, 0, timeout=min(2.0, restante / 2.0)):
                    resultado["acks_cero"][str(stage)] = True
                    pendientes.remove(stage)
                if time.monotonic() >= techo:
                    break
            if pendientes:
                dormir_hasta(1.0, techo)
        resultado["espera_psoc_s"] = time.monotonic() - iniciado
        if pendientes:
            resultado["detalle"] = (
                "PSoC no aceptó cero en IDAC " + ",".join(map(str, sorted(pendientes)))
                + f" antes de {timeout_psoc_s:.0f} s"
            )
            return resultado

        for stage, code in ((0, idac0), (1, idac1)):
            if not lab.set_idac(stage, code):
                resultado["detalle"] = f"PSoC rechazó randomización IDAC{stage}={code}"
                return resultado
            perturbado = perturbado or code != 0
        espera_iniciada = time.monotonic()
        if not dormir_hasta(espera_random_s, limite_campana):
            resultado["detalle"] = "venció --max-h durante la espera randomizada"
            return resultado
        resultado["espera_random_s"] = time.monotonic() - espera_iniciada
        rechazados = [stage for stage in range(4) if not lab.set_idac(stage, 0)]
        if rechazados:
            resultado["detalle"] = "PSoC rechazó vuelta a cero: " + ",".join(map(str, rechazados))
            return resultado
        resultado["ok"] = True
        resultado["detalle"] = "estado perturbado y cuatro IDAC devueltos a cero"
        return resultado
    except Exception as exc:  # cada intento debe sobrevivir al enlace
        resultado["detalle"] = f"{type(exc).__name__}: {exc}"
        return resultado
    finally:
        if lab is not None and perturbado:
            # También neutraliza un intento cuya segunda escritura aleatoria
            # fue rechazada. Es mejor esfuerzo: el siguiente intento volverá a
            # insistir con la guarda de 300 s.
            for stage in range(4):
                try:
                    lab.set_idac(stage, 0, timeout=0.5)
                except Exception:
                    pass
        console.close()


def buscar_resultado_campana(evidencia: dict[str, Any]) -> dict[str, Any] | None:
    eventos = evidencia.get("events", [])
    if not isinstance(eventos, list):
        return None
    for evento in reversed(eventos):
        if isinstance(evento, dict) and evento.get("kind") == "gain_campaign_result":
            resultado = evento.get("result")
            return resultado if isinstance(resultado, dict) else None
    resumenes = [e.get("runs") for e in eventos if isinstance(e, dict)
                 and e.get("kind") == "campaign_summary"]
    if resumenes and isinstance(resumenes[-1], list) and resumenes[-1]:
        ultimo = resumenes[-1][-1]
        if isinstance(ultimo, dict) and isinstance(ultimo.get("result"), dict):
            return ultimo["result"]
    return None


def resultados_ganancias(resultado: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not resultado:
        return []
    if isinstance(resultado.get("gains"), list):  # esquema anterior
        return [item for item in resultado["gains"] if isinstance(item, dict)]
    encontrados: list[dict[str, Any]] = []
    for clave in ("comprometidas", "mejor_esfuerzo"):
        bloque = resultado.get(clave, {})
        if isinstance(bloque, dict) and isinstance(bloque.get("results"), list):
            encontrados.extend(item for item in bloque["results"] if isinstance(item, dict))
    return encontrados


def puntaje_tap(tap: dict[str, Any]) -> float | None:
    media = numero(tap.get("mean_bank_uv"))
    pp = numero(tap.get("pp_bank_uv"))
    if media is None or pp is None:
        return None
    borde_bajo = (media - pp / 2.0) / 1000.0
    borde_alto = (media + pp / 2.0) / 1000.0
    margen = min(
        FACTOR_BANCO * (borde_bajo - BANCO_MIN_VALIDO_MV),
        FACTOR_BANCO * (BANCO_RIEL_ALTO_MV - borde_alto),
    ) - MARGEN_RIEL_MV
    # El calibrador considera que pp < 300 uV identifica un tap clavado aunque
    # su media aparente quedar dentro de rango. El signo conserva esa decisión.
    if pp < PP_CLAVADO_UV and margen >= 0.0:
        return -max(0.001, margen)
    return margen


def extraer_actuadores(resultado: dict[str, Any] | None,
                       ganancias_resultado: list[dict[str, Any]]) -> dict[str, Any]:
    salida = {str(stage): {"movimientos": 0, "motivos": []} for stage in range(4)}
    if resultado:
        fase1 = resultado.get("phase1", {})
        if isinstance(fase1, dict):
            idacs = fase1.get("idacs", {})
            if isinstance(idacs, dict):
                for stage in range(4):
                    dato = idacs.get(str(stage), {})
                    if isinstance(dato, dict) and dato.get("moved"):
                        intentos = dato.get("attempts")
                        cantidad = len(intentos) if isinstance(intentos, list) and intentos else 1
                        salida[str(stage)]["movimientos"] += cantidad
                        salida[str(stage)]["motivos"].append(str(dato.get("reason", "sin motivo")))
    for ganancia in ganancias_resultado:
        for stage in (2, 3):
            ajuste = ganancia.get(f"idac{stage}")
            if not isinstance(ajuste, dict):
                continue
            historial = ajuste.get("history")
            cantidad = len(historial) if isinstance(historial, list) else int(
                bool(numero(ajuste.get("iterations"))))
            if cantidad:
                salida[str(stage)]["movimientos"] += cantidad
                salida[str(stage)]["motivos"].append(
                    f"x{ganancia.get('gain')}: lazo cerrado ({ajuste.get('stop_reason', 'sin motivo')})"
                )
    return salida


def analizar_evidencia(
    evidencia: dict[str, Any] | None,
    ganancias_pedidas: list[int],
    exit_code: int,
) -> dict[str, Any]:
    resultado = buscar_resultado_campana(evidencia or {})
    lista = resultados_ganancias(resultado)
    por_ganancia = {int(item["gain"]): item for item in lista if numero(item.get("gain")) is not None}
    resumen_ganancias: dict[str, Any] = {}
    taps: dict[str, Any] = {}
    puntajes = []
    fase2 = 0.0
    for gain in ganancias_pedidas:
        item = por_ganancia.get(gain)
        duracion = numero(item.get("elapsed_s")) if item else None
        error_lpo = numero(item.get("lp_error_real_mv")) if item else None
        final = item.get("final", {}) if item else {}
        if not isinstance(final, dict):
            final = {}
        taps_gain = {}
        for ch in range(5):
            punto = final.get(str(ch), final.get(ch))
            if isinstance(punto, dict):
                taps_gain[str(ch)] = {
                    "nombre": NOMBRES_TAPS[ch],
                    "media_banco_uv": numero(punto.get("mean_bank_uv")),
                    "pico_a_pico_banco_uv": numero(punto.get("pp_bank_uv")),
                }
                score = puntaje_tap(punto)
                if score is not None:
                    puntajes.append(score)
            else:
                taps_gain[str(ch)] = None
        taps[str(gain)] = taps_gain
        resumen_ganancias[str(gain)] = {
            "paso": bool(item and item.get("pass")),
            "error_lpo_mv": error_lpo,
            "duracion_s": duracion,
        }
        if duracion is not None:
            fase2 += duracion
    total = numero(resultado.get("total_elapsed_s")) if resultado else None
    fase1 = None if total is None else max(0.0, total - fase2)
    exito = bool(exit_code == 0 and resultado and resultado.get("pass")
                 and all(resumen_ganancias[str(g)]["paso"] for g in ganancias_pedidas))
    return {
        "exito": exito,
        "resultado": resultado,
        "ganancias": resumen_ganancias,
        "taps": taps,
        "puntaje_mv": min(puntajes) if puntajes else None,
        "duracion_reportada_s": total,
        "fase1_s": fase1,
        "fase2_s": fase2 if lista else None,
        "actuadores": extraer_actuadores(resultado, lista),
    }


def simular_evidencia(
    numero_intento: int,
    modo: str,
    ganancias: list[int],
    rng: random.Random,
    path: Path,
) -> tuple[int, str, str, float]:
    """Doble verosímil del subproceso; incluye fallos sin demoras reales."""
    base_fase1 = rng.uniform(45.0, 125.0) + (rng.uniform(25.0, 70.0) if modo == "golpe" else 0.0)
    prob_fallo = 0.07 if modo == "aleatorio" else 0.17
    fallo = rng.random() < prob_fallo
    falla_en = rng.choice(ganancias) if fallo else None
    resultados = []
    elapsed_gains = 0.0
    for gain in ganancias:
        duracion = rng.uniform(15.0, 38.0) + (rng.uniform(4.0, 18.0) if modo == "golpe" else 0.0)
        elapsed_gains += duracion
        paso = not fallo or gain != falla_en
        final = {}
        for ch in range(5):
            media = 1_001_500.0 + rng.gauss(0.0, 4_500.0 + gain * 80.0)
            pp = rng.uniform(520.0, 3_400.0)
            if not paso and ch == rng.randrange(5):
                media = rng.choice((884_000.0, 1_102_500.0))
                pp = rng.uniform(70.0, 220.0)
            final[str(ch)] = {
                "mean_bank_uv": round(media), "pp_bank_uv": round(pp),
                "error_real_mv": (media - 1_001_500.0) * FACTOR_BANCO / 1000.0,
            }
        ajuste2 = rng.randint(0, 3)
        ajuste3 = rng.randint(0, 3)
        resultados.append({
            "gain": gain, "pass": paso, "elapsed_s": duracion,
            "lp_error_real_mv": final["4"]["error_real_mv"], "final": final,
            "idac2": {"iterations": ajuste2, "history": [{}] * ajuste2,
                      "stop_reason": "resolucion_medio_paso"},
            "idac3": {"iterations": ajuste3, "history": [{}] * ajuste3,
                      "stop_reason": "resolucion_medio_paso"},
        })
    idac0_mueve = rng.random() < (0.12 if modo == "aleatorio" else 0.32)
    idac1_mueve = rng.random() < (0.25 if modo == "aleatorio" else 0.48)
    fase1 = {
        "pass": True,
        "idacs": {
            "0": {"moved": idac0_mueve, "reason": (
                "mínimo movimiento para margen ch0" if idac0_mueve
                else "IDAC0 no hizo falta: ch0 ya tenía margen")},
            "1": {"moved": idac1_mueve, "reason": (
                "mínimo movimiento para margen ch1" if idac1_mueve
                else "IDAC1 no hizo falta para margen ch1")},
            "2": {"moved": False, "reason": "reservado para fase 2"},
            "3": {"moved": False, "reason": "reservado para fase 2"},
        },
    }
    bloque = {
        "pass": not fallo, "phase1": fase1,
        "comprometidas": {"results": resultados},
        "mejor_esfuerzo": {"results": []},
        "total_elapsed_s": base_fase1 + elapsed_gains,
    }
    evidencia = {
        "algorithm": "DOBLE SIMULADO: GEO margen + fase2 por ganancia",
        "events": [{"at": datetime.now().isoformat(timespec="milliseconds"),
                    "kind": "gain_campaign_result", "result": bloque}],
        "measurements": [],
    }
    guardar_json(path, evidencia)
    codigo = 0 if not fallo else 1
    stdout = f"[SIMULADO] intento {numero_intento}; evidencia: {path}\n"
    return codigo, stdout, "", bloque["total_elapsed_s"]


def ejecutar_calibrador(
    port: str,
    ganancias: list[int],
    evidencia_path: Path,
    timeout_s: float,
) -> tuple[int, str, str, float, bool]:
    comando = [
        "py", "-3", str(CALIBRADOR), "calibrate-gains", "--port", port,
        "--gains", ",".join(map(str, ganancias)), "--output", str(evidencia_path),
    ]
    inicio = time.monotonic()
    proceso = subprocess.Popen(
        comando, cwd=str(PYTHON_DIR), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
    )
    timeout = False
    try:
        stdout, stderr = proceso.communicate(timeout=max(0.1, timeout_s))
    except subprocess.TimeoutExpired:
        timeout = True
        matar_arbol(proceso.pid)
        stdout, stderr = proceso.communicate()
        stderr += f"\nTimeout del runner tras {timeout_s:.1f} s"
    codigo = 124 if timeout else (proceso.returncode if proceso.returncode is not None else 125)
    return codigo, stdout, stderr, time.monotonic() - inicio, timeout


def crear_intento_base(numero_intento: int, modo: str, idac0: int, idac1: int,
                        reset_programado: bool) -> dict[str, Any]:
    return {
        "intento": numero_intento,
        "inicio": datetime.now().isoformat(timespec="seconds"),
        "fin": None,
        "reset_programado": reset_programado,
        "reset_realizado": False,
        "reset_ok": None,
        "reset_detalle": "salteado por cadencia" if not reset_programado else "",
        "modo": modo,
        "idac0_sorteado": idac0,
        "idac1_sorteado": idac1,
        "preparacion_ok": False,
        "espera_psoc_s": 0.0,
        "espera_random_s": 0.0,
        "duracion_calibracion_s": None,
        "duracion_fase1_s": None,
        "duracion_fase2_s": None,
        "duracion_total_s": None,
        "exit_code": None,
        "exito": False,
        "puntaje_mv": None,
        "error": "",
        "huerfanos_matados": [],
        "evidencia_json": "",
        "ganancias_json": {},
        "taps_json": {},
        "actuadores_json": {},
        "stdout": "",
        "stderr": "",
    }


def completar_columnas_ganancia(intento: dict[str, Any], ganancias: list[int]) -> None:
    resumen = intento.get("ganancias_json", {})
    for gain in ganancias:
        dato = resumen.get(str(gain), {}) if isinstance(resumen, dict) else {}
        intento[f"x{gain}_paso"] = dato.get("paso")
        intento[f"x{gain}_error_lpo_mv"] = dato.get("error_lpo_mv")
        intento[f"x{gain}_duracion_s"] = dato.get("duracion_s")


def resumen_para_json(intentos: list[dict[str, Any]], ganancias: list[int]) -> dict[str, Any]:
    exito = sum(bool(item.get("exito")) for item in intentos)
    por_ganancia = {}
    for gain in ganancias:
        aprobados = sum(bool(item.get(f"x{gain}_paso")) for item in intentos)
        por_ganancia[str(gain)] = {
            "exitos": aprobados, "total": len(intentos),
            "tasa": aprobados / len(intentos) if intentos else None,
            "wilson_95": wilson(aprobados, len(intentos)),
        }
    return {
        "intentos_completados": len(intentos),
        "exitos": exito,
        "tasa_exito": exito / len(intentos) if intentos else None,
        "wilson_95": wilson(exito, len(intentos)),
        "por_ganancia": por_ganancia,
        "tiempos": {
            nombre: estadistica(item[clave] for item in intentos if numero(item.get(clave)) is not None)
            for nombre, clave in (("total", "duracion_total_s"),
                                  ("calibracion", "duracion_calibracion_s"),
                                  ("fase1", "duracion_fase1_s"),
                                  ("fase2", "duracion_fase2_s"))
        },
    }


def persistir(
    csv_path: Path,
    json_path: Path,
    config: dict[str, Any],
    intentos: list[dict[str, Any]],
    ganancias: list[int],
    estado: str,
    motivo_fin: str | None,
) -> None:
    guardar_csv(csv_path, intentos, ganancias)
    payload = {
        "tipo": "campaña de arranque en frío lógico",
        "estado": estado,
        "motivo_fin": motivo_fin,
        "actualizado": datetime.now().isoformat(timespec="seconds"),
        "configuracion": config,
        "advertencia": (
            "ToggleReset reinicia el chip pero no corta alimentación; el capacitor de "
            "680 uF conserva carga. No es un ciclo real ni prueba la rampa de fuente."
        ),
        "resumen": resumen_para_json(intentos, ganancias),
        "intentos": intentos,
    }
    guardar_json(json_path, payload)


def agregar_tabla_tiempos(lineas: list[str], nombre: str, datos: dict[str, Any]) -> None:
    lineas.append(
        f"| {nombre} | {datos['n']} | {fmt(datos['media'])} | {fmt(datos['desvio'])} | "
        f"{fmt(datos['p95'])} | {fmt(datos['maximo'])} |"
    )


def generar_informe(
    path: Path,
    csv_path: Path,
    json_path: Path,
    intentos: list[dict[str, Any]],
    ganancias: list[int],
    planeados: int,
    motivo_fin: str,
    simular: bool,
) -> None:
    resumen = resumen_para_json(intentos, ganancias)
    completados = len(intentos)
    exitos = int(resumen["exitos"])
    duracion = sum(numero(item.get("duracion_total_s")) or 0.0 for item in intentos)
    lineas = [
        f"# Resultados de arranque en frío — {datetime.now():%Y-%m-%d}", "",
        "## Alcance y estado", "",
        f"- Intentos completados: {completados} de {planeados}.",
        f"- Duración acumulada de los intentos: {fmt(duracion)} s ({fmt(duracion / 3600.0, 2)} h).",
        f"- Fin de campaña: {motivo_fin}.",
        f"- Datos por intento: `{ruta_registrable(csv_path)}`.",
        f"- Evidencia estructurada: `{ruta_registrable(json_path)}`.",
    ]
    if simular:
        lineas.extend(["- Ejecución simulada: no se abrió COM8 ni se llamó a KitProg.", ""])
    else:
        lineas.append("")

    lineas.extend([
        "## Tasa de éxito", "",
        f"La tasa global fue {exitos}/{completados} ({fmt_pct(100.0 * exitos / completados if completados else None)}), "
        f"con intervalo de Wilson al 95 % de {intervalo_texto(exitos, completados)}.", "",
        "| Ganancia | Éxitos/intentos | Tasa | Wilson 95 % |",
        "|---:|---:|---:|---:|",
    ])
    for gain in ganancias:
        aprobados = sum(bool(item.get(f"x{gain}_paso")) for item in intentos)
        lineas.append(
            f"| x{gain} | {aprobados}/{completados} | "
            f"{fmt_pct(100.0 * aprobados / completados if completados else None)} | "
            f"{intervalo_texto(aprobados, completados)} |"
        )

    lineas.extend([
        "", "## Tiempos", "",
        "| Tramo | n | Media (s) | Desvío (s) | p95 (s) | Máximo (s) |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for nombre in ("total", "calibracion", "fase1", "fase2"):
        agregar_tabla_tiempos(lineas, nombre, resumen["tiempos"][nombre])

    lineas.extend([
        "", "## Error de LPo logrado", "",
        "Se informa la mediana y el rango intercuartílico (RIC); no se compara con un umbral.", "",
        "| Ganancia | n | Mediana (mV) | RIC (mV) |",
        "|---:|---:|---:|---:|",
    ])
    for gain in ganancias:
        valores = [numero(item.get(f"x{gain}_error_lpo_mv")) for item in intentos]
        stats = estadistica(v for v in valores if v is not None)
        lineas.append(f"| x{gain} | {stats['n']} | {fmt(stats['mediana'])} | {fmt(stats['ric'])} |")

    scores = [numero(item.get("puntaje_mv")) for item in intentos]
    score_stats = estadistica(v for v in scores if v is not None)
    negativos = sum(v is not None and v < 0.0 for v in scores)
    lineas.extend([
        "", "## Puntaje", "",
        "El puntaje de un intento es el menor margen de cualquiera de sus taps respecto de la guarda de 250 mV. "
        "Un tap con pico a pico menor que 300 µV se trata como clavado y recibe signo negativo.", "",
        f"Hubo {negativos} intentos con puntaje negativo de {score_stats['n']} con puntaje calculable.", "",
        "| Mínimo (mV) | Q1 (mV) | Mediana (mV) | Q3 (mV) | Máximo (mV) |",
        "|---:|---:|---:|---:|---:|",
        f"| {fmt(score_stats['minimo'])} | {fmt(score_stats['q1'])} | "
        f"{fmt(score_stats['mediana'])} | {fmt(score_stats['q3'])} | {fmt(score_stats['maximo'])} |",
        "", "## Movimientos de actuadores", "",
        "No se cuentan la perturbación previa ni la vuelta deliberada a cero; sólo movimientos decididos por el calibrador.", "",
        "| Actuador | Movimientos | Motivos (cantidad) |",
        "|---|---:|---|",
    ])
    total_movimientos = Counter()
    motivos: dict[str, Counter[str]] = defaultdict(Counter)
    for intento in intentos:
        actuadores = intento.get("actuadores_json", {})
        if not isinstance(actuadores, dict):
            continue
        for stage in range(4):
            dato = actuadores.get(str(stage), {})
            if not isinstance(dato, dict):
                continue
            total_movimientos[str(stage)] += int(dato.get("movimientos", 0))
            for motivo in dato.get("motivos", []):
                motivos[str(stage)][str(motivo)] += 1
    for stage in range(4):
        detalle = "; ".join(f"{motivo} ({cantidad})" for motivo, cantidad in
                            sorted(motivos[str(stage)].items())) or "sin movimientos"
        lineas.append(f"| IDAC{stage} | {total_movimientos[str(stage)]} | {detalle} |")
    if total_movimientos["0"] == 0:
        lineas.extend(["", "IDAC0 no se movió durante la campaña. Esto significa que la placa no lo necesitó hoy; no demuestra que sobre."])

    lineas.extend([
        "", "## Comparación de perturbaciones", "",
        "| Modo | Intentos | Tasa de éxito | Calibración media (s) | Total medio (s) |",
        "|---|---:|---:|---:|---:|",
    ])
    por_modo = {}
    for modo in ("aleatorio", "golpe"):
        grupo = [item for item in intentos if item.get("modo") == modo]
        e = sum(bool(item.get("exito")) for item in grupo)
        cal = estadistica(numero(item.get("duracion_calibracion_s")) for item in grupo
                          if numero(item.get("duracion_calibracion_s")) is not None)
        tot = estadistica(numero(item.get("duracion_total_s")) for item in grupo
                          if numero(item.get("duracion_total_s")) is not None)
        por_modo[modo] = {"n": len(grupo), "exitos": e, "cal": cal, "total": tot}
        lineas.append(
            f"| {modo} | {len(grupo)} | {fmt_pct(100.0 * e / len(grupo) if grupo else None)} | "
            f"{fmt(cal['media'])} | {fmt(tot['media'])} |"
        )
    a, g = por_modo["aleatorio"], por_modo["golpe"]
    if a["n"] and g["n"]:
        delta_t = None if a["cal"]["media"] is None or g["cal"]["media"] is None else g["cal"]["media"] - a["cal"]["media"]
        delta_p = 100.0 * (g["exitos"] / g["n"] - a["exitos"] / a["n"])
        lineas.extend([
            "",
            f"Frente al modo aleatorio, el modo golpe cambió el tiempo medio de calibración en {fmt(delta_t)} s "
            f"y la tasa de éxito en {fmt(delta_p)} puntos porcentuales. Esa diferencia cuantifica el costo observado "
            "de dejar la cadena contra un riel antes de calibrar.",
        ])

    lineas.extend([
        "", "## Limitaciones", "",
        "- `ToggleReset` resetea el PSoC, pero no corta la alimentación. El electrolítico de 680 µF conserva carga; "
        "por lo tanto, esto no es un ciclo de alimentación real y no prueba la rampa de la fuente.",
        "- La perturbación de IDAC reproduce un estado analógico desplazado, no todas las condiciones posibles de un arranque de campo.",
        "- Los datos corresponden a un solo nodo. No cuantifican dispersión entre placas.",
        "- Cada agregado de este informe se obtiene de las filas del CSV citado al comienzo; las evidencias crudas por intento figuran en su columna `evidencia_json`.",
        "",
    ])
    escritura_atomica(path, "\n".join(lineas))


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simular", action="store_true",
                        help="no abre COM8, no llama KitProg y no mata procesos")
    parser.add_argument("--intentos", type=int, default=100)
    parser.add_argument("--reset-cada", type=int, default=4,
                        help="resetea en 1, 1+N, 1+2N... (predeterminado: 4)")
    parser.add_argument("--espera-random-s", type=float, default=60.0)
    parser.add_argument("--modo", choices=("aleatorio", "golpe", "mixto"), default="mixto")
    parser.add_argument("--modo-golpe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--modo-mixto", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--max-h", type=float, default=9.0)
    parser.add_argument("--abortar-tras", type=int, default=8)
    parser.add_argument("--timeout-psoc-s", type=float, default=300.0)
    parser.add_argument("--timeout-calibracion-s", type=float, default=1800.0)
    parser.add_argument("--gains", default=",".join(map(str, GANANCIAS_DEFAULT)))
    parser.add_argument(
        "--ganancias-por-intento", type=int, default=1,
        help=("cuantas ganancias calibra CADA intento; 0 = todas. Elias fijo que "
              "la ganancia se elige una vez por campana y no se cambia entre "
              "disparos, asi que calibrar las seis en cada intento mide un "
              "requisito que no existe y multiplica por seis el costo. Con 1 se "
              "rota la ganancia entre intentos y se obtiene estadistica POR "
              "ganancia en la misma noche."))
    parser.add_argument("--port", default="COM8")
    parser.add_argument("--ppcli-dir", default=PPCLI_DIR_DEFAULT)
    parser.add_argument("--kitprog", default=KITPROG_DEFAULT)
    parser.add_argument("--semilla", type=int, default=None)
    parser.add_argument("--directorio-salida", type=Path, default=DATOS_DIR)
    return parser


def validar_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> list[int]:
    if args.modo_golpe:
        args.modo = "golpe"
    if args.modo_mixto:
        args.modo = "mixto"
    for nombre in ("intentos", "reset_cada", "abortar_tras"):
        if getattr(args, nombre) < 1:
            parser.error(f"--{nombre.replace('_', '-')} debe ser >= 1")
    for nombre in ("espera_random_s", "max_h", "timeout_psoc_s", "timeout_calibracion_s"):
        if getattr(args, nombre) <= 0:
            parser.error(f"--{nombre.replace('_', '-')} debe ser > 0")
    try:
        ganancias = [int(item.strip()) for item in args.gains.split(",") if item.strip()]
    except ValueError:
        parser.error("--gains debe contener enteros separados por coma")
    if not ganancias or len(set(ganancias)) != len(ganancias) or any(g <= 0 for g in ganancias):
        parser.error("--gains debe contener ganancias positivas, sin duplicados")
    return ganancias


def main() -> int:
    parser = construir_parser()
    args = parser.parse_args()
    ganancias = validar_args(parser, args)
    rng = random.Random(args.semilla)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    sufijo = "_SIMULADO" if args.simular else ""
    salida_dir = args.directorio_salida.resolve()
    csv_path = salida_dir / f"ARRANQUE_FRIO_{sello}{sufijo}.csv"
    json_path = salida_dir / f"ARRANQUE_FRIO_{sello}{sufijo}.json"
    informe_path = LAB_DIR / f"RESULTADOS_ARRANQUE_FRIO_{datetime.now():%Y-%m-%d}.md"
    limite = time.monotonic() + args.max_h * 3600.0
    intentos: list[dict[str, Any]] = []
    consecutivos = 0
    motivo_fin = "se completaron los intentos planeados"
    config = {
        "simular": args.simular, "intentos": args.intentos,
        "reset_cada": args.reset_cada, "espera_random_s": args.espera_random_s,
        "modo": args.modo, "max_h": args.max_h, "abortar_tras": args.abortar_tras,
        "timeout_psoc_s": args.timeout_psoc_s,
        "timeout_calibracion_s": args.timeout_calibracion_s,
        "gains": ganancias, "port": args.port, "ppcli_dir": args.ppcli_dir,
        "kitprog": args.kitprog, "semilla": args.semilla,
    }
    persistir(csv_path, json_path, config, intentos, ganancias, "en curso", None)

    for n in range(1, args.intentos + 1):
        if time.monotonic() >= limite:
            motivo_fin = "se agotó --max-h antes de iniciar otro intento"
            break
        inicio_intento = time.monotonic()
        modo = modo_del_intento(args.modo, n)
        _, idac0, idac1 = elegir_estado(modo, rng)
        # Rotacion round-robin: el intento 1 calibra la primera ganancia, el 2 la
        # segunda, y asi. Con 100 intentos y 6 ganancias quedan ~17 por ganancia.
        if args.ganancias_por_intento <= 0:
            ganancias_intento = list(ganancias)
        else:
            k = args.ganancias_por_intento
            inicio = ((n - 1) * k) % len(ganancias)
            ganancias_intento = [ganancias[(inicio + i) % len(ganancias)]
                                 for i in range(min(k, len(ganancias)))]
        reset_programado = (n - 1) % args.reset_cada == 0
        intento = crear_intento_base(n, modo, idac0, idac1, reset_programado)
        intento["ganancias_intento"] = ",".join(map(str, ganancias_intento))
        evidencia_path = salida_dir / f"evidencia_{sello}_intento_{n:04d}{sufijo}.json"
        intento["evidencia_json"] = ruta_registrable(evidencia_path)
        log(f"[{n}/{args.intentos}] modo={modo}; IDAC0={idac0}; IDAC1={idac1}; "
            f"reset={'sí' if reset_programado else 'no'}")
        try:
            if args.simular:
                intento["huerfanos_matados"] = []
                if reset_programado:
                    reset_simulado_ok = rng.random() >= 0.05
                    intento.update(
                        reset_realizado=False,
                        reset_ok=reset_simulado_ok,
                        reset_detalle=("simulado; KitProg no invocado" if reset_simulado_ok
                                       else "fallo simulado de reset; KitProg no invocado"),
                    )
                intento["preparacion_ok"] = True
                intento["espera_psoc_s"] = rng.uniform(0.2, 1.5)
                intento["espera_random_s"] = args.espera_random_s
                exit_code, stdout, stderr, duracion_cal = simular_evidencia(
                    n, modo, ganancias_intento, rng, evidencia_path)
                timeout = False
            else:
                try:
                    intento["huerfanos_matados"] = matar_calibradores_huerfanos()
                except Exception as exc:
                    intento["error"] = f"limpieza de huérfanos: {type(exc).__name__}: {exc}"
                if reset_programado:
                    intento["reset_realizado"] = True
                    try:
                        reset = psoc_reset(args.ppcli_dir, args.kitprog)
                        intento["reset_ok"] = bool(reset.get("ok"))
                        intento["reset_detalle"] = str(reset.get("detail", ""))
                    except Exception as exc:
                        intento["reset_ok"] = False
                        intento["reset_detalle"] = f"{type(exc).__name__}: {exc}"
                preparacion = preparar_estado_real(
                    args.port, idac0, idac1, args.espera_random_s,
                    args.timeout_psoc_s, limite,
                )
                intento["preparacion_ok"] = bool(preparacion["ok"])
                intento["espera_psoc_s"] = preparacion["espera_psoc_s"]
                intento["espera_random_s"] = preparacion["espera_random_s"]
                if not preparacion["ok"]:
                    raise RuntimeError(preparacion["detalle"])
                restante = min(args.timeout_calibracion_s, limite - time.monotonic())
                if restante <= 0.0:
                    raise TimeoutError("se agotó --max-h antes del subproceso")
                exit_code, stdout, stderr, duracion_cal, timeout = ejecutar_calibrador(
                    args.port, ganancias_intento, evidencia_path, restante)

            intento["exit_code"] = exit_code
            intento["stdout"] = stdout
            intento["stderr"] = stderr
            intento["duracion_calibracion_s"] = duracion_cal
            if timeout:
                intento["error"] = (intento["error"] + "; " if intento["error"] else "") + "timeout del calibrador"
            evidencia = None
            try:
                evidencia = json.loads(evidencia_path.read_text(encoding="utf-8"))
            except Exception as exc:
                intento["error"] = (intento["error"] + "; " if intento["error"] else "") + (
                    f"evidencia no legible: {type(exc).__name__}: {exc}")
            analisis = analizar_evidencia(evidencia, ganancias_intento, exit_code)
            intento["exito"] = analisis["exito"]
            intento["ganancias_json"] = analisis["ganancias"]
            intento["taps_json"] = analisis["taps"]
            intento["actuadores_json"] = analisis["actuadores"]
            intento["puntaje_mv"] = analisis["puntaje_mv"]
            intento["duracion_fase1_s"] = analisis["fase1_s"]
            intento["duracion_fase2_s"] = analisis["fase2_s"]
        except Exception as exc:  # guardia exterior: ninguna corrida tumba la campaña
            intento["error"] = (intento["error"] + "; " if intento["error"] else "") + f"{type(exc).__name__}: {exc}"
            if intento["exit_code"] is None:
                intento["exit_code"] = 125
            intento["ganancias_json"] = {
                str(g): {"paso": False, "error_lpo_mv": None, "duracion_s": None}
                for g in ganancias_intento
            }
            intento["taps_json"] = {str(g): {str(ch): None for ch in range(5)} for g in ganancias_intento}
            intento["actuadores_json"] = {
                str(stage): {"movimientos": 0, "motivos": []} for stage in range(4)
            }
        finally:
            intento["fin"] = datetime.now().isoformat(timespec="seconds")
            medido = time.monotonic() - inicio_intento
            if args.simular:
                intento["duracion_total_s"] = (
                    (numero(intento.get("espera_psoc_s")) or 0.0)
                    + (numero(intento.get("espera_random_s")) or 0.0)
                    + (numero(intento.get("duracion_calibracion_s")) or 0.0)
                )
            else:
                intento["duracion_total_s"] = medido
            completar_columnas_ganancia(intento, ganancias)
            intentos.append(intento)
            consecutivos = 0 if intento["exito"] else consecutivos + 1
            persistir(csv_path, json_path, config, intentos, ganancias, "en curso", None)
            log(f"  {'PASS' if intento['exito'] else 'FAIL'}; exit={intento['exit_code']}; "
                f"consecutivos={consecutivos}; guardado={csv_path.name}")

        if consecutivos >= args.abortar_tras:
            motivo_fin = f"se alcanzaron {consecutivos} intentos fallidos consecutivos"
            break
        if time.monotonic() >= limite:
            motivo_fin = "se agotó --max-h al terminar el intento"
            break

    estado = "finalizada"
    persistir(csv_path, json_path, config, intentos, ganancias, estado, motivo_fin)
    generar_informe(informe_path, csv_path, json_path, intentos, ganancias,
                    args.intentos, motivo_fin, args.simular)
    log(f"CSV: {csv_path}")
    log(f"JSON: {json_path}")
    log(f"Informe: {informe_path}")
    log(f"Fin: {motivo_fin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
