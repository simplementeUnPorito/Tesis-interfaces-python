"""Review and export field hammer/geophone captures.

Typical use:
    python review_field_data.py
    python review_field_data.py --scan-only
    python review_field_data.py --export-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from .field_review_data import (
        DEFAULT_RAW_ROOT,
        auto_pick_shot,
        default_average_arrivals_path,
        default_alignment_offsets_path,
        default_alignment_shot_offsets_path,
        default_annotations_path,
        default_disabled_folders_path,
        default_filter_settings_path,
        default_output_dir,
        discover_dataset,
        export_processed,
        load_alignment_offsets,
        load_alignment_shot_offsets,
        load_annotations,
        load_average_arrivals,
        load_disabled_folders,
        load_filter_settings,
        save_annotations,
    )
except ImportError:  # pragma: no cover - script execution from this folder
    from field_review_data import (
        DEFAULT_RAW_ROOT,
        auto_pick_shot,
        default_average_arrivals_path,
        default_alignment_offsets_path,
        default_alignment_shot_offsets_path,
        default_annotations_path,
        default_disabled_folders_path,
        default_filter_settings_path,
        default_output_dir,
        discover_dataset,
        export_processed,
        load_alignment_offsets,
        load_alignment_shot_offsets,
        load_annotations,
        load_average_arrivals,
        load_disabled_folders,
        load_filter_settings,
        save_annotations,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw-root",
        default=str(DEFAULT_RAW_ROOT),
        help=f"Carpeta con los crudos. Default: {DEFAULT_RAW_ROOT}",
    )
    parser.add_argument(
        "--annotations",
        default=None,
        help="JSON de marcas. Default: <raw-root>\\field_review_annotations.json",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Carpeta de exportacion. Default: <raw-root>_procesado",
    )
    parser.add_argument(
        "--include-duplicates",
        action="store_true",
        help="Mostrar/exportar tambien carpetas duplicadas exactas.",
    )
    parser.add_argument(
        "--filtered",
        action="store_true",
        help="Usar filt_f32le.bin. Default: usa raw_f32le.bin.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Usar raw_f32le.bin. Es el default; queda por compatibilidad.",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Solo listar capturas unicas y duplicados; no abre GUI.",
    )
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="Exportar con las marcas existentes; si falta una marca usa pick automatico.",
    )
    return parser.parse_args()


def print_scan(dataset) -> None:
    print(f"Raw root: {dataset.raw_root}")
    print(f"Capturas revisables: {len(dataset.shots)}")
    print(f"Carpetas duplicadas ignoradas: {dataset.duplicate_folder_count}")
    if dataset.skipped_folders:
        print("Carpetas saltadas por error:")
        for name in dataset.skipped_folders:
            print(f"  - {name}")
    if dataset.duplicate_groups:
        print("Grupos duplicados:")
        for group in dataset.duplicate_groups:
            duplicates = ", ".join(group.duplicate_folders)
            print(f"  - queda {group.keep_folder}; duplicadas: {duplicates}")
    print("Primeras capturas:")
    for shot in dataset.shots[:12]:
        print(f"  - {shot.folder_name}/{shot.capture_name} | {shot.distance_m:g} m | fs={shot.fs:g}")


def main() -> int:
    args = parse_args()
    raw_root = Path(args.raw_root).resolve()
    annotations_path = Path(args.annotations).resolve() if args.annotations else default_annotations_path(raw_root)
    output_dir = Path(args.output_dir).resolve() if args.output_dir else default_output_dir(raw_root)
    prefer_filtered = bool(args.filtered and not args.raw)

    dataset = discover_dataset(raw_root, include_duplicates=args.include_duplicates)
    if args.scan_only:
        print_scan(dataset)
        return 0

    annotations = load_annotations(annotations_path)
    if args.export_only:
        for shot in dataset.shots:
            if shot.shot_id not in annotations:
                annotations[shot.shot_id] = auto_pick_shot(shot, prefer_filtered=prefer_filtered)
        average_arrivals = load_average_arrivals(default_average_arrivals_path(output_dir))
        filter_settings = load_filter_settings(default_filter_settings_path(raw_root))
        alignment_offsets = load_alignment_offsets(default_alignment_offsets_path(raw_root))
        alignment_shot_offsets = load_alignment_shot_offsets(default_alignment_shot_offsets_path(raw_root))
        disabled_folders = load_disabled_folders(default_disabled_folders_path(raw_root))
        result = export_processed(
            dataset,
            annotations,
            output_dir,
            prefer_filtered=prefer_filtered,
            average_arrivals=average_arrivals,
            filter_settings=filter_settings,
            alignment_offsets=alignment_offsets,
            alignment_shot_offsets=alignment_shot_offsets,
            disabled_folders=disabled_folders,
        )
        save_annotations(annotations_path, dataset, annotations)
        print(f"Salida: {result.output_dir}")
        print(f"Muestras: {result.sample_count}")
        print(f"Promedios: {result.average_count}")
        print(f"Saltadas: {result.skipped_count}")
        print(f"Manifest: {result.manifest_json}")
        if result.waterfall_png:
            print(f"Waterfall PNG: {result.waterfall_png}")
        if result.waterfall_pdf:
            print(f"Waterfall PDF: {result.waterfall_pdf}")
        return 0

    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    high_dpi_pixmaps = getattr(Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps", None)
    if high_dpi_pixmaps is not None:
        QApplication.setAttribute(high_dpi_pixmaps)
    app = QApplication(sys.argv)
    app.setApplicationName("Revision Canchita")
    app.setOrganizationName("Tesis")

    try:
        from .field_review_app import FieldReviewWindow
    except ImportError:  # pragma: no cover - script execution from this folder
        from field_review_app import FieldReviewWindow

    win = FieldReviewWindow(
        dataset=dataset,
        annotations_path=annotations_path,
        output_dir=output_dir,
        prefer_filtered=prefer_filtered,
    )
    win.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
