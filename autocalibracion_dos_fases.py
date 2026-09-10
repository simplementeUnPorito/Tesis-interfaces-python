"""Autocalibración GEO dividida en arranque lento y cambio rápido de ganancia.

La rutina de producción aplica y verifica el mínimo histórico de PGAgain/BPo
una sola vez. Cada cambio de PGAout ejecuta sólo fase 2 y revalida el punto
histórico de IDAC2/IDAC3, con LPo como objetivo funcional prioritario. Si la
memoria lenta invalida el punto, descarga fase 2 en x1 antes de un último
intento; nunca usa una semilla sin verificarla.

Todos los valores del ADC están en unidades del banco. La conversión a magnitud
física se centraliza en ``escala_banco.py``. Ninguna lectura de control conecta
el capacitor auxiliar del AMux: el firmware cargado implementa ``with_cap=0``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from escala_banco import (BANCO_MAX_VALIDO_MV, BANCO_MIN_VALIDO_MV,
                          BANCO_VREF_MV, FACTOR)
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab, TAP_NAMES
from testbench.core.session import Session

TARGET_UV = int(round(BANCO_VREF_MV * 1000.0))
VALID_MIN_UV = int(round(BANCO_MIN_VALIDO_MV * 1000.0))
VALID_MAX_UV = int(round(BANCO_MAX_VALIDO_MV * 1000.0))
UPSTREAM_GUARD_UV = int(round(500_000.0 / FACTOR))
PGA_X50_CODE = GAIN_CODES.index(50)
PGAOUT_X1_CODE = GAIN_CODES.index(1)
PGAOUT_X24_CODE = GAIN_CODES.index(24)
ADC_ABSOLUTE_UV = (700_000, 1_150_000)
HISTORY_PATH = (Path(__file__).resolve().parents[3] / "lab" /
                "calibracion_nueva" / "autocal_history.json")


def real_mv(bank_delta_uv: float) -> float:
    return bank_delta_uv * FACTOR / 1000.0


def valid_bank(value_uv: float) -> bool:
    return VALID_MIN_UV <= value_uv <= VALID_MAX_UV


def load_history(path: Path = HISTORY_PATH) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {
        "version": 1,
        "phase1": {"codes": [19, 141],
                   "slopes_bank_uv_per_code": [3000.0, 185.7],
                   "source": "median of two hardware PASS runs 2026-09-09"},
        "gains": {
            "1": {"codes": [19, 146, 254, 76],
                  "source": "hardware PASS 2026-09-09"}
        },
    }


def save_history(history: dict, path: Path = HISTORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    history["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(history, indent=2), encoding="utf-8")


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


def fit_first_order(points: list[tuple[float, float]]) -> tuple[float | None, dict]:
    """Ajusta y_inf + A exp(-t/tau); rechaza respuestas débiles/no exponenciales."""
    if len(points) < 8:
        return None, {"reason": "pocas muestras"}
    t0 = points[0][0]
    ts = [t - t0 for t, _ in points]
    ys = [y for _, y in points]
    noise = statistics.pstdev(ys[-min(5, len(ys)):]) if len(ys) > 1 else 0.0
    best = None
    for tau_tenths in range(20, 1201):
        tau = tau_tenths / 10.0
        xs = [math.exp(-t / tau) for t in ts]
        mx = statistics.fmean(xs)
        my = statistics.fmean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        if den <= 0.0:
            continue
        amplitude = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        final = my - amplitude * mx
        residuals = [y - (final + amplitude * x) for x, y in zip(xs, ys)]
        sse = sum(r * r for r in residuals)
        if best is None or sse < best[0]:
            best = (sse, tau, amplitude, final, residuals)
    if best is None:
        return None, {"reason": "ajuste imposible"}
    sse, tau, amplitude, final, residuals = best
    total = sum((y - statistics.fmean(ys)) ** 2 for y in ys)
    r2 = 1.0 - sse / total if total > 0.0 else 0.0
    residual_rms = math.sqrt(sse / len(ys))
    metrics = {"tau_s": tau, "amplitude_bank_uv": amplitude,
               "amplitude_real_mv": real_mv(amplitude), "final_bank_uv": final,
               "r2": r2, "residual_rms_bank_uv": residual_rms,
               "noise_bank_uv": noise, "samples": len(points)}
    min_effect = max(8.0 * max(noise, 1.0), 2_000.0 / FACTOR)
    if abs(amplitude) < min_effect:
        metrics["reason"] = "excursion insuficiente"
        return None, metrics
    if r2 < 0.70 or residual_rms > max(4.0 * noise, 2_000.0 / FACTOR):
        metrics["reason"] = "respuesta no compatible con primer orden"
        return None, metrics
    return tau, metrics


class TwoPhaseCalibrator:
    def __init__(self, lab: Lab, output: Path, *, samples: int = 3) -> None:
        self.lab = lab
        self.output = output
        self.samples = samples
        self.started = time.monotonic()
        self.codes = [0, 0, 0, 0]
        self.rows: list[Point] = []
        self.events: list[dict] = []
        self.deadline: float | None = None
        self.current_gain = 1

    def event(self, kind: str, message: str, **data) -> None:
        item = {"at": datetime.now().isoformat(timespec="milliseconds"),
                "kind": kind, "message": message, **data}
        self.events.append(item)
        print(message, flush=True)
        self.save()

    def save(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        csv_path = Path(str(self.output) + ".csv")
        fields = tuple(Point.__dataclass_fields__)
        payload = {"algorithm": "two-phase verified history with safe recovery",
                   "started_at": datetime.now().isoformat(timespec="seconds"),
                   "codes": self.codes, "events": self.events,
                   "measurements": [asdict(row) for row in self.rows]}
        last_error = None
        for _attempt in range(5):
            try:
                with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(asdict(row) for row in self.rows)
                self.output.write_text(json.dumps(payload, indent=2),
                                       encoding="utf-8")
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.10)
        raise last_error

    def set_code(self, stage: int, code: int) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise RuntimeError("presupuesto temporal de calibración agotado")
        if not -255 <= code <= 255:
            raise RuntimeError(f"IDAC{stage} fuera de rango: {code}")
        for attempt in range(3):
            if self.lab.set_idac(stage, code):
                self.codes[stage] = code
                return
            self.event("write_retry", f"reintento IDAC{stage}={code}",
                       stage=stage, code=code, attempt=attempt + 1)
            time.sleep(0.25)
        raise RuntimeError(f"sin ACK IDAC{stage}={code} tras tres reintentos")

    def read(self, channel: int, phase: str, stage: int, code: int,
             *, require_valid: bool = True) -> Point:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise RuntimeError("presupuesto temporal de calibración agotado")
        values = []
        for _ in range(self.samples):
            point = None
            for _attempt in range(4):
                point = self.lab.measure_dc(channel, 2)
                if point is not None and point.ok:
                    break
                time.sleep(0.2)
            if point is None or not point.ok:
                raise RuntimeError(f"sin lectura válida ch{channel}")
            values.append(point)
        mean_uv = int(statistics.median(point.mean_uv for point in values))
        pp_uv = int(statistics.median(point.pp_uv for point in values))
        is_valid = valid_bank(mean_uv)
        result = Point(time.monotonic() - self.started, phase, stage, code,
                       channel, mean_uv, mean_uv - TARGET_UV,
                       real_mv(mean_uv - TARGET_UV), pp_uv, is_valid)
        self.rows.append(result)
        self.save()
        if require_valid and not is_valid:
            raise RuntimeError(
                f"ch{channel} {TAP_NAMES[channel]} fuera de ventana: "
                f"{mean_uv/1000:.3f} mV banco")
        return result

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

    def probe(self, stage: int, channel: int, delta: int,
              *, settle_s: float = 1.0) -> dict:
        """Prueba +/-delta, siempre vuelve al origen y devuelve la pendiente local."""
        origin = self.codes[stage]
        base = self.read(channel, f"IDAC{stage} base", stage, origin)
        measured: dict[int, Point] = {origin: base}
        try:
            for candidate in (origin + delta, origin - delta):
                self.set_code(stage, candidate)
                time.sleep(settle_s)
                measured[candidate] = self.read(
                    channel, f"IDAC{stage} probe", stage, candidate,
                    require_valid=False)
                self.set_code(stage, origin)
                time.sleep(settle_s)
                self.read(channel, f"IDAC{stage} restore", stage, origin,
                          require_valid=False)
        finally:
            self.set_code(stage, origin)
        lo, hi = origin - delta, origin + delta
        if not measured[lo].valid or not measured[hi].valid:
            slope = None
        else:
            slope = ((measured[hi].mean_bank_uv - measured[lo].mean_bank_uv) /
                     float(hi - lo))
        data = {"stage": stage, "channel": channel, "origin": origin,
                "delta": delta, "slope_bank_uv_per_code": slope,
                "slope_real_mv_per_code": None if slope is None else real_mv(slope),
                "points": {str(code): asdict(point)
                           for code, point in measured.items()}}
        self.event("probe", f"IDAC{stage}->ch{channel}: pendiente " +
                   ("no observable" if slope is None else
                    f"{real_mv(slope):+.2f} mV/código"), **data)
        return data

    def probe_upstream(self) -> dict:
        self.configure_neutral()
        time.sleep(5.0)
        pga = self.probe(0, 0, 1, settle_s=1.0)
        bp = self.probe(1, 1, 2, settle_s=2.0)
        return {"pga": pga, "bp": bp}

    def wait_channel_stable(self, channel: int, *, stage: int, code: int,
                            timeout_s: float = 240.0,
                            threshold_real_mv: float = 20.0) -> Point:
        """Espera estabilidad de la media, no del pico-pico de 50 Hz."""
        threshold = threshold_real_mv * 1000.0 / FACTOR
        previous = self.read(channel, "espera estabilidad", stage, code)
        consecutive = 0
        started = time.monotonic()
        while time.monotonic() - started < timeout_s:
            time.sleep(3.0)
            current = self.read(channel, "espera estabilidad", stage, code)
            if abs(current.mean_bank_uv - previous.mean_bank_uv) <= threshold:
                consecutive += 1
                if consecutive >= 4:
                    self.event("stable", f"ch{channel} estable",
                               channel=channel, elapsed_s=time.monotonic()-started,
                               threshold_real_mv=threshold_real_mv)
                    return current
            else:
                consecutive = 0
            previous = current
        raise RuntimeError(f"ch{channel} no se estabilizó en {timeout_s:.0f} s")

    def wait_resolution_stable(self, channel: int, *, stage: int, code: int,
                               timeout_s: float = 60.0) -> Point:
        """Espera hasta que el cambio quede dentro del ruido medido, sin mV fijos."""
        previous = self.read(channel, "espera por resolución", stage, code,
                             require_valid=False)
        started = time.monotonic()
        consecutive = 0
        while time.monotonic() - started < timeout_s:
            time.sleep(5.0)
            current = self.read(channel, "espera por resolución", stage, code,
                                require_valid=False)
            if not current.valid:
                raise RuntimeError(f"ch{channel} salió del rango físico válido")
            resolution = max(previous.pp_bank_uv, current.pp_bank_uv) / 2.0
            if abs(current.mean_bank_uv - previous.mean_bank_uv) <= resolution:
                consecutive += 1
                if consecutive >= 2:
                    return current
            else:
                consecutive = 0
            previous = current
        self.event("settle_timeout", f"ch{channel}: se agotó espera; se usa última lectura",
                   channel=channel, timeout_s=timeout_s)
        return previous

    def ramp_to(self, stage: int, channel: int, target: int,
                *, max_step: int) -> None:
        """Lleva el IDAC al Newton en escalones eléctricos, midiendo cada uno."""
        while self.codes[stage] != target:
            delta = target - self.codes[stage]
            candidate = self.codes[stage] + max(-max_step, min(max_step, delta))
            previous = self.codes[stage]
            self.set_code(stage, candidate)
            time.sleep(0.35)
            trial = self.read(channel, f"IDAC{stage} rampa Newton", stage,
                              candidate, require_valid=False)
            if (not trial.valid or
                    abs(trial.error_bank_uv) > 2.0 * UPSTREAM_GUARD_UV):
                self.set_code(stage, previous)
                raise RuntimeError(f"IDAC{stage}: rampa Newton cruzó guarda")

    def newton_pi(self, stage: int, channel: int, slope: float,
                  *, max_abs_code: int, window_s: float,
                  sample_period_s: float, ramp_step: int,
                  max_pi_step: int = 4,
                  control_delay_s: float = 0.0,
                  kp: float = 0.30, ki: float = 0.04) -> Point:
        """Salto Newton acotado y PI hasta el mínimo discreto medible.
        """
        if abs(slope) < 1.0:
            raise RuntimeError(f"IDAC{stage} sin pendiente útil")
        current = self.read(channel, f"IDAC{stage} inicio", stage,
                            self.codes[stage])
        raw_target = self.codes[stage] - current.error_bank_uv / slope
        target = max(-max_abs_code, min(max_abs_code, int(round(raw_target))))
        self.event("newton", f"IDAC{stage} Newton {self.codes[stage]}->{target}",
                   stage=stage, channel=channel, slope_bank_uv_per_code=slope,
                   raw_target=raw_target, target=target)
        self.ramp_to(stage, channel, target, max_step=ramp_step)

        integral_codes = 0.0
        current = self.read(channel, f"IDAC{stage} post Newton", stage,
                            self.codes[stage], require_valid=False)
        settle_started = time.monotonic()
        while time.monotonic() - settle_started < control_delay_s:
            remaining = control_delay_s - (time.monotonic() - settle_started)
            time.sleep(min(sample_period_s, max(0.0, remaining)))
            current = self.read(channel, f"IDAC{stage} asentando Newton", stage,
                                self.codes[stage], require_valid=False)
            if not current.valid or abs(current.error_bank_uv) > UPSTREAM_GUARD_UV:
                raise RuntimeError(f"IDAC{stage}: Newton cruzó guarda upstream")

        # En la planta lenta, el primer Newton grande todavía puede conservar
        # memoria aun partiendo de un origen estable. Recalcular como máximo
        # tres saltos acotados y asentados evita perseguir ese transitorio con
        # decenas de pasos PI de un código.
        if control_delay_s > 0.0:
            for refinement in range(3):
                raw_refinement = -current.error_bank_uv / slope
                uncertainty_codes = max(
                    0.5, current.pp_bank_uv / (2.0 * abs(slope)))
                if abs(raw_refinement) <= max(2.0, uncertainty_codes):
                    break
                correction = int(round(max(-32.0, min(32.0,
                                                     raw_refinement))))
                candidate = max(-max_abs_code,
                                min(max_abs_code, self.codes[stage] + correction))
                if candidate == self.codes[stage]:
                    break
                before = self.codes[stage]
                self.event("newton_refine",
                           f"IDAC{stage} Newton refinado {before}->{candidate}",
                           stage=stage, channel=channel,
                           refinement=refinement + 1,
                           raw_correction=raw_refinement,
                           uncertainty_codes=uncertainty_codes)
                self.ramp_to(stage, channel, candidate, max_step=ramp_step)
                time.sleep(control_delay_s)
                current = self.read(channel,
                                    f"IDAC{stage} Newton refinado asentado",
                                    stage, self.codes[stage],
                                    require_valid=False)

        centered = 0
        range_limited = False
        convergence_resolution_codes = None
        max_iterations = 2 * max_abs_code + 4
        for cycle in range(max_iterations):
            time.sleep(sample_period_s)
            current = self.read(channel, f"IDAC{stage} PI", stage,
                                self.codes[stage], require_valid=False)
            if not current.valid or abs(current.error_bank_uv) > UPSTREAM_GUARD_UV:
                raise RuntimeError(f"IDAC{stage}: PI cruzó guarda upstream")
            raw_correction = -current.error_bank_uv / slope
            uncertainty_codes = max(
                0.5, current.pp_bank_uv / (2.0 * abs(slope)))
            if abs(raw_correction) <= uncertainty_codes:
                centered += 1
                if centered >= 2:
                    convergence_resolution_codes = uncertainty_codes
                    break
                continue
            centered = 0
            integral_codes = max(-24.0, min(24.0,
                integral_codes + raw_correction * sample_period_s / max(window_s, 1.0)))
            effort = kp * raw_correction + ki * integral_codes
            correction = int(round(max(-max_pi_step, min(max_pi_step, effort))))
            # Evitar que la ganancia pequeña seguida del redondeo mate el lazo
            # cuando todavía falta más de media LSB. Sigue siendo PI: el signo
            # y la necesidad del paso salen del error realimentado y la
            # pendiente medida, no de una semilla.
            if correction == 0 and abs(raw_correction) >= 0.50:
                correction = 1 if raw_correction > 0.0 else -1
            if correction:
                candidate = max(-max_abs_code,
                                min(max_abs_code, self.codes[stage] + correction))
                if candidate != self.codes[stage]:
                    before = self.codes[stage]
                    self.set_code(stage, candidate)
                    self.event("pi", f"IDAC{stage} PI {before}->{candidate}",
                               stage=stage, channel=channel,
                               error_real_mv=current.error_real_mv,
                               correction=correction)
                else:
                    range_limited = True
                    break
        else:
            raise RuntimeError(f"IDAC{stage}: PI agotó el rango de búsqueda")
        verification = []
        for index in range(3):
            verification.append(self.read(
                channel, f"IDAC{stage} verificación {index + 1}/3", stage,
                self.codes[stage]))
            if index < 2:
                time.sleep(min(1.0, sample_period_s))
        median_error = int(statistics.median(
            point.error_bank_uv for point in verification))
        final = Point(
            elapsed_s=time.monotonic() - self.started,
            phase=f"IDAC{stage} verificación agregada",
            stage=stage, code=self.codes[stage], channel=channel,
            mean_bank_uv=TARGET_UV + median_error,
            error_bank_uv=median_error,
            error_real_mv=real_mv(median_error),
            pp_bank_uv=int(statistics.median(
                point.pp_bank_uv for point in verification)),
            valid=all(point.valid for point in verification),
        )
        residual_codes = abs(median_error / slope)
        resolution_codes = max(
            0.5, final.pp_bank_uv / (2.0 * abs(slope)),
            convergence_resolution_codes or 0.5)
        self.event("closed_loop", f"IDAC{stage} final "
                   f"{final.error_real_mv:+.1f} mV", stage=stage,
                   channel=channel, residual_codes=residual_codes,
                   resolution_codes=resolution_codes,
                   range_limited=range_limited,
                   verification=[asdict(point) for point in verification],
                   final=asdict(final))
        if not range_limited and residual_codes > resolution_codes:
            self.event("discrete_residual",
                       f"IDAC{stage}: residual {residual_codes:.2f} códigos; "
                       "se conserva el punto válido",
                       stage=stage, residual_codes=residual_codes,
                       resolution_codes=resolution_codes)
        return final

    def estimate_tau_bp(self, pga_slope: float, *, duration_s: float = 90.0) -> tuple[float, dict]:
        """Excita IDAC0 un código y estima el polo lento leyendo BPo/ch1."""
        origin = self.codes[0]
        # Elegir el signo que mantenga ch0 cerca de Vref según la pendiente.
        ch0 = self.read(0, "tau guarda ch0", 0, origin)
        direction = -1 if ch0.error_bank_uv * pga_slope > 0 else 1
        candidate = origin + direction
        series: list[tuple[float, float]] = []
        try:
            self.set_code(0, candidate)
            t0 = time.monotonic()
            while time.monotonic() - t0 <= duration_s:
                bp = self.read(1, "tau BP", 0, candidate)
                series.append((time.monotonic() - t0, float(bp.mean_bank_uv)))
                time.sleep(max(0.0, 3.0 - (time.monotonic() - t0) % 3.0))
        finally:
            self.set_code(0, origin)
        tau, metrics = fit_first_order(series)
        if tau is None:
            self.event("tau_rejected", "Tau BP rechazada", metrics=metrics)
            raise RuntimeError(f"tau BP no fiable: {metrics.get('reason')}")
        self.event("tau", f"Tau BP = {tau:.1f} s", metrics=metrics,
                   excitation={"stage": 0, "delta": direction, "channel": 1})
        return tau, metrics

    def _fast_point(self, stage: int, channel: int, phase: str) -> Point:
        point = self.read(channel, phase, stage, self.codes[stage],
                          require_valid=False)
        if not ADC_ABSOLUTE_UV[0] <= point.mean_bank_uv <= ADC_ABSOLUTE_UV[1]:
            raise RuntimeError(
                f"IDAC{stage}: ch{channel} fuera de guarda ADC absoluta")
        return point

    def _walk_fast(self, stage: int, channel: int, target: int,
                   points: dict[int, Point], *, dwell_s: float = 0.15) -> Point:
        """Rampa de un código, observada aun si parte desde saturación."""
        target = max(-255, min(255, target))
        last = points.get(self.codes[stage])
        while self.codes[stage] != target:
            direction = 1 if target > self.codes[stage] else -1
            self.set_code(stage, self.codes[stage] + direction)
            time.sleep(dwell_s)
            last = self._fast_point(
                stage, channel, f"IDAC{stage} bracket {self.codes[stage]}")
            points[self.codes[stage]] = last
        assert last is not None
        return last

    def center_fast(self, stage: int, channel: int,
                    *, settle_s: float = 1.0) -> dict:
        """Búsqueda multirresolución por saltos; sólo el final prueba ±1 LSB.

        Cada candidato se mide y se vuelve al mejor punto conocido. Si ya hay
        un punto físicamente válido, cualquier candidato saturado se descarta.
        Así se evita el antiguo barrido exhaustivo código por código.
        """
        origin = self.codes[stage]
        current = self._fast_point(stage, channel, f"IDAC{stage} base rápida")
        started_invalid = not current.valid
        best_code, best_point = origin, current
        history = [{"step": None, "code": origin, "point": asdict(current)}]
        points: dict[int, Point] = {origin: current}

        def score(point: Point) -> tuple[int, float]:
            if point.valid:
                return (0, abs(point.error_bank_uv))
            if point.mean_bank_uv < VALID_MIN_UV:
                distance = VALID_MIN_UV - point.mean_bank_uv
            elif point.mean_bank_uv > VALID_MAX_UV:
                distance = point.mean_bank_uv - VALID_MAX_UV
            else:
                distance = abs(point.error_bank_uv)
            return (1, distance)

        # Desde saturación, una sonda chica puede no mover el número crudo y no
        # revela dirección. Probar ambos extremos una sola vez da máxima
        # información y evita recorrer 255 códigos incrementalmente.
        if not current.valid:
            endpoint_best = (best_code, best_point)
            for candidate in (-255, 255):
                self.set_code(stage, candidate)
                time.sleep(settle_s)
                trial = self._fast_point(
                    stage, channel, f"IDAC{stage} extremo de rescate")
                points[candidate] = trial
                history.append({"step": "endpoint", "code": candidate,
                                "point": asdict(trial)})
                if score(trial) < score(endpoint_best[1]):
                    endpoint_best = (candidate, trial)
            best_code, best_point = endpoint_best
            self.set_code(stage, best_code)
            time.sleep(settle_s)

        # Desde un punto válido no vale la pena arriesgar saltos enormes. Los
        # saltos 64/32 quedan reservados al rescate desde fuera de rango.
        steps = ((16, 8, 4, 2, 1) if current.valid
                 else (128, 64, 32, 16, 8, 4, 2, 1))
        for step in steps:
            round_best = (best_code, best_point)
            for sign in (-1, 1):
                candidate = max(-255, min(255, best_code + sign * step))
                if candidate == best_code:
                    continue
                self.set_code(stage, candidate)
                time.sleep(settle_s)
                trial = self._fast_point(
                    stage, channel, f"IDAC{stage} salto {step:+d}")
                points[candidate] = trial
                history.append({"step": step, "code": candidate,
                                "point": asdict(trial)})
                if score(trial) < score(round_best[1]):
                    round_best = (candidate, trial)
                self.set_code(stage, best_code)
                time.sleep(settle_s)
                # Una excursión a riel puede quedar visible por memoria del LP.
                # No se toma otra decisión hasta recuperar el mejor punto válido.
                if best_point.valid and not trial.valid:
                    restored = self._fast_point(
                        stage, channel, f"IDAC{stage} comprobación restauración")
                    deadline = time.monotonic() + 20.0
                    while not restored.valid and time.monotonic() < deadline:
                        time.sleep(2.0)
                        restored = self._fast_point(
                            stage, channel, f"IDAC{stage} recuperando de riel")
                    if not restored.valid:
                        raise RuntimeError(
                            f"IDAC{stage}: no recuperó el último punto válido")
                    best_point = restored
            if round_best[0] != best_code:
                best_code, best_point = round_best
                self.set_code(stage, best_code)
                time.sleep(settle_s)
                best_point = self._fast_point(
                    stage, channel, f"IDAC{stage} mejor salto {step}")
                if round_best[1].valid and not best_point.valid:
                    deadline = time.monotonic() + 20.0
                    while not best_point.valid and time.monotonic() < deadline:
                        time.sleep(2.0)
                        best_point = self._fast_point(
                            stage, channel, f"IDAC{stage} asentando mejor salto")
                    if not best_point.valid:
                        raise RuntimeError(
                            f"IDAC{stage}: el candidato válido no se sostuvo")
                if started_invalid and best_point.valid:
                    self.event("rail_rescued",
                               f"IDAC{stage}->ch{channel} salió del riel en "
                               f"{best_code}; se conserva el primer punto válido",
                               stage=stage, channel=channel, code=best_code,
                               point=asdict(best_point))
                    break

        self.set_code(stage, best_code)
        time.sleep(settle_s)
        current = self._fast_point(stage, channel, f"IDAC{stage} mínimo final")
        valid = sorted((code, point) for code, point in points.items()
                       if point.valid)
        slopes = [(pb.error_bank_uv - pa.error_bank_uv) / float(cb - ca)
                  for (ca, pa), (cb, pb) in zip(valid, valid[1:]) if cb != ca]
        slope = statistics.median(slopes) if slopes else None
        result = {"stage": stage, "channel": channel,
                  "code": self.codes[stage], "slope_bank_uv_per_code": slope,
                  "final": asdict(current), "search_history": history,
                  "method": "multiresolution jumps 64..1; no exhaustive scan"}
        self.event("fast_minimum",
                   f"IDAC{stage}->ch{channel} mínimo en {self.codes[stage]}: "
                   f"{current.error_real_mv:+.1f} mV",
                   result=result)
        return result

    def verify_lp_hold(self, duration_s: float = 60.0, *, samples: int = 10) -> dict:
        """Recuento estacionario de LPo; registra datos, no impone banda mV."""
        interval_s = duration_s / max(1, samples - 1)
        hold: list[Point] = []
        for index in range(samples):
            hold.append(self._fast_point(3, 4, f"LPo hold {index + 1}/{samples}"))
            if index + 1 < samples:
                time.sleep(max(0.0, interval_s - 2.0 * self.samples))
        errors = [point.error_bank_uv for point in hold]
        result = {"duration_s": duration_s, "samples": [asdict(p) for p in hold],
                  "median_error_real_mv": real_mv(statistics.median(errors)),
                  "span_real_mv": real_mv(max(errors) - min(errors)),
                  "all_valid": all(point.valid for point in hold)}
        self.event("lp_hold", "Recuento estacionario LPo completado", result=result)
        return result

    def calibrate_phase1_from_history(self, history: dict) -> dict:
        """Aplica y verifica el punto histórico, sin barrido ni corrección."""
        self.configure_neutral()
        # COM8 reinicia el ESP, no el PSoC ni la memoria analógica del filtro.
        # Descargar primero en neutro hace repetible el arranque entre campañas.
        time.sleep(30.0)
        phase = history.get("phase1", {})
        seed = phase.get("codes", [19, 141])
        for stage in (0, 1):
            self.set_code(stage, int(seed[stage]))
        time.sleep(30.0)
        finals = []
        for stage, channel in ((0, 0), (1, 1)):
            point = self.read(channel, f"fase1 historial IDAC{stage}",
                              stage, self.codes[stage], require_valid=False)
            if not point.valid:
                raise RuntimeError(f"historial IDAC{stage} dejó ch{channel} inválido")
            # El par histórico proviene de ventanas largas. Durante el arranque
            # rápido sólo se verifica: alternar un vecino por ruido transitorio
            # puede destruir el margen aguas abajo con PGA x50.
            finals.append(point)
        result = {"pass": True, "codes": self.codes[:2],
                  "pga": asdict(finals[0]), "bp": asdict(finals[1]),
                  "elapsed_s": time.monotonic() - self.started,
                  "source": "history verified without search or correction"}
        self.event("phase1_fast_pass",
                   f"Fase 1 rápida PASS: {self.codes[:2]}", result=result)
        return result

    def calibrate_gain_step(self, gain: int, history: dict) -> dict:
        """Cambia PGAout y ejecuta sólo fase 2, reutilizando el mejor historial."""
        started = time.monotonic()
        previous_gain = self.current_gain
        previous_codes = list(self.codes)
        remembered = history.get("gains", {}).get(str(gain))
        if remembered:
            codes = remembered.get("codes", self.codes)
            self.set_code(2, int(codes[2]))
            self.set_code(3, int(codes[3]))
        if not self.lab.set_gain("pgaout", GAIN_CODES.index(gain)):
            raise RuntimeError(f"sin ACK PGAout x{gain}")
        self.current_gain = gain
        time.sleep(1.0)
        if remembered:
            attempts = []
            for verify_attempt in range(1, 4):
                if verify_attempt == 2:
                    # Una lectura apenas fuera del rango físico puede ser
                    # memoria del cambio de ganancia. Reaplicar el punto y
                    # esperar es seguro; barrer extremos no lo es.
                    self.set_code(2, int(remembered["codes"][2]))
                    self.set_code(3, int(remembered["codes"][3]))
                    time.sleep(4.0)
                elif verify_attempt == 3:
                    # Si insistir una vez no alcanza, descargar sólo la fase 2
                    # en x1. No se recalibra upstream y no se exploran rieles.
                    if not self.lab.set_gain(
                            "pgaout", GAIN_CODES.index(previous_gain)):
                        raise RuntimeError(
                            f"x{gain}: sin ACK al iniciar recuperación neutra")
                    self.current_gain = previous_gain
                    self.set_code(2, 0)
                    self.set_code(3, 0)
                    self.event(
                        "history_neutral_recovery",
                        f"x{gain}: descargando fase 2 en x{previous_gain} "
                        "antes del último intento",
                        gain=gain, restored_gain=previous_gain,
                        neutral_s=30.0)
                    time.sleep(30.0)
                    self.set_code(2, int(remembered["codes"][2]))
                    self.set_code(3, int(remembered["codes"][3]))
                    if not self.lab.set_gain("pgaout", GAIN_CODES.index(gain)):
                        raise RuntimeError(
                            f"x{gain}: sin ACK después de recuperación neutra")
                    self.current_gain = gain
                    time.sleep(1.0)
                control_samples = self.samples
                self.samples = max(3, self.samples)
                try:
                    quick = {ch: asdict(self._fast_point(
                             3 if ch == 4 else (2 if ch in (2, 3) else ch), ch,
                             f"x{gain} historial verificación "
                             f"{verify_attempt}/3")) for ch in range(5)}
                finally:
                    self.samples = control_samples
                quick_pass = (quick[0]["valid"] and quick[1]["valid"] and
                              quick[4]["valid"])
                attempts.append({"attempt": verify_attempt,
                                 "pass": quick_pass, "points": quick})
                if quick_pass:
                    result = {"gain": gain, "pass": True,
                              "codes": list(self.codes), "final": quick,
                              "all_taps_valid": all(
                                  p["valid"] for p in quick.values()),
                              "elapsed_s": time.monotonic() - started,
                              "history_verify_attempts": attempts,
                              "source": "history verified; no search"}
                    history.setdefault("gains", {})[str(gain)] = {
                        "codes": list(self.codes),
                        "verified_at": datetime.now().isoformat(),
                        "lp_error_real_mv": quick[4]["error_real_mv"],
                        "source": "history reverified without search"}
                    history["phase1"]["codes"] = self.codes[:2]
                    save_history(history)
                    self.event("gain_pass",
                               f"PGAout x{gain} PASS histórico en "
                               f"{result['elapsed_s']:.1f}s: {self.codes}",
                               result=result)
                    return result
                if verify_attempt == 1:
                    self.event(
                        "history_verify_retry",
                        f"x{gain}: punto histórico no válido; reverificación 2/3",
                        gain=gain, attempt=verify_attempt, points=quick)

            # Un punto ya conocido que deja de ser válido indica deriva o
            # memoria lenta, no autorización para explorar rieles. Volver al
            # estado anterior y abortar el cambio conserva la placa legible.
            if not self.lab.set_gain("pgaout", GAIN_CODES.index(previous_gain)):
                raise RuntimeError(
                    f"x{gain}: sin ACK al restaurar PGAout x{previous_gain}")
            self.current_gain = previous_gain
            for stage in (2, 3):
                self.set_code(stage, previous_codes[stage])
            self.event(
                "history_verify_abort",
                f"x{gain}: historial inválido tras 3 intentos; "
                f"restaurado x{previous_gain} sin barrer rieles",
                gain=gain, restored_gain=previous_gain,
                restored_codes=previous_codes, attempts=attempts)
            raise RuntimeError(
                f"PGAout x{gain}: historial inválido tras tres verificaciones")
        grid_codes2 = list(dict.fromkeys(
            [self.codes[2], 255, 128, 0, -128, -255]))
        grid_codes3 = list(dict.fromkeys(
            [self.codes[3], 128, 0, -128, 255, -255]))
        grid = []
        grid_rescued = False
        for code2 in grid_codes2:
            if grid_rescued:
                break
            self.set_code(2, code2)
            for code3 in grid_codes3:
                self.set_code(3, code3)
                time.sleep(0.75)
                point = self._fast_point(3, 4, f"x{gain} malla rescate")
                grid.append({"idac2": code2, "idac3": code3,
                             "lp": asdict(point)})
                if point.valid:
                    grid_rescued = True
                    self.event("grid_rescued",
                               f"x{gain}: malla rescató LPo en "
                               f"IDAC2={code2}, IDAC3={code3}",
                               gain=gain, idac2=code2, idac3=code3,
                               point=asdict(point))
                    break
        if grid_rescued:
            sum_result = {"method": "coarse 2D grid", "points": grid}
            lp_result = {"method": "first physically valid point",
                         "code": self.codes[3], "point": grid[-1]["lp"]}
        else:
            sum_result = None
            lp_result = None
        if not grid_rescued:
            sum_entry = self._fast_point(2, 3, f"x{gain} SUM entrada")
            if sum_entry.valid:
                sum_result = {"kept": True, "code": self.codes[2],
                              "point": asdict(sum_entry),
                              "reason": "already readable; avoid rail excursion"}
            else:
                sum_result = self.center_fast(2, 3, settle_s=0.25)
            lp_entry = self._fast_point(3, 4, f"x{gain} LPo entrada")
            if lp_entry.valid:
                lp_result = {"kept": True, "code": self.codes[3],
                             "point": asdict(lp_entry),
                             "reason": "already readable; functional priority"}
            else:
                lp_result = self.center_fast(3, 4, settle_s=0.25)
        lp_point = self._fast_point(3, 4, f"x{gain} LPo post ajuste")
        joint_rescue = None
        if not lp_point.valid:
            fine_idac2 = self.center_fast(2, 4, settle_s=0.25)
            fine_idac3 = self.center_fast(3, 4, settle_s=0.25)
            joint_rescue = {"idac2_on_lp": fine_idac2,
                            "idac3_final": fine_idac3}
        # Control usa una ventana por velocidad; la aceptación usa tres por tap
        # para descartar memoria del FIR/AMux después de una excursión a riel.
        control_samples = self.samples
        self.samples = max(3, self.samples)
        try:
            final = {ch: asdict(self._fast_point(
                        3 if ch == 4 else (2 if ch in (2, 3) else ch), ch,
                        f"x{gain} verificación robusta")) for ch in range(5)}
        finally:
            self.samples = control_samples
        all_taps_valid = all(item["valid"] for item in final.values())
        passed = final[0]["valid"] and final[1]["valid"] and final[4]["valid"]
        result = {"gain": gain, "pass": passed, "codes": list(self.codes),
                  "sum": sum_result, "lp": lp_result,
                  "joint_rescue": joint_rescue, "final": final,
                  "all_taps_valid": all_taps_valid,
                  "acceptance": "PGAgain, BPo and LPo valid; internal taps recorded",
                  "elapsed_s": time.monotonic() - started}
        self.event("gain_pass" if passed else "gain_fail",
                   f"PGAout x{gain} {'PASS' if passed else 'FAIL'} "
                   f"en {result['elapsed_s']:.1f}s: {self.codes}", result=result)
        if not passed:
            raise RuntimeError(f"PGAout x{gain}: algún tap saturado")
        history.setdefault("gains", {})[str(gain)] = {
            "codes": list(self.codes), "verified_at": datetime.now().isoformat(),
            "lp_error_real_mv": final[4]["error_real_mv"]}
        history["phase1"] = {
            "codes": self.codes[:2],
            "slopes_bank_uv_per_code": history.get("phase1", {}).get(
                "slopes_bank_uv_per_code", [3000.0, 185.7]),
            "verified_at": datetime.now().isoformat()}
        save_history(history)
        return result

    def calibrate_gain_campaign(self, gains: list[int], *, budget_s: float) -> dict:
        """Fase 1 una vez (<3 min) y cambios sucesivos usando sólo fase 2."""
        history = load_history()
        campaign_started = time.monotonic()
        self.deadline = campaign_started + budget_s
        phase1 = self.calibrate_phase1_from_history(history)
        initial_elapsed = time.monotonic() - campaign_started
        if initial_elapsed > budget_s:
            raise RuntimeError("fase 1 excedió el presupuesto de 3 minutos")
        # El techo aplica a la puesta en marcha. Los cambios posteriores tienen
        # su propio tiempo registrado y no vuelven a ejecutar fase 1.
        self.deadline = None
        results = []
        for gain in gains:
            try:
                results.append(self.calibrate_gain_step(gain, history))
            except Exception as exc:
                self.event("gain_campaign_abort",
                           f"Se detiene escalada en x{gain}: {exc}", gain=gain)
                break
        passed = len(results) == len(gains) and all(r["pass"] for r in results)
        result = {"pass": passed, "phase1": phase1, "gains": results,
                  "requested_gains": gains,
                  "initial_elapsed_s": initial_elapsed,
                  "total_elapsed_s": time.monotonic() - campaign_started}
        self.event("gain_campaign_result",
                   f"Campaña ganancias: {len(results)}/{len(gains)} PASS",
                   result=result)
        return result

    def characterize_x2_seeds(self, repeats: int = 3,
                              idac0_codes: list[int] | None = None,
                              idac1_codes: list[int] | None = None) -> dict:
        """Compara vecinos mínimos de fase 1 por repetibilidad al entrar x2."""
        idac0_codes = idac0_codes or [19, 20]
        idac1_codes = idac1_codes or [157, 159, 161]
        candidates = [(id0, id1) for id0 in idac0_codes
                      for id1 in idac1_codes]
        rows = []
        control_samples = self.samples
        self.samples = max(3, self.samples)
        try:
            for id0, id1 in candidates:
                for repetition in range(1, repeats + 1):
                    self.configure_neutral()
                    time.sleep(20.0)
                    self.set_code(0, id0)
                    self.set_code(1, id1)
                    self.set_code(2, 254)
                    self.set_code(3, 83)
                    time.sleep(30.0)
                    x1 = {ch: asdict(self._fast_point(
                          min(ch, 3), ch, "semilla x1 verificación"))
                          for ch in range(5)}
                    if not self.lab.set_gain("pgaout", GAIN_CODES.index(2)):
                        raise RuntimeError("sin ACK PGAout x2")
                    time.sleep(3.0)
                    x2 = {ch: asdict(self._fast_point(
                          min(ch, 3), ch, "semilla x2 verificación"))
                          for ch in range(5)}
                    functional = (x2[0]["valid"] and x2[1]["valid"] and
                                  x2[4]["valid"])
                    all_valid = all(point["valid"] for point in x2.values())
                    row = {"idac0": id0, "idac1": id1,
                           "repetition": repetition, "x1": x1, "x2": x2,
                           "functional_pass": functional,
                           "all_taps_valid": all_valid}
                    rows.append(row)
                    self.event("seed_trial",
                               f"Semilla [{id0},{id1}] rep {repetition}/{repeats}: "
                               f"x2 {'PASS' if functional else 'FAIL'}",
                               result=row)
        finally:
            self.samples = control_samples
            self.configure_neutral()
        summary = []
        for id0, id1 in candidates:
            subset = [row for row in rows
                      if row["idac0"] == id0 and row["idac1"] == id1]
            summary.append({"idac0": id0, "idac1": id1,
                            "functional_passes": sum(r["functional_pass"] for r in subset),
                            "all_taps_passes": sum(r["all_taps_valid"] for r in subset),
                            "repeats": repeats})
        best = max(summary, key=lambda item: (item["functional_passes"],
                                               item["all_taps_passes"],
                                               -abs(item["idac0"] - 20),
                                               -abs(item["idac1"] - 159)))
        result = {"pass": best["functional_passes"] == repeats,
                  "best": best, "summary": summary, "trials": rows}
        self.event("seed_characterization",
                   f"Caracterización x2: mejor [{best['idac0']},{best['idac1']}] "
                   f"{best['functional_passes']}/{repeats} PASS",
                   result=result)
        if result["pass"]:
            history = load_history()
            history["phase1"]["codes"] = [best["idac0"], best["idac1"]]
            history.setdefault("gains", {})["2"] = {
                "codes": [best["idac0"], best["idac1"], 254, 83],
                "source": f"seed characterization {repeats}/{repeats} PASS",
                "verified_at": datetime.now().isoformat()}
            save_history(history)
        return result

    def calibrate_phase1_minimal(self) -> dict:
        """Fase 1 corta: sólo mínimos de PGAgain y BPo, sin tocar downstream."""
        self.configure_neutral()
        time.sleep(5.0)
        pga_probe = self.probe(0, 0, 1, settle_s=1.0)
        pga_slope = pga_probe["slope_bank_uv_per_code"]
        if pga_slope is None:
            raise RuntimeError("no se pudo identificar IDAC0->PGAgain")
        pga_final = self.newton_pi(0, 0, pga_slope, max_abs_code=32,
                                   window_s=20.0, sample_period_s=1.5,
                                   ramp_step=8, max_pi_step=2)

        base = self.wait_resolution_stable(1, stage=1, code=0,
                                           timeout_s=45.0)
        probe_code = 64
        self.set_code(1, probe_code)
        probe = self.wait_resolution_stable(1, stage=1, code=probe_code,
                                            timeout_s=60.0)
        slope = (probe.mean_bank_uv - base.mean_bank_uv) / probe_code
        if abs(slope) < 1.0:
            raise RuntimeError("IDAC1->BPo sin pendiente útil")
        target = int(round(-base.error_bank_uv / slope))
        target = max(-255, min(255, target))
        self.set_code(1, target)
        bp_final = self.wait_resolution_stable(1, stage=1, code=target,
                                               timeout_s=60.0)
        refinements = []
        for _ in range(2):
            uncertainty_codes = max(0.5, bp_final.pp_bank_uv /
                                    (2.0 * abs(slope)))
            correction_float = -bp_final.error_bank_uv / slope
            if abs(correction_float) <= uncertainty_codes:
                break
            correction = int(round(max(-64.0, min(64.0, correction_float))))
            candidate = max(-255, min(255, self.codes[1] + correction))
            if candidate == self.codes[1]:
                break
            self.set_code(1, candidate)
            bp_final = self.wait_resolution_stable(
                1, stage=1, code=candidate, timeout_s=45.0)
            refinements.append({"code": candidate, "point": asdict(bp_final)})

        pga_check = self.read(0, "fase1 verificación PGAgain", 0, self.codes[0])
        bp_check = self.read(1, "fase1 verificación BPo", 1, self.codes[1])
        passed = pga_check.valid and bp_check.valid
        result = {"pass": passed, "codes": self.codes[:2],
                  "pga": asdict(pga_final), "bp": asdict(bp_final),
                  "verified": {"pga": asdict(pga_check),
                               "bp": asdict(bp_check)},
                  "idac1_slope_bank_uv_per_code": slope,
                  "refinements": refinements,
                  "criterion": "minimum measurable PGAgain and BPo only"}
        self.event("phase1_pass" if passed else "phase1_fail",
                   f"Fase 1 {'PASS' if passed else 'FAIL'}: "
                   f"IDAC0={self.codes[0]} IDAC1={self.codes[1]}", result=result)
        if not passed:
            raise RuntimeError("fase 1 fuera del rango físico")
        return result

    def calibrate_phase2_lp(self) -> dict:
        """Fase 2 x1: IDAC2 rescata SUM e IDAC3 hace LPo legible."""
        sum_rescue = self.center_fast(2, 3)
        coarse = self.center_fast(3, 4)
        time.sleep(2.0)
        final_lp = self._fast_point(3, 4, "fase2 LPo final")
        final = {ch: asdict(self._fast_point(
                    3 if ch == 4 else (2 if ch in (2, 3) else ch), ch,
                    "fase2 verificación final")) for ch in range(5)}
        passed = all(final[ch]["valid"] for ch in range(5))
        result = {"pass": passed, "gain": 1, "codes": list(self.codes),
                  "sum_rescue": sum_rescue, "idac3_coarse": coarse,
                  "lp": asdict(final_lp),
                  "final": final,
                  "criterion": "all taps physically valid; minimize LPo"}
        self.event("phase2_pass" if passed else "phase2_fail",
                   f"Fase 2 x1 {'PASS' if passed else 'FAIL'}: "
                   f"IDAC2={self.codes[2]} IDAC3={self.codes[3]} "
                   f"LPo={final_lp.error_real_mv:+.1f} mV", result=result)
        if not passed:
            raise RuntimeError("fase 2 no logró dejar todas las señales legibles")
        return result

    def calibrate_full(self, *, keep_on_pass: bool = False) -> dict:
        """Rutinas A+B completas; LPo/ch4 es el objetivo final prioritario."""
        upstream = self.calibrate_phase1_minimal()
        phase2 = self.calibrate_phase2_lp()
        lp_hold = self.verify_lp_hold()
        final = {ch: asdict(self._fast_point(3 if ch == 4 else 2, ch,
                                             "verificación final x1"))
                 for ch in range(5)}
        passed = all(final[ch]["valid"] for ch in range(5)) and lp_hold["all_valid"]
        result = {"pass": passed, "gain": 1, "codes": list(self.codes),
                  "phase1": upstream, "phase2": phase2,
                  "lp_hold": lp_hold, "final": final,
                  "priority": "functional x1, all taps readable, LPo minimum"}
        self.event("full_pass" if passed else "full_fail",
                   f"Dos fases x1 {'PASS' if passed else 'FAIL'}: {self.codes}",
                   result=result)
        if not passed:
            raise RuntimeError("verificación final x24 fuera de ventana")
        if not keep_on_pass:
            self.configure_neutral()
        return result

    def calibrate_upstream(self, *, keep_on_pass: bool = False,
                           center_bp: bool = True) -> dict:
        """Implementación de la rutina A hasta IDAC1; no toca IDAC2/IDAC3."""
        self.configure_neutral()
        time.sleep(5.0)
        pga_probe = self.probe(0, 0, 1, settle_s=1.0)
        pga_slope = pga_probe["slope_bank_uv_per_code"]
        if pga_slope is None:
            raise RuntimeError("no se pudo identificar IDAC0->PGAgain")
        pga_final = self.newton_pi(0, 0, pga_slope, max_abs_code=32,
                                   window_s=30.0, sample_period_s=1.5,
                                   ramp_step=4, max_pi_step=2)
        self.wait_channel_stable(1, stage=0, code=self.codes[0])
        tau = None
        for tau_attempt in range(2):
            try:
                tau, _ = self.estimate_tau_bp(pga_slope)
                break
            except RuntimeError:
                if tau_attempt:
                    raise
                self.event("tau_retry", "Reintentando estimación de tau BP")
                self.wait_channel_stable(1, stage=0, code=self.codes[0])
        assert tau is not None
        if not center_bp:
            pga_check = self.read(0, "verificación PGAgain", 0, self.codes[0])
            bp_guard = self.read(1, "guarda BPo", 1, self.codes[1],
                                 require_valid=False)
            passed = pga_check.valid and bp_guard.valid
            result = {"pass": passed, "tau_s": tau,
                      "codes": self.codes[:2], "pga": asdict(pga_final),
                      "verified": {"pga": asdict(pga_check),
                                   "bp_guard": asdict(bp_guard)},
                      "bp_role": "physical-range guard, not centering objective"}
            self.event("upstream_guard_pass" if passed else "upstream_guard_fail",
                       f"Upstream guarda {'PASS' if passed else 'FAIL'}: "
                       f"IDAC0={self.codes[0]} IDAC1={self.codes[1]}", result=result)
            if not passed:
                raise RuntimeError("BPo fuera de guarda antes de buscar OPA_SUM")
            return result
        # La estabilidad observada reemplaza otra espera fija y certifica que el
        # escalón de tau ya no contamina la identificación de IDAC1.
        self.wait_channel_stable(1, stage=0, code=self.codes[0],
                                 timeout_s=max(120.0, 4.0 * tau))
        bp_probe = self.probe(1, 1, 32, settle_s=2.0 * tau)
        bp_slope = bp_probe["slope_bank_uv_per_code"]
        if bp_slope is None:
            raise RuntimeError("no se pudo identificar IDAC1->BPo")
        # La última restauración del ABBA ocurre tras el punto -delta. Aunque
        # espere 2*tau, todavía puede conservar ~13,5 % del transitorio y sesgar
        # decenas de mV el error usado por Newton. Exigir estabilidad observada
        # en el origen antes de calcular el salto evita convertir esa memoria en
        # una corrección insuficiente.
        self.wait_channel_stable(1, stage=1, code=self.codes[1],
                                 timeout_s=max(120.0, 4.0 * tau))
        bp_final = self.newton_pi(1, 1, bp_slope, max_abs_code=255,
                                  window_s=3.0 * tau,
                                  sample_period_s=max(3.0, 0.20 * tau),
                                   ramp_step=16, max_pi_step=1,
                                   control_delay_s=2.0 * tau,
                                  kp=0.12, ki=0.02)
        # Verificación común: la propia ventana Newton+PI ya consumió 2tau.
        pga_check = self.read(0, "verificación upstream", 0, self.codes[0])
        bp_check = self.read(1, "verificación upstream", 1, self.codes[1])
        passed = pga_check.valid and bp_check.valid
        result = {"pass": passed, "tau_s": tau, "codes": self.codes[:2],
                  "pga": asdict(pga_final), "bp": asdict(bp_final),
                  "verified": {"pga": asdict(pga_check), "bp": asdict(bp_check)}}
        self.event("upstream_pass" if passed else "upstream_fail",
                   f"Upstream {'PASS' if passed else 'FAIL'}: "
                   f"IDAC0={self.codes[0]} IDAC1={self.codes[1]}", result=result)
        if not passed:
            raise RuntimeError("verificación upstream fuera de ventana válida")
        if not keep_on_pass:
            self.set_code(0, 0)
            self.set_code(1, 0)
        return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=("probe-upstream", "calibrate-upstream",
                                     "calibrate-full", "calibrate-gains",
                                     "characterize-x2"))
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--keep-on-pass", action="store_true")
    ap.add_argument("--repeat", type=int, default=1,
                    help="cantidad de corridas consecutivas desde cero")
    ap.add_argument("--samples", type=int, default=1,
                    help="ventanas ADC por lectura; la verificación repite puntos")
    ap.add_argument("--gains", default="1,2,4,8,16,24,32,48,50")
    ap.add_argument("--budget-s", type=float, default=180.0,
                    help="techo de puesta en marcha/fase 1")
    ap.add_argument("--save-eeprom", action="store_true",
                    help="tras PASS total, guarda en EEPROM el punto x1 verificado")
    ap.add_argument("--seed-repeats", type=int, default=3)
    ap.add_argument("--campaign-repeats", type=int, default=1,
                    help="reinicios completos de la secuencia de ganancias")
    ap.add_argument("--seed-idac0", default="19,20")
    ap.add_argument("--seed-idac1", default="157,159,161")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    if args.repeat < 1:
        ap.error("--repeat debe ser al menos 1")
    output = args.output or (Path(__file__).resolve().parents[3] / "lab" /
        "calibracion_nueva" /
        f"dos_fases_{args.mode}_{datetime.now():%Y%m%d_%H%M%S}.json")
    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    calibrator = TwoPhaseCalibrator(lab, output, samples=args.samples)
    exit_code = 1
    runs = []
    final_kept = False
    try:
        if args.mode == "characterize-x2":
            idac0_codes = [int(item) for item in args.seed_idac0.split(",")]
            idac1_codes = [int(item) for item in args.seed_idac1.split(",")]
            result = calibrator.characterize_x2_seeds(
                args.seed_repeats, idac0_codes, idac1_codes)
            runs.append({"run": 1, "pass": result["pass"], "result": result})
            exit_code = 0 if result["pass"] else 1
            calibrator.event("campaign_summary",
                             "Caracterización de semillas x2 terminada",
                             runs=runs)
            return exit_code
        if args.mode == "calibrate-gains":
            gains = [int(item.strip()) for item in args.gains.split(",")]
            if any(gain not in GAIN_CODES for gain in gains):
                raise RuntimeError("lista de ganancias no soportada")
            for campaign_index in range(1, args.campaign_repeats + 1):
                calibrator.event("gain_campaign_start",
                                 f"Campaña {campaign_index}/{args.campaign_repeats}",
                                 run=campaign_index)
                result = calibrator.calibrate_gain_campaign(
                    gains, budget_s=args.budget_s)
                runs.append({"run": campaign_index,
                             "pass": result["pass"], "result": result})
            all_campaigns_pass = all(run["pass"] for run in runs)
            if all_campaigns_pass and args.save_eeprom:
                x1 = load_history().get("gains", {}).get("1")
                if x1 is None:
                    raise RuntimeError("no hay punto x1 verificado para EEPROM")
                if not lab.set_gain("pgaout", PGAOUT_X1_CODE):
                    raise RuntimeError("sin ACK al restaurar PGAout x1")
                for stage, code in enumerate(x1["codes"]):
                    calibrator.set_code(stage, int(code))
                final_x1 = [calibrator.read(ch, "EEPROM verificación x1",
                                            min(ch, 3),
                                            calibrator.codes[min(ch, 3)],
                                            require_valid=False)
                            for ch in range(5)]
                if not all(point.valid for point in final_x1):
                    raise RuntimeError("punto x1 histórico no es válido; EEPROM no escrita")
                if not lab.commit_calibration():
                    raise RuntimeError("PSoC rechazó guardado EEPROM")
                calibrator.event("eeprom_saved",
                                 "EEPROM guardada con el punto x1 verificado",
                                 codes=x1["codes"])
            final_kept = all_campaigns_pass
            exit_code = 0 if all_campaigns_pass else 1
            calibrator.event("campaign_summary",
                             f"Campañas ganancias: "
                             f"{sum(run['pass'] for run in runs)}/"
                             f"{args.campaign_repeats} PASS",
                             runs=runs)
            return exit_code
        for run_index in range(1, args.repeat + 1):
            calibrator.event("run_start",
                             f"Corrida {run_index}/{args.repeat} desde cero",
                             run=run_index, repeat=args.repeat)
            try:
                if args.mode == "probe-upstream":
                    result = calibrator.probe_upstream()
                elif args.mode == "calibrate-full":
                    keep = args.keep_on_pass and run_index == args.repeat
                    result = calibrator.calibrate_full(keep_on_pass=keep)
                    final_kept = keep
                else:
                    keep = args.keep_on_pass and run_index == args.repeat
                    result = calibrator.calibrate_upstream(keep_on_pass=keep)
                    final_kept = keep
                item = {"run": run_index, "pass": True, "result": result}
                runs.append(item)
                calibrator.event("run_result",
                                 f"Corrida {run_index}/{args.repeat} finalizada",
                                 **item)
            except Exception as exc:
                item = {"run": run_index, "pass": False,
                        "error": f"{type(exc).__name__}: {exc}"}
                runs.append(item)
                calibrator.event("run_error",
                                 f"Corrida {run_index}/{args.repeat} ABORT: "
                                 f"{item['error']}", **item)
                final_kept = False
                try:
                    calibrator.lab.set_gain("pgaout", PGAOUT_X1_CODE)
                except Exception:
                    pass
                for stage in range(4):
                    try:
                        calibrator.set_code(stage, 0)
                    except Exception:
                        pass
        passed = sum(int(item["pass"]) for item in runs)
        calibrator.event("campaign_summary",
                         f"Campaña terminada: {passed}/{args.repeat} PASS",
                         repeat=args.repeat, passed=passed, failed=args.repeat-passed,
                         runs=runs)
        exit_code = 0 if passed == args.repeat else 1
    finally:
        if exit_code != 0 or not final_kept:
            try:
                calibrator.lab.set_gain("pgaout", PGAOUT_X1_CODE)
            except Exception:
                pass
            for stage in range(4):
                try:
                    calibrator.set_code(stage, 0)
                except Exception:
                    pass
        calibrator.save()
        console.close()
        print(f"Evidencia: {output}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
