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
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Sequence

from testbench.core import console as con
from testbench.core.lab import GAIN_CODES, Lab, TAP_NAMES
from testbench.core.session import Session


TARGET_UV = 1_000_000
PGA_X50_CODE = GAIN_CODES.index(50)
GAIN_TO_CODE = {gain: code for code, gain in enumerate(GAIN_CODES)}
PRODUCTION_MAX_GAIN = 24

# Límites de software, deliberadamente menores que los eléctricos. IDAC2:
# 48 uA * 1,5 kohm = 72 mV. IDAC3: 20 uA * 10 kohm = 200 mV.
CODE_LIMITS = ((-200, 200), (-200, 200), (-48, 48), (-160, 160))
PROBE_CODES = (8, 12, 16, 16)
MAX_MOVE_CODES = (64, 80, 24, 64)
MAX_RAMP_CODES = 16
MAX_PI_STEP = (8, 16, 8, 20)
OBJECTIVE_CHANNELS = ((0,), (1, 2), (3,), (4,))

FINAL_TOLERANCE_UV = 20_000
CONTROL_TOLERANCE_UV = 8_000
PRE_GAIN_SUM_LIMIT_UV = 80_000
GUARD_UV = (500_000, 500_000, 1_000_000, 1_000_000, 1_000_000)
MIN_IDENT_EFFECT_UV = (3_000, 3_000, 2_000, 4_000)
TAU_EST_STAGE = 1
TAU_EST_CHANNEL = 1
TAU_EST_DELTA = 60
TAU_EST_SAMPLE_S = 5.0
TAU_EST_DURATION_S = 90.0
TAU_INITIAL_SETTLE_S = 60.0
SETTLE_TAU_MULTIPLIER = 2.0


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
    if abs(amplitude) < max(8.0 * max(noise_uv, 1.0), 2_000.0):
        metrics["reason"] = "excursion insuficiente"
        return None, metrics
    if r2 < 0.70 or residual_rms > max(4.0 * noise_uv, 2_000.0):
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

    def log(self, kind: str, message: str, **data) -> None:
        event = {"at": datetime.now().isoformat(timespec="seconds"),
                 "kind": kind, "message": message, **data}
        self.events.append(event)
        if self.verbose:
            print(message, flush=True)

    def wait(self, seconds: float) -> None:
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end:
            time.sleep(min(2.0, end - time.monotonic()))

    def read_once(self) -> dict[int, dict[str, int]]:
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
            if abs(error) > GUARD_UV[ch]:
                raise RuntimeError(
                    f"guarda ch{ch} {TAP_NAMES[ch]}: {error / 1000:+.1f} mV")
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
            f"{TAP_NAMES[ch]}={errors[ch] / 1000:+.1f}mV" for ch in range(5)),
            label=label, observation=obs.as_dict(), codes=list(self.codes))
        return obs

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
        checked(lambda: self.lab.set_gain("pgaout", GAIN_TO_CODE[1]),
                "PGAout x1")
        for stage in (2, 3, 0, 1):
            self.set_code_direct(stage, 0)
        self.wait(TAU_INITIAL_SETTLE_S if self.tau_s is None
                  else self.settle_for(0))
        self.observe("estado neutro")

    def measure_tau_bp(self) -> float:
        """Mide tau del polo dominante con IDAC1 -> BPo (AMux ch1).

        OPA_SUMo (ch2) se sigue leyendo como guarda, pero no participa del
        estimador: tau pertenece al pasabanda y se observa en su propio tap.
        """
        if self.tau_s is not None:
            return self.tau_s
        for direction in (1, -1):
            base = self.observe("base para estimar tau", samples=3)
            self.set_code_direct(TAU_EST_STAGE, direction * TAU_EST_DELTA)
            started = time.monotonic()
            points: list[tuple[float, float]] = []
            next_sample = 0.0
            while next_sample <= TAU_EST_DURATION_S + 1e-6:
                self.wait(next_sample - (time.monotonic() - started))
                point = self.lab.measure_dc(TAU_EST_CHANNEL, 3)
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
                # Restaurar IDAC1 genera otro transitorio de BP. No se paga una
                # espera aparte: el controlador realimentado lo absorbe y su
                # única ventana lenta completa será de 2*tau.
                self.observe("tau aceptado; BP restaurado e iniciando control")
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
            patterns = ((1, 1), (1, -1))
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
            patterns = ((1, 1), (1, -1))
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
        # previous es todavía el estado dinámico medido. Para el Newton se usa
        # el régimen reconstruido del patrón actualmente aplicado; la próxima
        # lectura realimentada decide si aceptar, recortar o corregir el salto.
        reconstructed = Observation(
            {ch: int(round(steady_responses[-1][ch])) for ch in range(5)},
            previous.pp_uv, previous.spread_uv, previous.samples)
        return reconstructed, jacobian

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
                 f"peor={final_score[0] / 1000:.1f}mV", stages=list(stages),
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

        if strategy == "sequential":
            self.tune_stage(0)
            self.tune_stage(1)
        elif strategy == "coupled":
            # El primer ensayo físico demostró que identificar los cuatro DAC
            # juntos a PGAout x1 mezcla la dinámica lenta de BP con PGAout/LP:
            # el modelo deja de valer al cambiar la ganancia. Se conserva el
            # control vectorial, separado en los dos bloques físicos reales.
            self.tune_joint_dynamic((0, 1), (0, 1, 2))
        else:
            raise ValueError(f"estrategia desconocida: {strategy}")

        upstream = self.observe("antes de subir PGAout")
        if abs(upstream.errors_uv[2]) > PRE_GAIN_SUM_LIMIT_UV:
            raise RuntimeError("OPA_SUMo no quedó seguro para subir PGAout")
        checked(lambda: self.lab.set_gain("pgaout", GAIN_TO_CODE[gain]),
                f"PGAout x{gain}")
        # No hay otra espera de 2*tau: el cambio se entrega directamente al
        # lazo cerrado de PGAout/LP, que mide antes de aceptar cada corrección.
        self.observe(f"PGAout x{gain}: entrada al lazo de salida")

        if strategy == "sequential":
            self.tune_stage(2)
            self.tune_stage(3)
        else:
            self.tune_joint_dynamic((2, 3), (3, 4))

        final = self.observe("verificación final", samples=max(5, self.samples))
        passed = all(abs(final.errors_uv[ch]) <= FINAL_TOLERANCE_UV
                     for ch in range(5))
        result = {
            "gain": gain, "strategy": strategy, "pass": passed,
            "codes": list(self.codes),
            "errors_mv": {str(ch): final.errors_uv[ch] / 1000.0
                          for ch in range(5)},
            "spread_mv": {str(ch): final.spread_uv[ch] / 1000.0
                          for ch in range(5)},
            "read_windows": self.read_count - read_start,
            "control_moves": self.control_moves - move_start,
            "elapsed_s": round(time.monotonic() - started, 3),
            "events": self.events[event_start:],
        }
        if not passed:
            raise RuntimeError(f"verificación x{gain} fuera de ±20 mV")
        self.log("gain_pass", f"PASS x{gain}: IDAC {self.codes}", result=result)
        return result


class SimLab:
    """Familia de placas lineales aleatorias para comparar controladores."""
    def __init__(self, seed: int) -> None:
        rng = random.Random(seed)
        self.codes = [0, 0, 0, 0]
        self.gain = 1
        self.truth = [rng.randint(-30, 30), rng.randint(-150, -50),
                      rng.randint(-35, 35), rng.randint(-120, 120)]
        self.shared_residual = rng.randint(-2_500, 2_500)
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
        e1 = 400 * d[0] + 1_200 * d[1] + self.shared_residual
        e2 = -800 * d[0] - 2_800 * d[1] - self.shared_residual
        e3 = 0.35 * e2 - (180 + 9 * self.gain) * d[2]
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
        "limits": {"codes": CODE_LIMITS, "guards_uv": GUARD_UV,
                   "final_tolerance_uv": FINAL_TOLERANCE_UV},
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
                    calibrator.safe_zero()
                output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                write_events_csv(payload, Path(str(output) + ".csv"))
    finally:
        if args.restore and last_calibrator is not None:
            try:
                last_calibrator.safe_zero()
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
