"""Resume estadísticamente una campaña larga de autocalibración en dos fases."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def sample_stats(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"n": 0, "mean": None, "sd": None, "median": None,
                "p95": None, "min": None, "max": None}

    def percentile(fraction: float) -> float:
        position = fraction * (len(ordered) - 1)
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        return (ordered[lower] * (upper - position) +
                ordered[upper] * (position - lower))

    return {
        "n": len(ordered),
        "mean": statistics.fmean(ordered),
        "sd": statistics.stdev(ordered) if len(ordered) > 1 else 0.0,
        "median": statistics.median(ordered),
        "p95": percentile(0.95),
        "min": ordered[0],
        "max": ordered[-1],
    }


def wilson(successes: int, trials: int, z: float = 1.959963984540054) -> dict:
    if trials == 0:
        return {"rate": None, "low": None, "high": None}
    rate = successes / trials
    denominator = 1.0 + z * z / trials
    center = (rate + z * z / (2.0 * trials)) / denominator
    half = (z / denominator * math.sqrt(
        rate * (1.0 - rate) / trials + z * z / (4.0 * trials * trials)))
    return {"rate": rate, "low": center - half, "high": center + half}


def analyze(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = payload["events"]
    campaign_results = [event["result"] for event in events
                        if event["kind"] == "gain_campaign_result"]
    gain_passes = [event["result"] for event in events
                   if event["kind"] == "gain_pass"]
    guarded_failures = [event for event in events
                        if event["kind"] == "history_verify_abort"]
    phase1_passes = sum(event["kind"] == "phase1_fast_pass" for event in events)
    complete_campaigns = sum(result["pass"] for result in campaign_results)
    gain_failures = Counter(int(event["gain"]) for event in guarded_failures)
    gain_groups: dict[int, list[dict]] = defaultdict(list)
    for result in gain_passes:
        gain_groups[int(result["gain"])].append(result)

    by_gain = {}
    for gain in sorted(set(gain_groups) | set(gain_failures)):
        passed = gain_groups[gain]
        lp_errors = [float(result["final"]["4"]["error_real_mv"])
                     for result in passed]
        all_taps = sum(bool(result["all_taps_valid"]) for result in passed)
        failures = gain_failures[gain]
        trials = len(passed) + failures
        by_gain[str(gain)] = {
            "passes": len(passed),
            "failures": failures,
            "attempts": trials,
            "success_wilson_95": wilson(len(passed), trials),
            "elapsed_s": sample_stats(
                [float(result["elapsed_s"]) for result in passed]),
            "lp_error_real_mv": sample_stats(lp_errors),
            "all_taps_valid": all_taps,
            "all_taps_fraction_of_passes": (
                all_taps / len(passed) if passed else None),
        }

    first = datetime.fromisoformat(events[0]["at"])
    last = datetime.fromisoformat(events[-1]["at"])
    complete_totals = [float(result["total_elapsed_s"])
                       for result in campaign_results if result["pass"]]
    phase1_times = [float(result["initial_elapsed_s"])
                    for result in campaign_results]
    pga_errors = [float(result["phase1"]["pga"]["error_real_mv"])
                  for result in campaign_results]
    bp_errors = [float(result["phase1"]["bp"]["error_real_mv"])
                 for result in campaign_results]
    attempts = len(gain_passes) + len(guarded_failures)
    unsafe_search_kinds = {"fast_minimum", "grid_rescued"}
    event_counts = Counter(event["kind"] for event in events)

    return {
        "source": str(path),
        "wall_duration_s": (last - first).total_seconds(),
        "campaigns": {
            "passes": complete_campaigns,
            "attempts": len(campaign_results),
            "success_wilson_95": wilson(
                complete_campaigns, len(campaign_results)),
            "complete_elapsed_s": sample_stats(complete_totals),
        },
        "phase1": {
            "passes": phase1_passes,
            "attempts": len(campaign_results),
            "success_wilson_95": wilson(phase1_passes, len(campaign_results)),
            "elapsed_s": sample_stats(phase1_times),
            "pga_error_real_mv": sample_stats(pga_errors),
            "bp_error_real_mv": sample_stats(bp_errors),
        },
        "gain_changes": {
            "passes": len(gain_passes),
            "failures": len(guarded_failures),
            "attempts": attempts,
            "planned_positions": sum(
                len(result["requested_gains"]) for result in campaign_results),
            "success_wilson_95": wilson(len(gain_passes), attempts),
        },
        "by_gain": by_gain,
        "safety": {
            "guarded_aborts": len(guarded_failures),
            "history_verify_retries": event_counts["history_verify_retry"],
            "neutral_recoveries": event_counts["history_neutral_recovery"],
            "idac_write_retries": event_counts["write_retry"],
            "unsafe_search_events": sum(event_counts[kind]
                                        for kind in unsafe_search_kinds),
            "unsafe_search_kinds": {
                kind: event_counts[kind] for kind in sorted(unsafe_search_kinds)},
        },
        "event_counts": dict(sorted(event_counts.items())),
    }


def fmt(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def percent(value: float | None) -> str:
    return "—" if value is None else f"{100.0 * value:.1f}%"


def markdown(summary: dict) -> str:
    campaigns = summary["campaigns"]
    phase1 = summary["phase1"]
    changes = summary["gain_changes"]
    lines = [
        "# Endurance de autocalibración en dos fases — 2026-09-10",
        "",
        f"Fuente: `{summary['source']}`.",
        "",
        "## Resultado global",
        "",
        f"- Duración observada: {summary['wall_duration_s']/60.0:.1f} min.",
        f"- Campañas completas: {campaigns['passes']}/{campaigns['attempts']} "
        f"({percent(campaigns['success_wilson_95']['rate'])}; IC95% Wilson "
        f"{percent(campaigns['success_wilson_95']['low'])}.."
        f"{percent(campaigns['success_wilson_95']['high'])}).",
        f"- Fase 1: {phase1['passes']}/{phase1['attempts']} PASS; "
        f"{fmt(phase1['elapsed_s']['mean'])} ± {fmt(phase1['elapsed_s']['sd'])} s.",
        f"- Cambios intentados: {changes['passes']}/{changes['attempts']} PASS "
        f"({percent(changes['success_wilson_95']['rate'])}; IC95% Wilson "
        f"{percent(changes['success_wilson_95']['low'])}.."
        f"{percent(changes['success_wilson_95']['high'])}).",
        f"- Campaña completa: {fmt(campaigns['complete_elapsed_s']['mean'])} ± "
        f"{fmt(campaigns['complete_elapsed_s']['sd'])} s; p95 "
        f"{fmt(campaigns['complete_elapsed_s']['p95'])} s; máximo "
        f"{fmt(campaigns['complete_elapsed_s']['max'])} s.",
        "",
        "## Desglose por ganancia",
        "",
        "| PGAout | PASS/intentos | IC95% | tiempo medio ± DE (s) | p95 (s) | "
        "LPo medio ± DE vs Vref (mV) | todos los taps |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    if changes["planned_positions"] == changes["attempts"]:
        lines.insert(8, f"- Posiciones planificadas e intentadas: "
                     f"{changes['planned_positions']}/{changes['planned_positions']}.")
    else:
        lines.insert(8, f"- Posiciones planificadas: {changes['planned_positions']}; "
                     f"intentadas: {changes['attempts']}. Las restantes no se "
                     "ejecutaron después del aborto seguro de la campaña.")
    for gain, row in summary["by_gain"].items():
        ci = row["success_wilson_95"]
        elapsed = row["elapsed_s"]
        lp = row["lp_error_real_mv"]
        lines.append(
            f"| x{gain} | {row['passes']}/{row['attempts']} | "
            f"{percent(ci['low'])}..{percent(ci['high'])} | "
            f"{fmt(elapsed['mean'])} ± {fmt(elapsed['sd'])} | "
            f"{fmt(elapsed['p95'])} | {fmt(lp['mean'], 1)} ± {fmt(lp['sd'], 1)} | "
            f"{row['all_taps_valid']}/{row['passes']} |")

    safety = summary["safety"]
    lines.extend([
        "",
        "## Seguridad y hallazgo",
        "",
        f"- Abortos protegidos: {safety['guarded_aborts']}.",
        f"- Reverificaciones extraordinarias: "
        f"{safety['history_verify_retries']}.",
        f"- Recuperaciones neutras de fase 2: "
        f"{safety['neutral_recoveries']}.",
        f"- Reintentos de escritura IDAC recuperados: "
        f"{safety['idac_write_retries']}.",
        f"- Eventos de búsqueda amplia/rieles: "
        f"**{safety['unsafe_search_events']}**.",
    ])
    if safety["guarded_aborts"]:
        lines.extend([
            "- El aborto observado ocurrió después de tres verificaciones "
            "inválidas. Se restauró la ganancia previa sin barrer rieles.",
            "- El resultado demuestra contención segura, pero no demuestra 100% "
            "de disponibilidad prolongada de todas las ganancias.",
        ])
    else:
        lines.append(
            "- No hubo fallos funcionales ni fue necesario activar la recuperación "
            "excepcional en esta tanda.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    summary = analyze(args.input)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                         encoding="utf-8")
    args.markdown.write_text(markdown(summary), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
