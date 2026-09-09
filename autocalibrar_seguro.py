"""Autocalibración adaptativa segura de la cadena GEO, sin semillas.

Cada corrida empieza con los cuatro IDAC en cero. El controlador identifica
en la propia placa las pendientes locales y cierra el lazo hasta verificar los
cinco taps. Hay dos estrategias comparables: ``sequential`` respeta la cadena
etapa por etapa; ``coupled`` identifica un Jacobiano y mueve varios IDAC como
un bloque. Ninguna usa códigos precargados ni pendientes nominales.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import random
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Sequence

from testbench.core import console as con
from testbench.core.lab import GAIN_CODES, Lab, TAP_NAMES
from testbench.core.session import Session
from escala_banco import (BANCO_MAX_VALIDO_MV, BANCO_MIN_VALIDO_MV,
                          BANCO_VREF_MV, FACTOR)


def real_uv_to_bank_uv(value_uv: float) -> int:
    """Convierte una diferencia fisica a las unidades crudas del banco."""
    return int(round(value_uv / FACTOR))


def bank_uv_to_real_uv(value_uv: float) -> int:
    """Convierte una diferencia cruda del banco a microvoltios fisicos."""
    return int(round(value_uv * FACTOR))


def real_mv(value_bank_uv: float) -> float:
    return bank_uv_to_real_uv(value_bank_uv) / 1000.0


# ``Lab.measure_dc`` entrega microvoltios DEL BANCO, no microvoltios fisicos.
# La calibracion lineal medida esta centralizada en escala_banco.py. Mantener el
# controlador en unidades crudas evita introducir redondeos; todo limite de
# ingenieria se convierte una sola vez aqui.
TARGET_UV = int(round(BANCO_VREF_MV * 1000.0))
VALID_MIN_UV = int(round(BANCO_MIN_VALIDO_MV * 1000.0))
VALID_MAX_UV = int(round(BANCO_MAX_VALIDO_MV * 1000.0))
ADC_RAIL_MARGIN_UV = real_uv_to_bank_uv(25_000)
PGA_X50_CODE = GAIN_CODES.index(50)
GAIN_TO_CODE = {gain: code for code, gain in enumerate(GAIN_CODES)}
PRODUCTION_MAX_GAIN = 24

# Los actuadores exponen su rango normal completo. La seguridad no depende de
# recortar códigos: cada rampa se verifica contra los cinco taps y se revierte
# ante una guarda. Los cuatro IDAC están en 31,875 uA (0,125 uA/bit).
CODE_LIMITS = ((-255, 255), (-255, 255), (-255, 255), (-255, 255))
PROBE_CODES = (8, 12, 2, 8)
MAX_MOVE_CODES = (64, 80, 24, 64)
MAX_RAMP_CODES = 16
MAX_PI_STEP = (8, 16, 2, 12)
# Topología de seis canales del 2026-09-08:
# IDAC0->PGA, IDAC1->BP, IDAC2->OPA_SUM (antes de PGAout), IDAC3->LP.
OBJECTIVE_CHANNELS = ((0,), (1,), (2,), (4,))

FINAL_TOLERANCE_UV = real_uv_to_bank_uv(20_000)
CONTROL_TOLERANCE_UV = real_uv_to_bank_uv(8_000)
PRE_GAIN_SUM_LIMIT_UV = real_uv_to_bank_uv(950_000)
GUARD_UV = tuple(real_uv_to_bank_uv(value) for value in
                 (500_000, 500_000, 1_000_000, 2_000_000, 2_000_000))
MIN_IDENT_EFFECT_UV = tuple(real_uv_to_bank_uv(value) for value in
                            (3_000, 3_000, 2_000, 4_000))
# Una sola resta entre dos puntos no distingue autoridad de una deriva lenta.
# Para las plantas rápidas se exige el efecto completo low->high en los dos
# sentidos temporales (ABBA), además de margen sobre el ruido observado. En
# IDAC2 el umbral físico es deliberadamente alto: con 1,5 kohm y PGAout x24 se
# esperan ~140 mV entre -16 y +16; los 2,5 mV vistos en placa eran deriva.
MIN_BIPOLAR_AUTHORITY_UV = tuple(real_uv_to_bank_uv(12_000) for _ in range(4))
AUTHORITY_NOISE_MULTIPLIER = 6.0
AUTHORITY_MIN_RATIO = 0.50
MAX_FINAL_DRIFT_UV = real_uv_to_bank_uv(15_000)
TAU_EST_STAGE = 1
TAU_EST_CHANNEL = 1
TAU_EST_DELTA = 60
TAU_EST_SAMPLE_S = 5.0
TAU_EST_DURATION_S = 90.0
TAU_INITIAL_SETTLE_S = 60.0
SETTLE_TAU_MULTIPLIER = 2.0
STARTUP_TIMEOUT_S = 180.0
STARTUP_MIN_S = 30.0
STARTUP_POLL_S = 2.0
STARTUP_STABLE_DELTAS_UV = tuple(real_uv_to_bank_uv(value) for value in
                                 (5_000, 2_500, 5_000, 8_000, 8_000))
STARTUP_STABLE_WINDOWS = 4


def checked(call, description: str) -> None:
    for _ in range(3):
        if call():
            return
        time.sleep(0.3)
    raise RuntimeError(f"sin ACK: {description}")


def write_events_csv(payload: dict, path: Path) -> None:
    """Exporta la evidencia a una tabla simple, además del JSON sin pérdida."""
    fields = ("gain", "strategy", "pass", "event_index", "at", "kind",
              "message", "codes", "tau_s", "channel", "error_uv",
              "pp_uv", "spread_uv", "data_json")
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in payload.get("results", []):
            events = result.get("events", [])
            for index, event in enumerate(events):
                observation = event.get("observation") or {}
                errors = observation.get("errors_uv") or {}
                channels = list(errors) or [""]
                for channel in channels:
                    writer.writerow({
                        "gain": result.get("gain"),
                        "strategy": result.get("strategy"),
                        "pass": result.get("pass"),
                        "event_index": index,
                        "at": event.get("at"),
                        "kind": event.get("kind"),
                        "message": event.get("message"),
                        "codes": json.dumps(event.get("codes", result.get("codes"))),
                        "tau_s": event.get("tau_s", (event.get("metrics") or {}).get("tau_s")),
                        "channel": channel,
                        "error_uv": errors.get(channel) if channel != "" else "",
                        "pp_uv": (observation.get("pp_uv") or {}).get(channel, ""),
                        "spread_uv": (observation.get("spread_uv") or {}).get(channel, ""),
                        "data_json": json.dumps(event, ensure_ascii=False,
                                                separators=(",", ":")),
                    })


def clamp_code(stage: int, code: float) -> int:
    lo, hi = CODE_LIMITS[stage]
    return max(lo, min(hi, int(round(code))))


def score(errors: dict[int, float], channels: Iterable[int]) -> tuple[float, float]:
    values = [abs(errors[ch]) for ch in channels]
    return max(values), math.sqrt(sum(value * value for value in values) /
                                  len(values))


def estimate_tau_series(points: Sequence[tuple[float, float]],
                        noise_uv: float) -> tuple[float | None, dict]:
    """Ajusta ``c + A*exp(-t/tau)`` sin necesitar conocer el valor final.

    Ch1 mostró en placa que un único triplete a 0/45/90 s puede quedar mal
    condicionado: a veces ya se asentó a 45 s y otras la deriva domina un
    escalón débil. Un barrido de tau sobre toda la serie usa la misma planta de
    primer orden, pero aprovecha todas las muestras y entrega métricas para
    rechazar, nunca inventar, una constante de tiempo.
    """
    if len(points) < 8:
        return None, {"reason": "pocas muestras"}
    t0 = points[0][0]
    ts = [float(t - t0) for t, _ in points]
    ys = [float(y) for _, y in points]
    mean_y = statistics.fmean(ys)
    sst = sum((y - mean_y) ** 2 for y in ys)
    best: tuple[float, float, float, float] | None = None
    for quarter in range(4, 481):  # 1,00 .. 120,00 s
        tau = quarter / 4.0
        xs = [math.exp(-t / tau) for t in ts]
        mean_x = statistics.fmean(xs)
        var_x = sum((x - mean_x) ** 2 for x in xs)
        if var_x <= 1e-12:
            continue
        amplitude = sum((x - mean_x) * (y - mean_y)
                        for x, y in zip(xs, ys)) / var_x
        final = mean_y - amplitude * mean_x
        sse = sum((y - (final + amplitude * x)) ** 2
                  for x, y in zip(xs, ys))
        if best is None or sse < best[0]:
            best = (sse, tau, amplitude, final)
    if best is None or sst <= 1.0:
        return None, {"reason": "serie plana"}
    sse, tau, amplitude, final = best
    residual_rms = math.sqrt(sse / len(points))
    r2 = 1.0 - sse / sst
    metrics = {"tau_s": tau, "amplitude_uv": amplitude,
               "final_uv": final, "r2": r2,
               "residual_rms_uv": residual_rms,
               "noise_uv": noise_uv, "samples": len(points)}
    if abs(amplitude) < max(8.0 * max(noise_uv, 1.0),
                            real_uv_to_bank_uv(2_000)):
        metrics["reason"] = "excursion insuficiente"
        return None, metrics
    if r2 < 0.70 or residual_rms > max(4.0 * noise_uv,
                                       real_uv_to_bank_uv(2_000)):
        metrics["reason"] = "no parece primer orden"
        return None, metrics
    if tau <= 1.0 or tau >= 120.0:
        metrics["reason"] = "optimo en el borde"
        return None, metrics
    return tau, metrics


def choose_scalar(stage: int, current: int, errors: dict[int, float],
                  slopes: dict[int, float]) -> tuple[int, dict[int, float]]:
    """Mínimo entero min-max del modelo local de un actuador."""
    channels = OBJECTIVE_CHANNELS[stage]
    hard_lo, hard_hi = CODE_LIMITS[stage]
    trust = MAX_MOVE_CODES[stage]
    lo, hi = max(hard_lo, current - trust), min(hard_hi, current + trust)
    best_code = current
    best_pred = {ch: float(errors[ch]) for ch in channels}
    best = (*score(best_pred, channels), 0)
    for code in range(lo, hi + 1):
        pred = {ch: errors[ch] + slopes[ch] * (code - current)
                for ch in channels}
        candidate_score = (*score(pred, channels), abs(code - current))
        if candidate_score < best:
            best, best_code, best_pred = candidate_score, code, pred
    return best_code, best_pred


def solve_dense(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Gauss-Jordan con pivoteo; matrices de como máximo 4x4."""
    n = len(rhs)
    augmented = [list(matrix[row]) + [rhs[row]] for row in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) < 1e-12:
            raise RuntimeError("Jacobiano singular")
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        divisor = augmented[col][col]
        augmented[col] = [value / divisor for value in augmented[col]]
        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            augmented[row] = [a - factor * b for a, b in
                              zip(augmented[row], augmented[col])]
    return [augmented[row][-1] for row in range(n)]


def joint_delta(jacobian: dict[int, dict[int, float]],
                errors: dict[int, float], stages: Sequence[int],
                channels: Sequence[int]) -> list[float]:
    """Gauss-Newton regularizado: argmin ||e + J delta||²."""
    n = len(stages)
    normal = [[0.0] * n for _ in range(n)]
    rhs = [0.0] * n
    for ch in channels:
        row = [jacobian[stage][ch] for stage in stages]
        for i in range(n):
            rhs[i] -= row[i] * errors[ch]
            for k in range(n):
                normal[i][k] += row[i] * row[k]
    diagonal = max(max(normal[i][i] for i in range(n)), 1.0)
    for i in range(n):
        normal[i][i] += diagonal * 1e-6
    return solve_dense(normal, rhs)


def choose_joint(codes: Sequence[int], errors: dict[int, float],
                 jacobian: dict[int, dict[int, float]],
                 stages: Sequence[int], channels: Sequence[int]) -> list[int]:
    """Paso acoplado acotado, con línea de búsqueda y refinamiento entero."""
    raw_delta = joint_delta(jacobian, errors, stages, channels)
    best_codes = list(codes)
    best_score = (*score(errors, channels), 0)
    for alpha in (1.0, 0.5, 0.25):
        candidate = list(codes)
        for stage, delta in zip(stages, raw_delta):
            delta = max(-MAX_MOVE_CODES[stage],
                        min(MAX_MOVE_CODES[stage], delta * alpha))
            candidate[stage] = clamp_code(stage, codes[stage] + delta)
        predicted = {
            ch: errors[ch] + sum(jacobian[stage][ch] *
                                 (candidate[stage] - codes[stage])
                                 for stage in stages)
            for ch in channels
        }
        if any(abs(predicted[ch]) >= GUARD_UV[ch] for ch in channels):
            continue
        candidate_score = (*score(predicted, channels),
                           sum(abs(candidate[s] - codes[s]) for s in stages))
        if candidate_score < best_score:
            best_score, best_codes = candidate_score, candidate

    # La solución continua minimiza RMS. Esta vecindad entera mejora el peor
    # tap, que es el criterio de aceptación real.
    for _ in range(2):
        changed = False
        for stage in stages:
            local_best = best_codes[stage]
            local_score = best_score
            for offset in range(-3, 4):
                candidate_code = clamp_code(stage, best_codes[stage] + offset)
                candidate = list(best_codes)
                candidate[stage] = candidate_code
                predicted = {
                    ch: errors[ch] + sum(jacobian[s][ch] *
                                         (candidate[s] - codes[s])
                                         for s in stages)
                    for ch in channels
                }
                candidate_score = (*score(predicted, channels),
                                   sum(abs(candidate[s] - codes[s])
                                       for s in stages))
                if candidate_score < local_score:
                    local_score, local_best = candidate_score, candidate_code
            if local_best != best_codes[stage]:
                best_codes[stage] = local_best
                best_score = local_score
                changed = True
        if not changed:
            break
    return best_codes


@dataclass
class Observation:
    errors_uv: dict[int, int]
    pp_uv: dict[int, int]
    spread_uv: dict[int, int]
    samples: list[dict[int, dict[str, int]]]

    def as_dict(self) -> dict:
        return {"errors_uv": self.errors_uv, "pp_uv": self.pp_uv,
                "spread_uv": self.spread_uv, "samples": self.samples}


class AdaptiveCalibrator:
    def __init__(self, lab: Lab, *, samples: int = 3, period_s: float = 1.0,
                 verbose: bool = True, tau_override_s: float | None = None) -> None:
        self.lab = lab
        self.samples = samples
        self.period_s = period_s
        self.verbose = verbose
        self.tau_s = tau_override_s
        self.codes = [0, 0, 0, 0]
        self.events: list[dict] = []
        self.read_count = 0
        self.control_moves = 0
        self.pgaout_gain = 1
        self.bp_slope_uv_per_code: float | None = None
        self.bp_zero_error_uv: float | None = None
        self.stage2_slope_x1_uv_per_code: float | None = None
        self.final_tolerance_uv = {
            0: GUARD_UV[0], 1: GUARD_UV[1], 2: GUARD_UV[2],
            3: FINAL_TOLERANCE_UV, 4: FINAL_TOLERANCE_UV,
        }

    def log(self, kind: str, message: str, **data) -> None:
        event = {"at": datetime.now().isoformat(timespec="seconds"),
                 "kind": kind, "message": message, **data}
        self.events.append(event)
        if self.verbose:
            # Algunos lanzadores de Windows conservan cp1252 aun dentro de un
            # PTY. La evidencia JSON mantiene Unicode; la consola usa escapes
            # sólo cuando su codec no puede representar flechas/letras griegas.
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            printable = message.encode(encoding, errors="backslashreplace").decode(
                encoding)
            print(printable, flush=True)

    def wait(self, seconds: float) -> None:
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end:
            time.sleep(min(2.0, end - time.monotonic()))

    def read_once(self, *, enforce_guards: bool = True) -> dict[int, dict[str, int]]:
        row: dict[int, dict[str, int]] = {}
        self.read_count += 1
        for ch in range(5):
            point = None
            for _ in range(3):
                point = self.lab.measure_dc(ch, 3)
                if point is not None and point.ok:
                    break
                time.sleep(0.2)
            if point is None or not point.ok:
                raise RuntimeError(f"sin lectura válida en ch{ch} {TAP_NAMES[ch]}")
            error = int(point.mean_uv) - TARGET_UV
            # Fuera de la recta medida del banco el ADC devuelve numeros que
            # no representan una tension fisica. Se deja 25 mV reales de
            # margen respecto de ambos rieles y se aborta antes de otro paso.
            if (enforce_guards and not
                    (VALID_MIN_UV + ADC_RAIL_MARGIN_UV <= int(point.mean_uv) <=
                     VALID_MAX_UV - ADC_RAIL_MARGIN_UV)):
                raise RuntimeError(
                    f"lectura imposible ch{ch} {TAP_NAMES[ch]}: "
                    f"banco={point.mean_uv / 1000:.3f}mV, "
                    f"error real={real_mv(error):+.1f}mV")
            if enforce_guards and abs(error) > GUARD_UV[ch]:
                raise RuntimeError(
                    f"guarda ch{ch} {TAP_NAMES[ch]}: "
                    f"{real_mv(error):+.1f} mV reales")
            row[ch] = {"mean_uv": int(point.mean_uv), "error_uv": error,
                       "pp_uv": int(point.pp_uv)}
        return row

    def observe(self, label: str, *, samples: int | None = None) -> Observation:
        count = samples or self.samples
        rows = []
        for index in range(count):
            rows.append(self.read_once())
            if index + 1 < count:
                self.wait(self.period_s)
        errors = {ch: int(round(statistics.median(
            row[ch]["error_uv"] for row in rows))) for ch in range(5)}
        pp = {ch: int(round(statistics.median(
            row[ch]["pp_uv"] for row in rows))) for ch in range(5)}
        spread = {ch: max(row[ch]["error_uv"] for row in rows) -
                      min(row[ch]["error_uv"] for row in rows)
                  for ch in range(5)}
        obs = Observation(errors, pp, spread, rows)
        self.log("observation", f"    {label}: " + " ".join(
            f"{TAP_NAMES[ch]}={real_mv(errors[ch]):+.1f}mV" for ch in range(5)),
            label=label, observation=obs.as_dict(), codes=list(self.codes))
        return obs

    def wait_startup_stable(self) -> Observation:
        """Espera estabilidad real de los cinco taps, sin un retardo ciego.

        Ignora el pico a pico de 50 Hz y mira sólo el desplazamiento de la
        media entre ventanas. Exige varias ventanas consecutivas; durante esta
        fase no aplica correcciones. En el banco simulado retorna de inmediato.
        """
        if self.tau_s is not None and self.tau_s <= 0.0:
            return self.observe("arranque simulado estable")
        started = time.monotonic()
        previous: dict[int, dict[str, int]] | None = None
        stable_windows = 0
        rows: list[dict[int, dict[str, int]]] = []
        while time.monotonic() - started <= STARTUP_TIMEOUT_S:
            row = self.read_once(enforce_guards=False)
            rows.append(row)
            if previous is not None:
                deltas = {ch: abs(row[ch]["error_uv"] -
                                  previous[ch]["error_uv"])
                          for ch in range(5)}
                inside_adc = all(
                    VALID_MIN_UV + ADC_RAIL_MARGIN_UV <= row[ch]["mean_uv"] <=
                    VALID_MAX_UV - ADC_RAIL_MARGIN_UV for ch in range(5))
                stable = (inside_adc and
                          all(deltas[ch] <= STARTUP_STABLE_DELTAS_UV[ch]
                              for ch in range(5)))
                stable_windows = stable_windows + 1 if stable else 0
                if (stable_windows >= STARTUP_STABLE_WINDOWS and
                        time.monotonic() - started >= STARTUP_MIN_S):
                    self.log(
                        "startup_stable",
                        "  arranque estable: " + " ".join(
                            f"ch{ch} Δ={real_mv(deltas[ch]):.1f}mV"
                            for ch in range(5)),
                        elapsed_s=time.monotonic() - started,
                        stable_windows=stable_windows,
                        deltas_uv=deltas,
                        thresholds_uv=STARTUP_STABLE_DELTAS_UV,
                        samples=rows,
                    )
                    return self.observe("estado neutro estable")
            previous = row
            self.wait(STARTUP_POLL_S)
        self.log("startup_unstable", "  arranque no alcanzó estabilidad",
                 elapsed_s=time.monotonic() - started, samples=rows,
                 thresholds_uv=STARTUP_STABLE_DELTAS_UV)
        raise RuntimeError("los cinco taps no se estabilizaron en 180 s")

    def settle_for(self, stage: int) -> float:
        del stage
        if self.tau_s is None:
            raise RuntimeError("tau todavía no fue medido")
        return SETTLE_TAU_MULTIPLIER * self.tau_s

    def set_code_direct(self, stage: int, code: int) -> None:
        code = clamp_code(stage, code)
        checked(lambda: self.lab.set_idac(stage, code), f"IDAC{stage}={code}")
        self.codes[stage] = code

    def move_stage(self, stage: int, target: int, *, verify: bool = True) -> None:
        target = clamp_code(stage, target)
        last_safe = self.codes[stage]
        while self.codes[stage] != target:
            direction = 1 if target > self.codes[stage] else -1
            nxt = self.codes[stage] + direction * min(
                MAX_RAMP_CODES, abs(target - self.codes[stage]))
            self.set_code_direct(stage, nxt)
            if verify:
                try:
                    self.read_once()
                except Exception:
                    self.set_code_direct(stage, last_safe)
                    raise
            last_safe = nxt

    def safe_zero(self) -> None:
        """Siempre parte de cero y PGAout x1; jamás carga una semilla."""
        # Cambiar PGAgain puede restaurar el punto EEPROM de operación. Por eso
        # se hace primero y acto seguido se fuerza x1/cero: la identificación
        # nunca observa ni reutiliza ese resultado persistido.
        checked(lambda: self.lab.set_gain("pga", PGA_X50_CODE), "PGAgain x50")
        self.restore_neutral()
        self.wait_startup_stable()

    def restore_neutral(self) -> None:
        """Restauración inmediata para finally: no espera ni calibra."""
        checked(lambda: self.lab.set_gain("pgaout", GAIN_TO_CODE[1]),
                "PGAout x1")
        self.pgaout_gain = 1
        for stage in (2, 3, 0, 1):
            self.set_code_direct(stage, 0)

    def measure_tau_bp(self) -> float:
        """Mide tau del polo dominante con IDAC1 -> BPo (AMux ch1).

        OPA_SUMo (ch2) se sigue leyendo como guarda, pero no participa del
        estimador: tau pertenece al pasabanda y se observa en su propio tap.
        """
        if self.tau_s is not None:
            return self.tau_s
        for direction in (1, -1):
            base = self.observe("base para estimar tau", samples=3)
            self.bp_zero_error_uv = float(base.errors_uv[TAU_EST_CHANNEL])
            self.set_code_direct(TAU_EST_STAGE, direction * TAU_EST_DELTA)
            started = time.monotonic()
            points: list[tuple[float, float]] = []
            next_sample = 0.0
            while next_sample <= TAU_EST_DURATION_S + 1e-6:
                self.wait(next_sample - (time.monotonic() - started))
                point = None
                for _ in range(3):
                    point = self.lab.measure_dc(TAU_EST_CHANNEL, 3)
                    if point is not None and point.ok:
                        break
                    self.wait(0.2)
                if point is None or not point.ok:
                    points = []
                    break
                error = point.mean_uv - TARGET_UV
                if abs(error) > GUARD_UV[TAU_EST_CHANNEL]:
                    self.set_code_direct(TAU_EST_STAGE, 0)
                    raise RuntimeError("guarda BPo durante estimación de tau")
                points.append((time.monotonic() - started, error))
                next_sample += TAU_EST_SAMPLE_S
            self.set_code_direct(TAU_EST_STAGE, 0)
            if not points:
                continue
            # pp incluye la componente de 50 Hz dentro de cada ventana. Para
            # estimar la incertidumbre de la MEDIA se usa la dispersión entre
            # ventanas de base, que fue 5-25 veces menor en la prueba real.
            noise = max(base.spread_uv[TAU_EST_CHANNEL], 100)
            tau, metrics = estimate_tau_series(points, noise)
            self.log("tau_measurement",
                     f"  tau BP medido: {tau if tau else 0:.2f} s",
                     direction=direction, delta_code=TAU_EST_DELTA,
                     points=points, metrics=metrics, tau_s=tau)
            if tau is not None:
                self.tau_s = tau
                settled_effect = float(metrics["final_uv"] -
                                       self.bp_zero_error_uv)
                self.bp_slope_uv_per_code = settled_effect / float(
                    direction * TAU_EST_DELTA)
                # En el pasabanda la referencia produce precisamente un
                # TRANSITORIO que decae: no se exige autoridad DC final. La
                # amplitud y el ajuste exponencial ya fueron validados por
                # estimate_tau_series(). IDAC1 queda en cero al terminar.
                self.log("tau_transient_authority",
                         "  BP: tau válido; autoridad DC final no requerida",
                         settled_effect_uv=settled_effect,
                         apparent_dc_slope_uv_per_code=
                         self.bp_slope_uv_per_code)
                # El escalón de tau deja memoria al volver IDAC1 a cero. Antes
                # de cualquier corrección se completa 2*tau y se verifica el
                # punto restaurado; así el transitorio no se confunde con error.
                self.wait(SETTLE_TAU_MULTIPLIER * tau)
                self.observe("tau aceptado; BP restaurado y asentado")
                return tau
            self.wait(TAU_INITIAL_SETTLE_S)
        raise RuntimeError("el estimador de tau BP rechazó ambas polaridades")

    def identify_stage(self, stage: int) -> tuple[Observation, dict[int, float]]:
        current = self.codes[stage]
        hard_lo, hard_hi = CODE_LIMITS[stage]
        probe = PROBE_CODES[stage]
        low, high = max(hard_lo, current - probe), min(hard_hi, current + probe)
        if high - low < max(4, probe):
            raise RuntimeError(f"IDAC{stage} sin espacio seguro para identificar")
        self.move_stage(stage, low)
        self.wait(self.settle_for(stage))
        low_obs = self.observe(f"IDAC{stage} prueba {low}")
        self.move_stage(stage, high)
        self.wait(self.settle_for(stage))
        high_obs = self.observe(f"IDAC{stage} prueba {high}")
        self.move_stage(stage, current)
        self.wait(self.settle_for(stage))
        base = self.observe(f"IDAC{stage} vuelve {current}")
        channels = OBJECTIVE_CHANNELS[stage]
        slopes = {ch: (high_obs.errors_uv[ch] - low_obs.errors_uv[ch]) /
                      float(high - low) for ch in channels}
        effect = max(abs(high_obs.errors_uv[ch] - low_obs.errors_uv[ch])
                     for ch in channels)
        if effect < MIN_IDENT_EFFECT_UV[stage]:
            raise RuntimeError(f"IDAC{stage} sin autoridad medible ({effect} uV)")
        self.log("identification", f"    IDAC{stage}: " + ", ".join(
            f"ch{ch} {slopes[ch]:+.1f}uV/código" for ch in channels),
            stage=stage, low=low, high=high, slopes=slopes, effect_uv=effect)
        return base, slopes

    def identify_jacobian(self, stages: Sequence[int]) -> tuple[
            Observation, dict[int, dict[int, float]]]:
        base = self.observe("base del Jacobiano")
        jacobian: dict[int, dict[int, float]] = {}
        for stage in stages:
            current = self.codes[stage]
            lo_lim, hi_lim = CODE_LIMITS[stage]
            probe = PROBE_CODES[stage]
            low, high = max(lo_lim, current - probe), min(hi_lim, current + probe)
            self.move_stage(stage, low)
            self.wait(self.settle_for(stage))
            low_obs = self.observe(f"J IDAC{stage}={low}")
            self.move_stage(stage, high)
            self.wait(self.settle_for(stage))
            high_obs = self.observe(f"J IDAC{stage}={high}")
            self.move_stage(stage, current)
            self.wait(self.settle_for(stage))
            jacobian[stage] = {
                ch: (high_obs.errors_uv[ch] - low_obs.errors_uv[ch]) /
                    float(high - low) for ch in range(5)}
            effect = max(abs(high_obs.errors_uv[ch] - low_obs.errors_uv[ch])
                         for ch in range(5))
            if effect < MIN_IDENT_EFFECT_UV[stage]:
                raise RuntimeError(f"IDAC{stage} sin autoridad en Jacobiano")
        base = self.observe("base confirmada del Jacobiano")
        self.log("jacobian", "    Jacobiano identificado", stages=list(stages),
                 jacobian=jacobian)
        return base, jacobian

    def tune_stage(self, stage: int, max_iterations: int = 12) -> Observation:
        channels = OBJECTIVE_CHANNELS[stage]
        current_obs = self.observe(f"IDAC{stage} antes de regular")
        for iteration in range(max_iterations):
            old_score = score(current_obs.errors_uv, channels)
            if old_score[0] <= CONTROL_TOLERANCE_UV:
                return current_obs
            base, slopes = self.identify_stage(stage)
            candidate, predicted = choose_scalar(stage, self.codes[stage],
                                                 base.errors_uv, slopes)
            raw_step = candidate - self.codes[stage]
            limited = max(-MAX_PI_STEP[stage], min(MAX_PI_STEP[stage],
                                                   int(round(0.60 * raw_step))))
            if limited == 0 and raw_step:
                limited = 1 if raw_step > 0 else -1
            candidate = clamp_code(stage, self.codes[stage] + limited)
            if candidate == self.codes[stage]:
                raise RuntimeError(f"IDAC{stage} no puede reducir el error")
            origin = self.codes[stage]
            self.control_moves += 1
            self.log("control", f"    IDAC{stage} {origin}->{candidate}",
                     prediction_uv=predicted, iteration=iteration)
            self.move_stage(stage, candidate)
            self.wait(self.settle_for(stage))
            trial = self.observe(f"IDAC{stage} control aplicado")
            new_score = score(trial.errors_uv, channels)
            if new_score[0] <= CONTROL_TOLERANCE_UV:
                return trial
            if new_score >= old_score:
                self.log("rollback", f"    IDAC{stage} empeoró, vuelve a {origin}")
                self.move_stage(stage, origin)
                self.wait(self.settle_for(stage))
                current_obs = self.observe(f"IDAC{stage} revertido")
            else:
                current_obs = trial
        if score(current_obs.errors_uv, channels)[0] > FINAL_TOLERANCE_UV:
            raise RuntimeError(f"IDAC{stage} no convergió")
        return current_obs

    def identify_hadamard(self, stages: Sequence[int]) -> tuple[
            Observation, dict[int, dict[int, float]]]:
        """Identifica varias columnas simultáneamente con patrones ortogonales."""
        if len(stages) == 4:
            patterns = ((1, 1, 1, 1), (1, -1, 1, -1),
                        (1, 1, -1, -1), (1, -1, -1, 1))
        elif len(stages) == 2:
            # Cuatro vértices balanceados. Con sólo (+,+)/(+,-), la primera
            # columna quedaba confundida con la deriva/intercepto: eso produjo
            # una autoridad ficticia de IDAC2 en el primer ensayo físico.
            patterns = ((1, 1), (1, -1), (-1, 1), (-1, -1))
        else:
            raise ValueError("Hadamard implementado para 2 o 4 actuadores")
        origins = list(self.codes)
        base = self.observe("base del Jacobiano multiplexado")
        responses = []
        for row, pattern in enumerate(patterns):
            for stage, sign in zip(stages, pattern):
                self.move_stage(stage, origins[stage] + sign * PROBE_CODES[stage],
                                verify=False)
            # Lectura inmediata sólo como guarda; el dato del modelo se toma a 2 tau.
            self.read_once()
            self.wait(self.settle_for(stages[0]))
            responses.append(self.observe(f"patrón Hadamard {row + 1}"))
        for stage in stages:
            self.move_stage(stage, origins[stage], verify=False)
        self.read_once()
        self.wait(self.settle_for(stages[0]))
        base = self.observe("base restaurada del Jacobiano")

        jacobian: dict[int, dict[int, float]] = {}
        n = float(len(patterns))
        for column, stage in enumerate(stages):
            jacobian[stage] = {}
            for ch in range(5):
                numerator = sum(pattern[column] *
                                (response.errors_uv[ch] - base.errors_uv[ch])
                                for pattern, response in zip(patterns, responses))
                jacobian[stage][ch] = numerator / (n * PROBE_CODES[stage])
            effect = max(abs(jacobian[stage][ch] * PROBE_CODES[stage])
                         for ch in range(5))
            if effect < MIN_IDENT_EFFECT_UV[stage]:
                raise RuntimeError(f"IDAC{stage} sin autoridad en Jacobiano")
        self.log("hadamard_jacobian", "    Jacobiano multiplexado identificado",
                 stages=list(stages), patterns=patterns, jacobian=jacobian)
        return base, jacobian

    def tune_joint(self, stages: Sequence[int], channels: Sequence[int],
                   max_iterations: int = 12) -> Observation:
        """PI vectorial: cuatro esfuerzos pequeños calculados en cada ciclo."""
        current_obs, jacobian = self.identify_hadamard(stages)
        integral = {stage: 0.0 for stage in stages}
        kp, ki = 0.60, 0.08
        rollbacks = 0
        for iteration in range(max_iterations):
            old_score = score(current_obs.errors_uv, channels)
            if old_score[0] <= CONTROL_TOLERANCE_UV:
                return current_obs
            correction = joint_delta(jacobian, current_obs.errors_uv,
                                     stages, channels)
            candidate = list(self.codes)
            for stage, delta in zip(stages, correction):
                integral[stage] = max(-4 * MAX_PI_STEP[stage],
                                      min(4 * MAX_PI_STEP[stage],
                                          integral[stage] + delta))
                effort = kp * delta + ki * integral[stage]
                effort = max(-MAX_PI_STEP[stage],
                             min(MAX_PI_STEP[stage], effort))
                if abs(effort) >= 0.5:
                    candidate[stage] = clamp_code(stage,
                                                  self.codes[stage] + effort)
            if all(candidate[stage] == self.codes[stage] for stage in stages):
                raise RuntimeError(f"bloque {tuple(stages)} sin esfuerzo útil")
            origin = list(self.codes)
            self.control_moves += 1
            self.log("vector_pi", f"    PI vectorial {origin}->{candidate}",
                     iteration=iteration, correction=correction,
                     integral=integral, kp=kp, ki=ki)
            for stage in stages:
                self.move_stage(stage, candidate[stage], verify=False)
            self.read_once()
            self.wait(self.settle_for(stages[0]))
            trial = self.observe("ciclo PI vectorial")
            new_score = score(trial.errors_uv, channels)
            if new_score[0] <= CONTROL_TOLERANCE_UV:
                return trial
            if new_score[0] > old_score[0] * 1.20:
                rollbacks += 1
                self.log("rollback", "    PI vectorial empeoró >20%; revierte")
                for stage in reversed(stages):
                    self.move_stage(stage, origin[stage], verify=False)
                self.read_once()
                self.wait(self.settle_for(stages[0]))
                current_obs = self.observe("PI vectorial revertido")
                kp *= 0.5
                integral = {stage: 0.0 for stage in stages}
                if rollbacks >= 2:
                    current_obs, jacobian = self.identify_hadamard(stages)
                    rollbacks = 0
            else:
                current_obs = trial
        if score(current_obs.errors_uv, channels)[0] > FINAL_TOLERANCE_UV:
            raise RuntimeError(f"PI vectorial {tuple(stages)} no convergió")
        return current_obs

    def identify_dynamic_multiplex(self, stages: Sequence[int]) -> tuple[
            Observation, dict[int, dict[int, float]]]:
        """Identifica J sin esperar régimen en cada patrón.

        Usa y[k]=a*y[k-1]+(1-a)*(b+J*u[k]), con a calculado a partir del tau
        medido. Así cada patrón dura 0,25 tau y no 2 tau.
        """
        if len(stages) == 4:
            patterns = ((1, 1, 1, 1), (1, -1, 1, -1),
                        (1, 1, -1, -1), (1, -1, -1, 1))
        elif len(stages) == 2:
            patterns = ((1, 1), (1, -1), (-1, 1), (-1, -1))
        else:
            raise ValueError("multiplexado implementado para 2 o 4 actuadores")
        assert self.tau_s is not None
        instantaneous = self.tau_s <= 0.0
        # Sólo los ensayos que mueven la referencia compartida BP/SUM excitan
        # el polo lento. PGAout y LP se verifican realimentados con 2 s, sin
        # volver a cobrar tau_BP por cada planta rápida.
        includes_bp = TAU_EST_STAGE in stages
        dt = (0.0 if instantaneous else
              (max(2.0, 0.25 * self.tau_s) if includes_bp else 2.0))
        a = (0.0 if instantaneous or not includes_bp else
             math.exp(-dt / self.tau_s))
        origins = list(self.codes)
        base = self.observe("base dinámica multiplexada")
        previous = base
        steady_responses: list[dict[int, float]] = []
        for row, pattern in enumerate(patterns):
            for stage, sign in zip(stages, pattern):
                self.move_stage(stage, origins[stage] + sign * PROBE_CODES[stage],
                                verify=False)
            self.read_once()  # guarda inmediata; no se usa para el modelo
            self.wait(dt)
            measured = self.observe(f"patrón dinámico {row + 1}")
            steady = {}
            for ch in range(5):
                # PGAgain/ch0 no atraviesa el polo BP. Aplicarle la inversa
                # dinámica lenta exagera su columna y fue una fuente concreta
                # de error en el primer ensayo físico de cuatro actuadores.
                channel_a = a if includes_bp and ch != 0 else 0.0
                steady[ch] = ((measured.errors_uv[ch] -
                               channel_a * previous.errors_uv[ch]) /
                              (1.0 - channel_a))
            steady_responses.append(steady)
            previous = measured

        # Los patrones son sólo identificación, nunca una precarga. Volver al
        # origen antes de calcular/aplicar Newton hace que el salto se refiera
        # al estado neutro observado y no al último vértice dinámico.
        for stage in stages:
            self.move_stage(stage, origins[stage], verify=False)
        self.read_once()

        jacobian: dict[int, dict[int, float]] = {}
        n = float(len(patterns))
        for column, stage in enumerate(stages):
            jacobian[stage] = {}
            for ch in range(5):
                jacobian[stage][ch] = sum(
                    pattern[column] * (steady[ch] - base.errors_uv[ch])
                    for pattern, steady in zip(patterns, steady_responses)
                ) / (n * PROBE_CODES[stage])
            effect = max(abs(jacobian[stage][ch] * PROBE_CODES[stage])
                         for ch in range(5))
            if effect < MIN_IDENT_EFFECT_UV[stage]:
                raise RuntimeError(
                    f"IDAC{stage} sin autoridad dinámica ({effect:.0f} uV)")
        self.log("dynamic_jacobian", "    Jacobiano dinámico multiplexado",
                 stages=list(stages), dt_s=dt, a=a, jacobian=jacobian)
        # El Jacobiano usa la reconstrucción dinámica, pero el error del salto
        # es el de la base neutra. La realimentación posterior absorberá la
        # memoria que quede del último patrón durante la ventana única de 2τ.
        return base, jacobian

    def identify_fast_scalar(self, stage: int, channel: int) -> tuple[
            Observation, float, float]:
        """Pendiente ABBA de una planta rápida, restaurando siempre el origen.

        La secuencia low/high/high/low hace que una deriva aproximadamente
        lineal aparezca con signo opuesto en los dos efectos. Sólo se declara
        autoridad cuando ambos sentidos tienen el mismo signo, magnitudes
        compatibles y superan tanto un piso físico como seis veces la
        dispersión medida. ``effect`` vale cero si la evidencia no es fiable.
        """
        origin = self.codes[stage]
        probe = PROBE_CODES[stage]
        if stage == 2 and self.pgaout_gain > 1:
            # IDAC2 está ahora ANTES de PGAout. Su pendiente sobre SUMo crece
            # aproximadamente G veces. Se dimensiona la sonda usando la
            # pendiente que esta misma corrida midió en OPA_SUM a x1; nunca
            # una tabla ni una semilla. Se buscan 40 mV low->high y se exige
            # al menos ±2 códigos para distinguir cuantización de deriva.
            expected = abs(self.stage2_slope_x1_uv_per_code or
                           real_uv_to_bank_uv(10_000))
            expected *= self.pgaout_gain
            probe = max(1, min(8, int(math.ceil(real_uv_to_bank_uv(40_000) /
                                                 (2.0 * expected)))))
        fast_wait = 0.0 if self.tau_s is not None and self.tau_s <= 0.0 else 2.0
        low = clamp_code(stage, origin - probe)
        high = clamp_code(stage, origin + probe)
        if high - low < 2:
            raise RuntimeError(f"IDAC{stage} sin espacio para prueba bipolar")
        try:
            self.move_stage(stage, low, verify=False)
            self.read_once()
            self.wait(fast_wait)
            low_forward = self.observe(f"IDAC{stage} ABBA low 1 ({low})")
            self.move_stage(stage, high, verify=False)
            self.read_once()
            self.wait(fast_wait)
            high_forward = self.observe(f"IDAC{stage} ABBA high 1 ({high})")
            self.wait(fast_wait)
            high_reverse = self.observe(f"IDAC{stage} ABBA high 2 ({high})")
            self.move_stage(stage, low, verify=False)
            self.read_once()
            self.wait(fast_wait)
            low_reverse = self.observe(f"IDAC{stage} ABBA low 2 ({low})")
        finally:
            self.move_stage(stage, origin, verify=False)
            self.read_once()
            self.wait(fast_wait)
        base = self.observe(f"IDAC{stage} rápido restaurado")
        forward = float(high_forward.errors_uv[channel] -
                        low_forward.errors_uv[channel])
        reverse = float(high_reverse.errors_uv[channel] -
                        low_reverse.errors_uv[channel])
        mean_effect = 0.5 * (forward + reverse)
        slope = mean_effect / float(high - low)
        max_effect = max(abs(forward), abs(reverse))
        min_effect = min(abs(forward), abs(reverse))
        noise_uv = max(
            low_forward.spread_uv[channel], high_forward.spread_uv[channel],
            high_reverse.spread_uv[channel], low_reverse.spread_uv[channel],
            base.spread_uv[channel], 100)
        threshold_uv = max(MIN_BIPOLAR_AUTHORITY_UV[stage],
                           AUTHORITY_NOISE_MULTIPLIER * noise_uv)
        same_sign = (forward != 0.0 and reverse != 0.0 and
                     ((forward < 0.0) == (reverse < 0.0)))
        magnitude_ratio = (min_effect / max_effect if max_effect else 0.0)
        reliable = (same_sign and magnitude_ratio >= AUTHORITY_MIN_RATIO and
                    min_effect >= threshold_uv)
        reliable_effect = min_effect if reliable else 0.0
        verdict = "fiable" if reliable else "rechazada"
        self.log("fast_identification",
                 f"    IDAC{stage}->ch{channel}: {slope:+.1f}uV/código "
                 f"({verdict})",
                 stage=stage, channel=channel, low=low, high=high,
                 slope_uv_per_code=slope, effect_uv=mean_effect,
                 forward_effect_uv=forward, reverse_effect_uv=reverse,
                 reliable_effect_uv=reliable_effect, noise_uv=noise_uv,
                 threshold_uv=threshold_uv, same_sign=same_sign,
                 magnitude_ratio=magnitude_ratio, reliable=reliable)
        if (reliable and stage == 2 and channel == 2 and
                self.pgaout_gain == 1):
            self.stage2_slope_x1_uv_per_code = slope
        if reliable and stage == 2 and channel == 3:
            # Con un actuador entero, el mejor error garantizable es medio
            # LSB de salida. Se conserva margen amplio antes del riel.
            self.final_tolerance_uv[3] = min(
                GUARD_UV[3] - real_uv_to_bank_uv(100_000),
                max(FINAL_TOLERANCE_UV, int(math.ceil(0.55 * abs(slope)))))
        return base, slope, reliable_effect

    def tune_shared_settled(self, max_cycles: int = 4) -> Observation:
        """Equilibra BPo y OPA_SUMo con IDAC1 en PGAout x1.

        Esta es la única planta lenta y compartida. Se identifica con puntos
        realmente asentados durante 2*tau; luego aplica un salto Newton
        acotado y conserva un PI pequeño realimentado. No busca el plateau de
        PGAout y no usa códigos conocidos de corridas anteriores.
        """
        stage = 1
        channels = (1, 2)
        origin = self.codes[stage]
        probe = PROBE_CODES[stage]
        low = clamp_code(stage, origin - probe)
        high = clamp_code(stage, origin + probe)

        self.move_stage(stage, low, verify=True)
        self.wait(self.settle_for(stage))
        low_obs = self.observe(f"IDAC1 lento asentado {low}")
        self.move_stage(stage, high, verify=True)
        self.wait(self.settle_for(stage))
        high_obs = self.observe(f"IDAC1 lento asentado {high}")
        self.move_stage(stage, origin, verify=False)
        self.read_once()
        self.wait(self.settle_for(stage))
        current = self.observe("IDAC1 lento restaurado")

        slopes = {
            ch: (high_obs.errors_uv[ch] - low_obs.errors_uv[ch]) /
                float(high - low)
            for ch in channels
        }
        effect = max(abs(high_obs.errors_uv[ch] - low_obs.errors_uv[ch])
                     for ch in channels)
        if effect < MIN_IDENT_EFFECT_UV[stage]:
            raise RuntimeError("IDAC1 sin autoridad asentada sobre BP/SUM")
        self.log("shared_settled_identification",
                 "    IDAC1 asentado: " + ", ".join(
                     f"ch{ch} {slopes[ch]:+.1f}uV/código" for ch in channels),
                 low=low, high=high, slopes=slopes, effect_uv=effect,
                 settle_s=self.settle_for(stage))

        integral = 0.0
        for cycle in range(max_cycles + 1):
            old_score = score(current.errors_uv, channels)
            if old_score[0] <= FINAL_TOLERANCE_UV:
                return current
            target, predicted = choose_scalar(stage, self.codes[stage],
                                               current.errors_uv, slopes)
            raw = target - self.codes[stage]
            if cycle == 0:
                effort = raw                 # salto Newton grueso
                kind = "shared_settled_newton"
            else:
                integral = max(-32.0, min(32.0, integral + raw))
                effort = max(-MAX_PI_STEP[stage],
                             min(MAX_PI_STEP[stage],
                                 0.35 * raw + 0.04 * integral))
                kind = "shared_settled_pi"
            if abs(effort) < 0.5:
                effort = 1.0 if raw > 0 else -1.0
            candidate = clamp_code(stage, self.codes[stage] + effort)
            if candidate == self.codes[stage]:
                break
            before = self.codes[stage]
            self.control_moves += 1
            self.log(kind, f"    {'Newton' if cycle == 0 else 'PI'} compartido "
                     f"IDAC1 {before}->{candidate}", cycle=cycle,
                     prediction_uv=predicted, slopes=slopes)
            self.move_stage(stage, candidate, verify=True)
            self.wait(self.settle_for(stage))
            trial = self.observe("BP/SUM asentado después del control")
            if score(trial.errors_uv, channels) >= old_score:
                self.log("rollback", "    BP/SUM empeoró; revierte")
                self.move_stage(stage, before, verify=False)
                self.read_once()
                self.wait(self.settle_for(stage))
                current = self.observe("BP/SUM restaurado")
                slopes = {ch: slope * 0.5 for ch, slope in slopes.items()}
            else:
                current = trial
        if score(current.errors_uv, channels)[0] > FINAL_TOLERANCE_UV:
            raise RuntimeError("control compartido BP/SUM no convergió")
        return current

    def verify_final_stationarity(self) -> Observation:
        """Exige dos verificaciones finales separadas, no un instante feliz."""
        first = self.observe("verificación final A", samples=max(5, self.samples))
        assert self.tau_s is not None
        verify_wait = (0.0 if self.tau_s <= 0.0 else
                       max(4.0, 0.20 * self.tau_s))
        self.wait(verify_wait)
        second = self.observe("verificación final B", samples=max(5, self.samples))
        drift_uv = {ch: second.errors_uv[ch] - first.errors_uv[ch]
                    for ch in range(5)}
        drift_limits_uv = {
            ch: max(MAX_FINAL_DRIFT_UV,
                    4 * max(first.spread_uv[ch], second.spread_uv[ch]))
            for ch in range(5)
        }
        critical = (0, 3, 4)
        stable_channels = (0, 1, 2, 3, 4)
        stable = all(abs(drift_uv[ch]) <= drift_limits_uv[ch]
                     for ch in stable_channels)
        both_centered = all(
            abs(first.errors_uv[ch]) <= self.final_tolerance_uv[ch] and
            abs(second.errors_uv[ch]) <= self.final_tolerance_uv[ch]
            for ch in critical)
        self.log("final_stationarity",
                 "    estabilidad final: " + " ".join(
                      f"ch{ch} Δ={real_mv(drift_uv[ch]):+.1f}mV"
                     for ch in critical),
                 wait_s=verify_wait, drift_uv=drift_uv,
                 drift_limits_uv=drift_limits_uv, stable=stable,
                 both_centered=both_centered)
        if not stable or not both_centered:
            raise RuntimeError("verificación final fuera de estabilidad/tolerancia")
        return second

    def tune_fast_scalar(self, stage: int, channel: int,
                         max_cycles: int = 20) -> Observation:
        """Newton escalar amortiguado + PI chico para una planta rápida."""
        current, slope, effect = self.identify_fast_scalar(stage, channel)
        fast_wait = 0.0 if self.tau_s is not None and self.tau_s <= 0.0 else 2.0
        if effect < MIN_IDENT_EFFECT_UV[stage] or abs(slope) < 1.0:
            raise RuntimeError(f"IDAC{stage} sin autoridad rápida medible")
        control_tolerance = max(
            CONTROL_TOLERANCE_UV,
            int(math.ceil(0.55 * abs(slope))) if stage == 2 and channel == 3
            else CONTROL_TOLERANCE_UV)
        final_tolerance = max(
            FINAL_TOLERANCE_UV,
            int(math.ceil(0.55 * abs(slope))) if stage == 2 and channel == 3
            else FINAL_TOLERANCE_UV)
        integral = 0.0
        for cycle in range(max_cycles):
            error = current.errors_uv[channel]
            if abs(error) <= control_tolerance:
                break
            raw = -error / slope
            integral = max(-3.0 * MAX_PI_STEP[stage],
                           min(3.0 * MAX_PI_STEP[stage], integral + raw))
            effort = 0.55 * raw + 0.04 * integral
            effort = max(-MAX_PI_STEP[stage],
                         min(MAX_PI_STEP[stage], effort))
            if abs(effort) < 0.5:
                effort = 1.0 if raw > 0 else -1.0
            candidate = clamp_code(stage, self.codes[stage] + effort)
            if candidate == self.codes[stage]:
                break
            before = self.codes[stage]
            self.control_moves += 1
            self.log("fast_pi", f"    PI rápido IDAC{stage} {before}->{candidate}",
                     cycle=cycle, error_uv=error, slope_uv_per_code=slope)
            self.move_stage(stage, candidate, verify=False)
            self.read_once()
            self.wait(fast_wait)
            trial = self.observe("microajuste rápido")
            if abs(trial.errors_uv[channel]) > abs(error) * 1.25:
                self.move_stage(stage, before, verify=False)
                self.read_once()
                self.wait(fast_wait)
                current = self.observe("PI rápido revertido")
                slope *= 0.5
            else:
                current = trial
        if abs(current.errors_uv[channel]) > final_tolerance:
            raise RuntimeError(f"PI rápido IDAC{stage} no convergió")
        return current

    def tune_slow_bp(self, max_cycles: int = 3) -> Observation:
        """Centra BPo usando la pendiente obtenida al medir tau.

        La identificación de tau ya produjo un escalón asentado de IDAC1; se
        reutiliza esa pendiente medida para no repetir otros tres intervalos
        lentos. Cada corrección se valida tras 2*tau y se revierte si empeora.
        """
        if self.tau_s is None or self.bp_slope_uv_per_code is None:
            raise RuntimeError("BP sin tau/pendiente medida")
        current = self.observe("BP antes del control lento")
        for cycle in range(max_cycles):
            error = current.errors_uv[1]
            if abs(error) <= CONTROL_TOLERANCE_UV:
                return current
            raw = -error / self.bp_slope_uv_per_code
            # El primer salto usa todo el rango normal, pero move_stage lo
            # aplica en rampas de 16 códigos con guarda en cada tramo.
            limit = 255 if cycle == 0 else MAX_PI_STEP[1]
            effort = max(-limit, min(limit, raw if cycle == 0 else 0.35 * raw))
            if abs(effort) < 0.5:
                effort = 1.0 if raw > 0 else -1.0
            candidate = clamp_code(1, self.codes[1] + effort)
            if candidate == self.codes[1]:
                break
            before = self.codes[1]
            self.control_moves += 1
            self.log("bp_newton" if cycle == 0 else "bp_pi",
                     f"    BP IDAC1 {before}->{candidate}", cycle=cycle,
                     error_uv=error,
                     slope_uv_per_code=self.bp_slope_uv_per_code)
            self.move_stage(1, candidate, verify=True)
            self.wait(self.settle_for(1))
            trial = self.observe("BP asentado 2tau")
            if abs(trial.errors_uv[1]) > abs(error) * 1.20:
                self.move_stage(1, before, verify=False)
                self.wait(self.settle_for(1))
                current = self.observe("BP revertido y asentado")
                self.bp_slope_uv_per_code *= 0.5
            else:
                current = trial
        if abs(current.errors_uv[1]) > FINAL_TOLERANCE_UV:
            raise RuntimeError("control lento de BP no convergió")
        return current

    def tune_shared_plateau(self, gain: int, origin: int,
                            base: Observation,
                            probes: Sequence[tuple[int, Observation]],
                            dt: float) -> Observation:
        """Control escalar direccional para la zona saturada de PGAout.

        El Jacobiano de SUMo es cero dentro del plateau y enorme en el borde.
        Primero se descubre el sentido con las dos sondas reales; luego se
        localiza el borde con pasos gruesos. Cada candidato fino se evalúa
        desde la misma rama (x1/origen -> candidato -> ganancia objetivo) y se
        mantiene 2*tau. No hay código precargado ni inversión de una pendiente
        inválida.
        """
        stage, channel = 1, 3
        assert self.tau_s is not None and self.tau_s > 0.0
        negative = min(probes, key=lambda item: item[0])
        positive = max(probes, key=lambda item: item[0])
        # SUMo desempata primero; si ambos siguen saturados, OPA_SUMo ch2 dice
        # qué sentido acerca la entrada no saturada a cero.
        chosen = min((negative, positive),
                     key=lambda item: (abs(item[1].errors_uv[channel]),
                                       abs(item[1].errors_uv[2]),
                                       abs(item[1].errors_uv[1])))
        direction = -1 if chosen[0] < origin else 1
        self.log("plateau_detected",
                 f"    plateau SUMo: búsqueda direccional {'-' if direction < 0 else '+'}",
                 probe_codes=[item[0] for item in probes],
                 probe_errors_uv=[item[1].errors_uv[channel] for item in probes],
                 direction=direction)

        self.move_stage(stage, origin, verify=False)
        self.read_once()
        previous_code, previous_obs = origin, base
        edge_code: int | None = None
        edge_trigger = max(FINAL_TOLERANCE_UV,
                           int(0.15 * abs(base.errors_uv[channel])))
        for radius in range(12, MAX_MOVE_CODES[stage] + 1, 12):
            candidate = clamp_code(stage, origin + direction * radius)
            self.move_stage(stage, candidate, verify=True)
            self.wait(dt)
            obs = self.observe(f"borde grueso IDAC1={candidate}")
            changed = abs(obs.errors_uv[channel] - base.errors_uv[channel])
            crossed = ((obs.errors_uv[channel] < 0) !=
                       (base.errors_uv[channel] < 0))
            if abs(obs.errors_uv[channel]) <= FINAL_TOLERANCE_UV:
                edge_code = candidate
                previous_obs = obs
                break
            if crossed or changed >= edge_trigger:
                edge_code = previous_code
                break
            previous_code, previous_obs = candidate, obs
        if edge_code is None:
            raise RuntimeError("no se encontró el borde del plateau SUMo")

        def settled_from_same_branch(candidate: int) -> Observation:
            checked(lambda: self.lab.set_gain("pgaout", GAIN_TO_CODE[1]),
                    "PGAout x1 para reiniciar rama")
            self.move_stage(stage, origin, verify=False)
            self.read_once()
            self.wait(0.50 * self.tau_s)
            self.move_stage(stage, candidate, verify=True)
            checked(lambda: self.lab.set_gain("pgaout", GAIN_TO_CODE[gain]),
                    f"PGAout x{gain} para evaluar candidato")
            self.wait(2.0 * self.tau_s)
            return self.observe(f"candidato asentado 2tau IDAC1={candidate}")

        candidate = edge_code
        current = settled_from_same_branch(candidate)
        for cycle in range(12):
            error = current.errors_uv[channel]
            if abs(error) <= FINAL_TOLERANCE_UV:
                self.log("plateau_closed_loop",
                         f"    borde regulado IDAC1={candidate}: "
                         f"{real_mv(error):+.1f}mV",
                         candidate=candidate, error_uv=error, cycles=cycle,
                         settle_s=2.0 * self.tau_s)
                return current
            # Mover en el sentido descubierto reduce un error positivo; para
            # un error negativo se vuelve un código hacia el origen. Cada
            # ensayo reinicia la rama, así la histéresis no decide el resultado.
            correction_direction = direction if error > 0 else -direction
            next_candidate = clamp_code(stage,
                                        candidate + correction_direction)
            if next_candidate == candidate:
                break
            self.control_moves += 1
            self.log("plateau_pi_step",
                     f"    lazo lento IDAC1 {candidate}->{next_candidate}",
                     cycle=cycle, error_uv=error,
                     direction=correction_direction)
            candidate = next_candidate
            current = settled_from_same_branch(candidate)
        raise RuntimeError("búsqueda escalar del borde no convergió")

    def tune_shared_bp_for_sum(self, gain: int) -> Observation:
        """Saca SUMo del plateau con IDAC1 y cierra durante una ventana 2τ.

        IDAC1 mueve la referencia común de OPAbp/OPAsum. BPo y OPA_SUMo son
        guardas; el objetivo primario es SUMo después de PGAout. La búsqueda
        bipolar obtiene un bracket sin asumir signo ni código previo, aplica
        un salto secante/Newton y deja sólo microcorrecciones PI durante 2τ.
        """
        stage, channel = 1, 3
        assert self.tau_s is not None
        if self.tau_s <= 0.0:  # banco simulado
            dt = 0.0
        else:
            dt = max(2.0, 0.25 * self.tau_s)
        origin = self.codes[stage]
        base = self.observe("base para bracket BP->SUM")
        if abs(base.errors_uv[channel]) <= FINAL_TOLERANCE_UV:
            self.log("shared_already_centered",
                     "    SUMo ya está dentro de tolerancia con IDAC1 actual",
                     error_uv=base.errors_uv[channel])
            return base
        points: list[tuple[int, Observation]] = [(origin, base)]
        bracket: tuple[tuple[int, Observation], tuple[int, Observation]] | None = None

        def opposite(a_obs: Observation, b_obs: Observation) -> bool:
            a_err, b_err = a_obs.errors_uv[channel], b_obs.errors_uv[channel]
            return a_err == 0 or b_err == 0 or (a_err < 0) != (b_err < 0)

        # Se expande simétricamente y de a poco. Cada medida realimenta las
        # guardas; nunca se aplica una tabla de semillas ni una dirección fija.
        for radius in (PROBE_CODES[stage], 2 * PROBE_CODES[stage],
                       4 * PROBE_CODES[stage], 6 * PROBE_CODES[stage],
                       10 * PROBE_CODES[stage], 15 * PROBE_CODES[stage]):
            for sign in (-1, 1):
                candidate = clamp_code(stage, origin + sign * radius)
                if any(code == candidate for code, _ in points):
                    continue
                try:
                    # El barrido amplio sólo avanza en rampas verificadas. Si
                    # una dirección toca una guarda, vuelve al origen seguro y
                    # prueba la opuesta; nunca deja aplicado el punto rechazado.
                    self.move_stage(stage, candidate, verify=True)
                except RuntimeError as exc:
                    self.move_stage(stage, origin, verify=False)
                    self.read_once()
                    self.log("probe_guard_rejected",
                             f"    bracket IDAC1={candidate} rechazado: {exc}",
                             candidate=candidate, error=str(exc))
                    continue
                self.wait(dt)
                obs = self.observe(f"bracket IDAC1={candidate}")
                crossing = [old for old in points if opposite(old[1], obs)]
                if crossing:
                    # La planta física tiene un plateau. La secante debe usar
                    # el tramo local más corto que encierra cero, no promediar
                    # desde el origen a través de toda la zona saturada.
                    old = min(crossing,
                              key=lambda point: (
                                  abs(point[0] - candidate),
                                  max(abs(point[1].errors_uv[channel]),
                                      abs(obs.errors_uv[channel]))))
                    bracket = (old, (candidate, obs))
                points.append((candidate, obs))
                if bracket is not None:
                    break
            if bracket is not None:
                break
            if radius == PROBE_CODES[stage]:
                first_probes = [(code, obs) for code, obs in points
                                if code != origin]
                output_effect = (max(obs.errors_uv[channel]
                                     for _, obs in first_probes) -
                                 min(obs.errors_uv[channel]
                                     for _, obs in first_probes))
                if output_effect < MIN_IDENT_EFFECT_UV[stage]:
                    return self.tune_shared_plateau(
                        gain, origin, base, first_probes, dt)
        # Siempre se vuelve al origen antes del salto calculado. Si hubo cruce
        # de signo, la secante queda interpolada dentro del bracket. Si no lo
        # hubo, sólo se permite extrapolar cuando dos puntos medidos exhiben
        # autoridad suficiente; el trust region limita el salto y el PI lo
        # valida inmediatamente en lazo cerrado.
        self.move_stage(stage, origin, verify=False)
        self.read_once()
        constrained = bracket is not None
        if bracket is None:
            pairs = list(itertools.combinations(points, 2))
            bracket = max(
                pairs,
                key=lambda pair: abs(pair[1][1].errors_uv[channel] -
                                     pair[0][1].errors_uv[channel]),
            )
            observed_effect = abs(bracket[1][1].errors_uv[channel] -
                                  bracket[0][1].errors_uv[channel])
            if observed_effect < MIN_IDENT_EFFECT_UV[stage]:
                raise RuntimeError("IDAC1 no demostró autoridad sobre SUMo")

        (code_a, obs_a), (code_b, obs_b) = bracket
        err_a = float(obs_a.errors_uv[channel])
        err_b = float(obs_b.errors_uv[channel])
        slope = (err_b - err_a) / float(code_b - code_a)
        if abs(slope) < 1.0:
            raise RuntimeError("bracket BP->SUM sin pendiente útil")
        newton = code_a - err_a / slope
        if constrained:
            lo, hi = sorted((code_a, code_b))
        else:
            lo = max(CODE_LIMITS[stage][0], origin - MAX_MOVE_CODES[stage])
            hi = min(CODE_LIMITS[stage][1], origin + MAX_MOVE_CODES[stage])
        newton_code = clamp_code(stage, max(lo, min(hi, newton)))
        self.log("scalar_newton",
                 f"    Newton BP->SUM [{code_a},{code_b}]->{newton_code}",
                 bracket_codes=[code_a, code_b], bracket_errors_uv=[err_a, err_b],
                 slope_uv_per_code=slope, constrained_by_crossing=constrained)
        self.move_stage(stage, newton_code, verify=False)
        self.read_once()

        window_s = 0.0 if self.tau_s <= 0.0 else 2.0 * self.tau_s
        control_s = 0.0 if self.tau_s <= 0.0 else 1.6 * self.tau_s
        sample_dt = 0.0 if self.tau_s <= 0.0 else max(2.0, 0.20 * self.tau_s)
        started = time.monotonic()
        integral = 0.0
        # La lectura inmediatamente posterior al salto contiene el transitorio
        # que justamente caracteriza tau. Se deja consumir 0,75*tau dentro de
        # la única ventana total de 2*tau antes de permitir microcorrecciones.
        if self.tau_s > 0.0:
            self.wait(0.75 * self.tau_s)
        current = self.observe("inicio PI BP->SUM")
        cycles = 0
        while cycles < 12 and (self.tau_s <= 0.0 or
                               time.monotonic() - started < control_s):
            self.wait(sample_dt)
            current = self.observe("PI BP->SUM asentando")
            error = current.errors_uv[channel]
            if abs(error) > CONTROL_TOLERANCE_UV:
                raw = -error / slope
                integral = max(-12.0, min(12.0, integral + raw))
                effort = max(-4.0, min(4.0, 0.20 * raw + 0.03 * integral))
                if abs(effort) >= 0.5:
                    candidate = clamp_code(stage, self.codes[stage] + effort)
                    if candidate != self.codes[stage]:
                        before = self.codes[stage]
                        self.control_moves += 1
                        self.log("shared_pi_micro",
                                 f"    micro PI BP->SUM {before}->{candidate}",
                                 cycle=cycles, error_uv=error,
                                 slope_uv_per_code=slope, integral=integral)
                        self.move_stage(stage, candidate, verify=False)
                        self.read_once()
            cycles += 1

        if self.tau_s > 0.0:
            while time.monotonic() - started < window_s:
                self.wait(min(sample_dt,
                              window_s - (time.monotonic() - started)))
                current = self.observe("verificación BP->SUM durante 2tau")
        self.log("closed_loop_window",
                 f"    BP->SUM 2tau terminó: "
                 f"{real_mv(current.errors_uv[channel]):+.1f}mV",
                 stages=[stage], cycles=cycles, window_s=window_s,
                 final_error_uv=current.errors_uv[channel])
        if abs(current.errors_uv[channel]) > FINAL_TOLERANCE_UV:
            raise RuntimeError("PI BP->SUM no convergió en 2tau")
        return current

    def tune_joint_dynamic(self, stages: Sequence[int], channels: Sequence[int],
                           max_cycles: int = 12) -> Observation:
        """Newton grueso y PI vectorial fino dentro de una ventana total 2*tau."""
        current_obs, jacobian = self.identify_dynamic_multiplex(stages)
        origin = list(self.codes)
        coarse = choose_joint(self.codes, current_obs.errors_uv, jacobian,
                              stages, channels)
        self.log("vector_newton", f"    Newton vectorial {origin}->{coarse}")
        for stage in stages:
            self.move_stage(stage, coarse[stage], verify=False)
        self.read_once()

        assert self.tau_s is not None
        instantaneous = self.tau_s <= 0.0
        includes_bp = TAU_EST_STAGE in stages
        window_s = (0.0 if instantaneous else
                    (2.0 * self.tau_s if includes_bp else 24.0))
        control_s = (0.0 if instantaneous else
                     (1.6 * self.tau_s if includes_bp else window_s))
        dt = (0.0 if instantaneous else
              (max(2.0, 0.15 * self.tau_s) if includes_bp else 2.0))
        started = time.monotonic()
        integral = {stage: 0.0 for stage in stages}
        current_obs = self.observe("inicio de ventana PI")
        cycles = 0
        while cycles < max_cycles and (instantaneous or
              time.monotonic() - started < control_s):
            if score(current_obs.errors_uv, channels)[0] <= CONTROL_TOLERANCE_UV:
                break
            correction = joint_delta(jacobian, current_obs.errors_uv,
                                     stages, channels)
            candidate = list(self.codes)
            for stage, delta in zip(stages, correction):
                integral[stage] = max(-3 * MAX_PI_STEP[stage],
                                      min(3 * MAX_PI_STEP[stage],
                                          integral[stage] + delta))
                effort = 0.20 * delta + 0.03 * integral[stage]
                effort = max(-MAX_PI_STEP[stage] / 2.0,
                             min(MAX_PI_STEP[stage] / 2.0, effort))
                if abs(effort) >= 0.5:
                    candidate[stage] = clamp_code(stage,
                                                  self.codes[stage] + effort)
            if all(candidate[stage] == self.codes[stage] for stage in stages):
                break
            before = list(self.codes)
            self.control_moves += 1
            self.log("vector_pi_micro", f"    micro PI {before}->{candidate}",
                     cycle=cycles, correction=correction, integral=integral)
            for stage in stages:
                self.move_stage(stage, candidate[stage], verify=False)
            self.read_once()
            self.wait(dt)
            current_obs = self.observe("microajuste PI asentando")
            cycles += 1

        # Sólo BP exige completar 2*tau desde el Newton. Para PGAout/LP se
        # termina apenas el lazo rápido cumple; siempre con medición posterior.
        if not instantaneous and includes_bp:
            remaining = window_s - (time.monotonic() - started)
            while remaining > 0:
                self.wait(min(max(2.0, 0.20 * self.tau_s), remaining))
                current_obs = self.observe("verificación durante 2tau")
                remaining = window_s - (time.monotonic() - started)
        final_score = score(current_obs.errors_uv, channels)
        window_name = "2tau BP" if includes_bp else "lazo rápido"
        self.log("closed_loop_window", f"    ventana {window_name} terminó: "
                 f"peor={real_mv(final_score[0]):.1f}mV", stages=list(stages),
                 cycles=cycles, window_s=window_s, final_score_uv=final_score)
        if final_score[0] > FINAL_TOLERANCE_UV:
            raise RuntimeError(
                f"PI vectorial {tuple(stages)} no convergió en {window_name}")
        return current_obs

    def calibrate_gain(self, gain: int, strategy: str) -> dict:
        event_start = len(self.events)
        read_start = self.read_count
        move_start = self.control_moves
        started = time.monotonic()
        self.log("gain_start", f"\nAutocalibrando x{gain} desde cero ({strategy})")
        self.safe_zero()
        self.measure_tau_bp()

        if strategy not in ("sequential", "coupled"):
            raise ValueError(f"estrategia desconocida: {strategy}")

        # PGAgain y BPo son guardas, no objetivos de cero. En placa IDAC0=0
        # deja PGAgain en -48 mV (holgadamente dentro de ±500 mV); centrarlo
        # con IDAC0=18 llevó OPA_SUM al riel incluso después de 2*tau. Por eso
        # no se toca una etapa aguas arriba que ya es segura.
        pga = self.observe("PGA estable (guarda, no centrado)")
        if abs(pga.errors_uv[0]) > GUARD_UV[0]:
            raise RuntimeError("PGAgain fuera del margen de ±500 mV")
        self.log("guard_only", "    IDAC0 queda en cero: PGA dentro de guarda",
                 channel=0, error_uv=pga.errors_uv[0])
        bp = self.observe("BP asentado tras 2tau")
        if abs(bp.errors_uv[1]) > GUARD_UV[1]:
            raise RuntimeError("BPo estable pero fuera del margen de ±500 mV")
        self.tune_fast_scalar(2, 2)
        upstream = self.observe("OPA_SUM centrado antes de ganar")
        if abs(upstream.errors_uv[2]) > FINAL_TOLERANCE_UV:
            raise RuntimeError("OPA_SUMo no quedó centrado antes de PGAout")

        checked(lambda: self.lab.set_gain("pgaout", GAIN_TO_CODE[gain]),
                f"PGAout x{gain}")
        self.pgaout_gain = gain
        gain_entry = self.observe(f"PGAout x{gain}: entrada segura")
        if abs(gain_entry.errors_uv[3]) > GUARD_UV[3]:
            raise RuntimeError("SUMo fuera de guarda al subir PGAout")

        if gain > 1:
            self.tune_fast_scalar(2, 3)
        if strategy == "coupled":
            self.log("strategy_retired",
                     "    vectorial retirado: se usa cierre secuencial seguro")
        self.tune_fast_scalar(3, 4)

        final = self.verify_final_stationarity()
        passed = (abs(final.errors_uv[0]) <= self.final_tolerance_uv[0] and
                  abs(final.errors_uv[3]) <= self.final_tolerance_uv[3] and
                  abs(final.errors_uv[4]) <= self.final_tolerance_uv[4] and
                  abs(final.errors_uv[1]) <= GUARD_UV[1] and
                  abs(final.errors_uv[2]) <= GUARD_UV[2])
        result = {
            "gain": gain, "strategy": strategy, "pass": passed,
            "codes": list(self.codes),
            "errors_mv": {str(ch): real_mv(final.errors_uv[ch])
                          for ch in range(5)},
            "spread_mv": {str(ch): real_mv(final.spread_uv[ch])
                          for ch in range(5)},
            "tolerance_mv": {str(ch): real_mv(value) for ch, value in
                             self.final_tolerance_uv.items()},
            "read_windows": self.read_count - read_start,
            "control_moves": self.control_moves - move_start,
            "elapsed_s": round(time.monotonic() - started, 3),
            "events": self.events[event_start:],
        }
        if not passed:
            raise RuntimeError("verificación: PGA/SUM/LP fuera de ±20 mV "
                               "o guarda BP/OPA_SUM excedida")
        self.log("gain_pass", f"PASS x{gain}: IDAC {self.codes}", result=result)
        return result


class SimLab:
    """Familia de placas lineales aleatorias para comparar controladores."""
    def __init__(self, seed: int) -> None:
        rng = random.Random(seed)
        self.codes = [0, 0, 0, 0]
        self.gain = 1
        self.truth = [rng.randint(-30, 30), rng.randint(-150, -50),
                      rng.randint(-20, 20), rng.randint(-120, 120)]
        self.pgaout_ref_mismatch = rng.randint(-1_000, 1_000)
        self.rng = rng

    def set_idac(self, stage: int, code: int) -> bool:
        self.codes[stage] = code
        return True

    def set_gain(self, which: str, code: int) -> bool:
        if which == "pgaout":
            self.gain = GAIN_CODES[code]
        return True

    def set_adc_config(self, cfg: int) -> bool:
        return cfg == 1

    def commit_calibration(self) -> bool:
        return True

    def measure_dc(self, ch: int, settle_sel: int = 3):
        d = [self.codes[i] - self.truth[i] for i in range(4)]
        e0 = 2_500 * d[0]
        e1 = 400 * d[0] + 1_200 * d[1]
        # 10 kΩ en la referencia de OPA_SUM: la placa midió del orden de
        # 8-10 mV/código en x1, mucho más grueso que la versión de 1,5 kΩ.
        e2 = -800 * d[0] - 250 * d[1] + 10_000 * d[2]
        e3 = self.gain * e2 + (1 - self.gain) * self.pgaout_ref_mismatch
        e4 = 0.25 * e3 + 1_300 * d[3]
        error = (e0, e1, e2, e3, e4)[ch] + self.rng.randint(-250, 250)
        return SimpleNamespace(mean_uv=int(TARGET_UV + error), pp_uv=500, ok=True)


def self_test() -> int:
    summary = {strategy: {"pass": 0, "reads": 0, "moves": 0, "fail": []}
               for strategy in ("sequential", "coupled")}
    for strategy, seed, gain in itertools.product(
            ("sequential", "coupled"), range(30), (1, 8, 24)):
        lab = SimLab(seed)
        cal = AdaptiveCalibrator(lab, samples=3, period_s=0, verbose=False,
                                 tau_override_s=0.0)
        # Sólo el banco matemático saltea la automedición física de tau.
        cal.bp_slope_uv_per_code = 1_200.0
        cal.bp_zero_error_uv = 0.0
        try:
            result = cal.calibrate_gain(gain, strategy)
            summary[strategy]["pass"] += int(result["pass"])
            summary[strategy]["reads"] += result["read_windows"]
            summary[strategy]["moves"] += result["control_moves"]
        except Exception as exc:
            summary[strategy]["fail"].append(
                {"seed": seed, "gain": gain, "error": str(exc)})
    for strategy in summary:
        item = summary[strategy]
        item["mean_reads"] = round(item["reads"] / max(item["pass"], 1), 2)
        item["mean_moves"] = round(item["moves"] / max(item["pass"], 1), 2)
    print(json.dumps(summary, indent=2))
    return 0 if all(item["pass"] == 90 for item in summary.values()) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--gains", default="24")
    ap.add_argument("--strategy", choices=("sequential", "coupled"),
                    default="sequential")
    ap.add_argument("--compare", action="store_true",
                    help="corre ambas estrategias desde cero y no graba EEPROM")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--period", type=float, default=1.0)
    ap.add_argument("--save-eeprom", action="store_true",
                    help="sólo después de PASS, acepta y guarda los códigos en PSoC")
    ap.add_argument("--experimental-high-gain", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    gains = [int(part.strip()) for part in args.gains.split(",")]
    if any(gain not in GAIN_TO_CODE for gain in gains):
        ap.error("ganancia no soportada")
    if (not args.experimental_high_gain and
            any(gain > PRODUCTION_MAX_GAIN for gain in gains)):
        ap.error("x32/x48/x50 requieren --experimental-high-gain")
    if args.samples < 3:
        ap.error("se requieren al menos tres muestras")
    if args.compare and args.save_eeprom:
        ap.error("--compare nunca escribe EEPROM; ejecute luego la ganadora")
    if args.save_eeprom and len(gains) != 1:
        ap.error("EEPROM guarda el punto operativo final: indique una ganancia")

    output = args.output or (
        Path(__file__).resolve().parents[3] / "lab" / "calibracion_nueva" /
        f"autocal_lazo_cerrado_{datetime.now():%Y%m%d_%H%M%S}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    strategies = ("sequential", "coupled") if args.compare else (args.strategy,)
    payload = {
        "algorithm": "closed-loop system identification; no seeds",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "limits": {
            "codes": CODE_LIMITS,
            "internal_units": "bank_uV",
            "bank_to_real_factor": FACTOR,
            "target_bank_uv": TARGET_UV,
            "valid_bank_uv": [VALID_MIN_UV, VALID_MAX_UV],
            "guards_bank_uv": GUARD_UV,
            "guards_real_mv": [real_mv(value) for value in GUARD_UV],
            "final_tolerance_bank_uv": FINAL_TOLERANCE_UV,
            "final_tolerance_real_mv": real_mv(FINAL_TOLERANCE_UV),
        },
        "results": [],
    }

    console = con.Console(args.port)
    print(f"Abriendo {args.port}...", flush=True)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    exit_code = 0
    last_calibrator = None
    try:
        checked(lambda: lab.set_adc_config(1), "ADC ±2,5 V")
        for strategy in strategies:
            for gain in gains:
                calibrator = AdaptiveCalibrator(
                    lab, samples=args.samples, period_s=args.period)
                last_calibrator = calibrator
                try:
                    result = calibrator.calibrate_gain(gain, strategy)
                    if args.save_eeprom:
                        checked(lab.commit_calibration,
                                "aceptar y guardar calibración en EEPROM")
                        result["eeprom_saved"] = True
                    payload["results"].append(result)
                except Exception as exc:
                    exit_code = 2
                    calibrator.log("gain_fail", f"FAIL x{gain}: {exc}")
                    payload["results"].append({
                        "gain": gain, "strategy": strategy, "pass": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "codes": list(calibrator.codes),
                        "events": calibrator.events})
                    calibrator.restore_neutral()
                output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                write_events_csv(payload, Path(str(output) + ".csv"))
    finally:
        if args.restore and last_calibrator is not None:
            try:
                last_calibrator.restore_neutral()
            except Exception:
                pass
        payload["finished_at"] = datetime.now().isoformat(timespec="seconds")
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        write_events_csv(payload, Path(str(output) + ".csv"))
        Path(str(output) + ".log").write_text(console.transcript.text(),
                                              encoding="utf-8")
        console.close()
    passed = sum(1 for result in payload["results"] if result.get("pass"))
    print(f"{passed}/{len(payload['results'])} PASS. Evidencia: {output}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
