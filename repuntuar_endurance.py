"""Re-puntúa campañas de cambio de ganancia con el criterio vigente."""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from autocalibracion_dos_fases import (
    CODIGO_SIN_AUTORIDAD,
    LPO_OBJETIVO_INFORMATIVO_MV,
    MARGEN_RIEL_MV,
    TARGET_UV,
    evaluar_aceptacion,
    toca_riel,
)
from escala_banco import (
    BANCO_MAX_VALIDO_MV,
    BANCO_MIN_VALIDO_MV,
    BANCO_RIEL_ALTO_MEDIDO_MV,
    FACTOR,
    PP_CLAVADO_UV,
    clavado,
)


ROOT = Path(__file__).resolve().parents[3]
LAB = ROOT / "lab" / "calibracion_nueva"
OUTPUT = ROOT / "lab" / "REPUNTUACION_ENDURANCE_2026-09-10.md"
RECOVERY = LAB / "dos_fases_endurance_recovery_10x_20260910.json"
EVENT_KINDS = {"gain_pass", "gain_fail"}
REASON_ORDER = (
    "media toca riel",
    "sólo excursión media +/- pp/2",
    "clavado",
    "sin autoridad",
)


@dataclass
class ScoredEvent:
    source: str
    campaign: int
    event_index: int
    at: str
    gain: int
    old_pass: bool
    new_pass: bool
    final: dict[int, dict]
    acceptance: dict
    mean_rail_channels: tuple[int, ...]
    excursion_only_channels: tuple[int, ...]
    clamped_channels: tuple[int, ...]
    no_authority_stages: tuple[int, ...]
    dominant_reason: str | None
    reasons: tuple[str, ...]


def decimal(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def discover_inputs() -> list[Path]:
    files = {RECOVERY}
    files.update(LAB.glob("dos_fases_endurance_10x_*.json"))
    files.update(LAB.glob("dos_fases_calibrate-gains_*.json"))
    return sorted(path for path in files if path.is_file())


def normalize_final(raw: object) -> dict[int, dict]:
    if not isinstance(raw, dict):
        raise ValueError("result.final no es un diccionario")
    final = {}
    required = {"mean_bank_uv", "pp_bank_uv", "valid"}
    for channel in range(5):
        point = raw.get(str(channel), raw.get(channel))
        if not isinstance(point, dict):
            raise ValueError(f"falta result.final[{channel}]")
        missing = required - point.keys()
        if missing:
            fields = ", ".join(sorted(missing))
            raise ValueError(f"result.final[{channel}] no tiene {fields}")
        final[channel] = point
    return final


def rail_breakdown(final: dict[int, dict]) -> tuple[tuple[int, ...],
                                                    tuple[int, ...],
                                                    tuple[int, ...]]:
    """Desglosa por qué ``toca_riel`` dispara, sin crear otro criterio.

    La comparación de la media sirve sólo como contrafactual para el informe;
    la decisión real siempre se toma mediante ``toca_riel``.
    """
    margin_bank_uv = MARGEN_RIEL_MV * 1000.0 / FACTOR
    low_uv = BANCO_MIN_VALIDO_MV * 1000.0 + margin_bank_uv
    high_uv = (min(BANCO_MAX_VALIDO_MV, BANCO_RIEL_ALTO_MEDIDO_MV) * 1000.0 -
               margin_bank_uv)
    mean_channels = []
    excursion_only_channels = []
    clamped_channels = []
    for channel, point in final.items():
        mean_uv = float(point["mean_bank_uv"])
        pp_uv = float(point["pp_bank_uv"])
        mean_touches = mean_uv <= low_uv or mean_uv >= high_uv
        is_clamped = clavado(pp_uv)
        full_touches = toca_riel(mean_uv, pp_uv, MARGEN_RIEL_MV)
        if mean_touches:
            mean_channels.append(channel)
        if is_clamped:
            clamped_channels.append(channel)
        if full_touches and not mean_touches and not is_clamped:
            excursion_only_channels.append(channel)
    return (tuple(mean_channels), tuple(excursion_only_channels),
            tuple(clamped_channels))


def classify(final: dict[int, dict], codes: list[int]) -> tuple:
    mean_channels, excursion_channels, clamped_channels = rail_breakdown(final)
    no_authority = tuple(stage for stage, code in enumerate(codes[:4])
                         if abs(int(code)) >= CODIGO_SIN_AUTORIDAD)
    reasons = []
    if mean_channels:
        reasons.append("media toca riel")
    if excursion_channels:
        reasons.append("sólo excursión media +/- pp/2")
    if clamped_channels:
        reasons.append("clavado")
    if no_authority:
        reasons.append("sin autoridad")
    dominant = next((reason for reason in REASON_ORDER if reason in reasons), None)
    return (mean_channels, excursion_channels, clamped_channels, no_authority,
            dominant, tuple(reasons))


def score_file(path: Path) -> tuple[list[ScoredEvent], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [], f"omitido: {type(exc).__name__}: {exc}"
    events = payload.get("events")
    if not isinstance(events, list):
        return [], "omitido: no tiene una lista events"

    candidates = [(index, event) for index, event in enumerate(events)
                  if isinstance(event, dict) and event.get("kind") in EVENT_KINDS]
    if not candidates:
        return [], "sin eventos gain_pass/gain_fail"

    # Se omite el archivo entero si algún cambio no aporta todos los campos.
    normalized: dict[int, tuple[dict, dict[int, dict], list[int], int]] = {}
    try:
        for index, event in candidates:
            result = event.get("result")
            if not isinstance(result, dict):
                raise ValueError(f"evento {index}: falta result")
            final = normalize_final(result.get("final"))
            codes = result.get("codes")
            if (not isinstance(codes, list) or len(codes) < 4 or
                    any(not isinstance(code, int) for code in codes[:4])):
                raise ValueError(f"evento {index}: faltan cuatro códigos IDAC")
            gain = result.get("gain")
            if not isinstance(gain, int):
                raise ValueError(f"evento {index}: falta gain entero")
            normalized[index] = (event, final, codes, gain)
    except (TypeError, ValueError) as exc:
        return [], f"omitido: {exc}"

    scores = []
    campaign = 1
    seen_campaign_start = False
    for index, event in enumerate(events):
        if isinstance(event, dict) and event.get("kind") == "gain_campaign_start":
            if seen_campaign_start:
                campaign += 1
            else:
                campaign = int(event.get("run", 1))
                seen_campaign_start = True
        if index not in normalized:
            continue
        event, final, codes, gain = normalized[index]
        old_pass = all(bool(final[channel]["valid"])
                       for channel in (0, 1, 4))
        acceptance = evaluar_aceptacion(final, codes)
        classified = classify(final, codes)
        scores.append(ScoredEvent(
            source=path.name,
            campaign=campaign,
            event_index=index,
            at=str(event.get("at", "sin fecha")),
            gain=gain,
            old_pass=old_pass,
            new_pass=bool(acceptance["pass"]),
            final=final,
            acceptance=acceptance,
            mean_rail_channels=classified[0],
            excursion_only_channels=classified[1],
            clamped_channels=classified[2],
            no_authority_stages=classified[3],
            dominant_reason=classified[4],
            reasons=classified[5],
        ))
    return scores, "procesado"


def fallen(records: list[ScoredEvent]) -> list[ScoredEvent]:
    return [record for record in records
            if record.old_pass and not record.new_pass]


def breakdown_counts(records: list[ScoredEvent]) -> dict[str, int]:
    failures = fallen(records)
    return {
        "mean": sum(bool(record.mean_rail_channels) for record in failures),
        "excursion_only": sum(
            bool(record.excursion_only_channels) and
            not record.mean_rail_channels and
            not record.clamped_channels and
            not record.no_authority_stages
            for record in failures),
        "clamped": sum(bool(record.clamped_channels) for record in failures),
        "authority": sum(bool(record.no_authority_stages) for record in failures),
    }


def reason_counts(records: list[ScoredEvent]) -> Counter:
    return Counter(record.dominant_reason for record in fallen(records))


def append_breakdown(lines: list[str], records: list[ScoredEvent]) -> None:
    counts = breakdown_counts(records)
    excursion_tap_events = sum(
        bool(record.excursion_only_channels) for record in fallen(records))
    excursion_taps = sum(
        len(record.excursion_only_channels) for record in fallen(records))
    lines.extend([
        "",
        "| Desglose de las caídas | Cambios |",
        "|---|---:|",
        f"| La media ya toca riel | {counts['mean']} |",
        f"| Caen sólo al considerar `media +/- pp/2` | {counts['excursion_only']} |",
        f"| Presentan al menos un tap clavado | {counts['clamped']} |",
        f"| Sin autoridad IDAC | {counts['authority']} |",
        "",
        ("`Clavado` es una firma adicional y puede coincidir con una media en "
         "riel; por eso esa fila no se suma a las dos anteriores."),
    ])
    if excursion_taps:
        tap_word = "tap" if excursion_taps == 1 else "taps"
        change_word = "cambio" if excursion_tap_events == 1 else "cambios"
        lines.extend([
            "",
            (f"La excursión descubre además {excursion_taps} {tap_word} en "
             f"{excursion_tap_events} {change_word} que ya caían porque otro tap "
             "tenía la media en riel; por eso no agrega una caída completa."),
        ])


def render(records: list[ScoredEvent], statuses: dict[str, str]) -> str:
    old_pass = [record for record in records if record.old_pass]
    survivors = [record for record in old_pass if record.new_pass]
    lines = [
        "# Re-puntuación de endurance — 2026-09-10",
        "",
        "## Criterio aplicado",
        "",
        (f"Se recalcularon los eventos `gain_pass` y `gain_fail` desde "
         f"`result.final`. El único criterio de señal es que ninguno de los "
         f"cinco taps toque un riel: se compara `mean_bank_uv +/- "
         f"pp_bank_uv/2` con la ventana de `escala_banco`, usando como límite "
         f"alto efectivo el riel medido de "
         f"{decimal(BANCO_RIEL_ALTO_MEDIDO_MV)} mV banco por ser más "
         f"restrictivo, y un margen de {decimal(MARGEN_RIEL_MV)} mV físicos. "
         f"Un tap con `pp_bank_uv < {decimal(PP_CLAVADO_UV, 0)}` también cuenta "
         f"como riel. Un IDAC queda sin autoridad con |código| >= "
         f"{CODIGO_SIN_AUTORIDAD}."),
        "",
        (f"LPo se minimiza hacia {decimal(LPO_OBJETIVO_INFORMATIVO_MV)} mV de "
         "error, pero su error no es una compuerta de aceptación."),
        "",
        "El PASS anterior se reconstruyó como `valid` en ch0, ch1 y ch4.",
        "",
        "## Resultado global",
        "",
        "| Cambios registrados | PASS anterior | Sobreviven | Caen |",
        "|---:|---:|---:|---:|",
        f"| {len(records)} | {len(old_pass)} | {len(survivors)} | "
        f"{len(old_pass)-len(survivors)} |",
    ]
    append_breakdown(lines, records)

    endurance = [record for record in records if record.source == RECOVERY.name]
    if endurance:
        endurance_old = [record for record in endurance if record.old_pass]
        endurance_survivors = [record for record in endurance_old
                               if record.new_pass]
        lines.extend([
            "",
            "### Endurance recovery 10x",
            "",
            (f"En `{RECOVERY.name}` hay {len(endurance)} cambios, "
             f"{len(endurance_old)} PASS anteriores y "
             f"{len(endurance_survivors)} que sobreviven. Caen "
             f"{len(endurance_old)-len(endurance_survivors)}."),
        ])
        append_breakdown(lines, endurance)

    lines.extend([
        "",
        "## Error de LPo logrado por ganancia (informativo)",
        "",
        "El error de LPo se informa siempre, pero no cambia PASS/FAIL.",
        "",
        "| Ganancia | Registrados | PASS anterior | Sobreviven | Caen | Error LPo mediano (mV físicos) | Rango de error LPo (mV físicos) |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ])
    by_gain = defaultdict(list)
    for record in records:
        by_gain[record.gain].append(record)
    for gain in sorted(by_gain):
        group = by_gain[gain]
        group_old = [record for record in group if record.old_pass]
        group_survivors = [record for record in group_old if record.new_pass]
        errors = [((float(record.final[4]["mean_bank_uv"]) - TARGET_UV) *
                   FACTOR / 1000.0 - LPO_OBJETIVO_INFORMATIVO_MV)
                  for record in group_old]
        median_text = decimal(statistics.median(errors)) if errors else "—"
        range_text = (f"{decimal(min(errors))} a {decimal(max(errors))}"
                      if errors else "—")
        lines.append(
            f"| x{gain} | {len(group)} | {len(group_old)} | "
            f"{len(group_survivors)} | {len(group_old)-len(group_survivors)} | "
            f"{median_text} | {range_text} |")

    lines.extend([
        "",
        "## Resultado por campaña",
        "",
        "| JSON | Campaña | Registrados | PASS anterior | Sobreviven | Motivo dominante de las caídas |",
        "|---|---:|---:|---:|---:|---|",
    ])
    by_campaign = defaultdict(list)
    for record in records:
        by_campaign[(record.source, record.campaign)].append(record)
    for (source, campaign), group in sorted(by_campaign.items()):
        group_old = [record for record in group if record.old_pass]
        group_survivors = [record for record in group_old if record.new_pass]
        counts = reason_counts(group)
        reasons = "; ".join(f"{reason}: {counts[reason]}"
                            for reason in REASON_ORDER
                            if counts[reason]) or "—"
        lines.append(
            f"| `{source}` | {campaign} | {len(group)} | {len(group_old)} | "
            f"{len(group_survivors)} | {reasons} |")

    lines.extend([
        "",
        "## Archivos",
        "",
        "| JSON | Estado |",
        "|---|---|",
    ])
    for source, status in statuses.items():
        lines.append(f"| `{source}` | {status} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    inputs = discover_inputs()
    records = []
    statuses = {}
    for path in inputs:
        scored, status = score_file(path)
        records.extend(scored)
        suffix = "cambio" if len(scored) == 1 else "cambios"
        statuses[path.name] = (f"{status}; {len(scored)} {suffix}"
                               if status == "procesado" else status)
    OUTPUT.write_text(render(records, statuses), encoding="utf-8")
    old_pass = sum(record.old_pass for record in records)
    survivors = sum(record.old_pass and record.new_pass for record in records)
    counts = breakdown_counts(records)
    print(
        f"PASS anterior: {old_pass}; sobreviven: {survivors}; "
        f"caen por media: {counts['mean']}; "
        f"sólo por excursión: {counts['excursion_only']}; "
        f"por clavado: {counts['clamped']}; informe: {OUTPUT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
