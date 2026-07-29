"""Exportaciones reproducibles ejecutadas por la cola científica."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import numpy as np

from ._gs import frd
from .averages import arrivals_path
from .datacache import get_dataset
from .groups import filtered_dataset, load_grouping, project_disabled_for_group
from .waterfall import load_view, matrix_for_masw


def run_processed_export(payload: dict, artifact_dir: Path) -> dict:
    raw_root = Path(payload["raw_root"]).resolve()
    group_id = max(1, int(payload.get("group_id", 1) or 1))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    output = artifact_dir / "processed"

    group_count, assignments = load_grouping(raw_root)
    group_id = min(group_id, max(1, group_count))
    dataset = filtered_dataset(
        get_dataset(raw_root), group_id, group_count, assignments
    )
    annotations = frd.load_annotations(frd.default_annotations_path(raw_root))
    settings = frd.load_filter_settings(frd.default_filter_settings_path(raw_root))
    disabled = project_disabled_for_group(
        frd.load_disabled_folders(frd.default_disabled_folders_path(raw_root)),
        group_id,
        group_count,
    )
    result = frd.export_processed(
        dataset,
        annotations,
        output,
        prefer_filtered=bool(payload.get("prefer_filtered", False)),
        average_arrivals=frd.load_average_arrivals(arrivals_path(raw_root)),
        filter_settings=settings if settings.enabled else None,
        alignment_offsets=frd.load_alignment_offsets(
            frd.default_alignment_offsets_path(raw_root)
        ),
        alignment_shot_offsets=frd.load_alignment_shot_offsets(
            frd.default_alignment_shot_offsets_path(raw_root)
        ),
        disabled_folders=disabled,
    )
    archive_base = artifact_dir / f"processed_{artifact_dir.name}"
    archive = Path(
        shutil.make_archive(str(archive_base), "zip", root_dir=str(output))
    )
    return {
        "group_id": group_id,
        "sample_count": result.sample_count,
        "average_count": result.average_count,
        "skipped_count": result.skipped_count,
        "artifacts": [
            {
                "id": archive.name,
                "name": archive.name,
                "media_type": "application/zip",
            }
        ],
    }


def run_waterfall_export(payload: dict, artifact_dir: Path) -> dict:
    """Exporta la vista científica actual como CSV y figuras PNG/PDF.

    El CSV contiene amplitudes reales (no las coordenadas escaladas del
    Canvas). Las figuras sí respetan la opción visual ``wiggle``; esto no
    modifica ni reescribe ninguna señal persistida.
    """
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    raw_root = Path(payload["raw_root"]).resolve()
    group_id = max(1, int(payload.get("group_id", 1) or 1))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    time_s, distances, matrix = matrix_for_masw(raw_root, group_id=group_id)
    time_s = np.asarray(time_s, dtype=np.float64)
    matrix = np.asarray(matrix, dtype=np.float64)
    distances_arr = np.asarray(distances, dtype=np.float64)
    view = load_view(raw_root, group_id)
    suffix = artifact_dir.name

    csv_path = artifact_dir / f"waterfall_{suffix}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["time_s"] + [f"distance_{distance:g}_m" for distance in distances]
        )
        for sample_idx, sample_time in enumerate(time_s):
            writer.writerow(
                [f"{sample_time:.12g}"]
                + [
                    "" if not np.isfinite(value) else f"{float(value):.12g}"
                    for value in matrix[:, sample_idx]
                ]
            )

    metadata = {
        "schema": 1,
        "group_id": group_id,
        "view": view,
        "samples": int(time_s.size),
        "distances_m": [float(value) for value in distances],
        "time_start_s": float(time_s[0]),
        "time_end_s": float(time_s[-1]),
        "columns": ["time_s"]
        + [f"distance_{distance:g}_m" for distance in distances],
    }
    metadata_path = artifact_dir / f"waterfall_{suffix}.json"
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    figure, axis = plt.subplots(figsize=(11.7, 7.2), constrained_layout=True)
    if bool(view.get("wiggle")):
        spacing = (
            float(np.median(np.diff(np.sort(distances_arr))))
            if distances_arr.size > 1
            else 1.0
        )
        spacing = max(abs(spacing), 1.0)
        if bool(view.get("raw_amplitude")):
            finite = np.abs(matrix[np.isfinite(matrix)])
            common_peak = float(np.max(finite)) if finite.size else 1.0
        else:
            common_peak = 0.0
        for distance, signal in zip(distances_arr, matrix):
            finite = np.abs(signal[np.isfinite(signal)])
            peak = common_peak or (float(np.max(finite)) if finite.size else 1.0)
            peak = peak or 1.0
            curve = distance + np.nan_to_num(signal / peak) * spacing * 0.4
            axis.plot(time_s, curve, color="#183153", linewidth=0.75)
            axis.fill_between(
                time_s,
                distance,
                curve,
                where=curve >= distance,
                color="#2f80ed",
                alpha=0.42,
                interpolate=True,
            )
    elif distances_arr.size > 1:
        image = axis.pcolormesh(
            time_s,
            distances_arr,
            matrix,
            shading="nearest",
            cmap="seismic",
        )
        figure.colorbar(image, ax=axis, label="Amplitud")
    else:
        extent = [
            float(time_s[0]),
            float(time_s[-1]),
            float(distances_arr[0]) - 0.5,
            float(distances_arr[0]) + 0.5,
        ]
        image = axis.imshow(
            matrix,
            extent=extent,
            aspect="auto",
            origin="lower",
            cmap="seismic",
        )
        figure.colorbar(image, ax=axis, label="Amplitud")
    axis.set(
        title=f"Waterfall · Grupo {group_id}",
        xlabel="Tiempo relativo al trigger (s)",
        ylabel="Distancia (m)",
    )
    axis.grid(bool(view.get("wiggle")), alpha=0.18)

    png_path = artifact_dir / f"waterfall_{suffix}.png"
    pdf_path = artifact_dir / f"waterfall_{suffix}.pdf"
    figure.savefig(png_path, dpi=180)
    figure.savefig(pdf_path)
    plt.close(figure)

    artifacts = [
        (csv_path, "text/csv"),
        (png_path, "image/png"),
        (pdf_path, "application/pdf"),
        (metadata_path, "application/json"),
    ]
    return {
        "group_id": group_id,
        "samples": int(time_s.size),
        "trace_count": int(len(distances)),
        "artifacts": [
            {"id": path.name, "name": path.name, "media_type": media_type}
            for path, media_type in artifacts
        ],
    }
