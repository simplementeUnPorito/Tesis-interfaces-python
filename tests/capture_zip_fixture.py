"""Genera un ZIP determinista con el contrato actual de ``export.js``."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import numpy as np


FS = 1000.0
SAMPLES_PER_BATCH = 30
SAMPLE_COUNT = 240


def _raw_signal(index: int, lost_batches: list[int]) -> np.ndarray:
    time_s = np.arange(SAMPLE_COUNT, dtype=np.float64) / FS
    signal = (
        0.18
        + 0.04 * np.sin(2.0 * np.pi * (12.0 + index) * time_s)
        + 0.02 * np.sin(2.0 * np.pi * 50.0 * time_s)
    ).astype(np.float32)
    signal[120 + index] += np.float32(0.7 if index == 3 else 0.25)
    for batch in lost_batches:
        start = batch * SAMPLES_PER_BATCH
        signal[start : start + SAMPLES_PER_BATCH] = np.nan
    return signal


def _marks_csv(marks: list[tuple[int, int]]) -> str:
    names = {
        0x01: "SD_IO",
        0x02: "SD_SYNC",
        0x04: "RADIO_TX",
        0x08: "I2C",
        0x10: "AP_BEACON",
        0x20: "MASTER_RX",
    }
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["sample_index", "flags", "flags_hex", "bit_names"])
    for sample, flags in marks:
        bit_names = "|".join(name for bit, name in names.items() if flags & bit)
        writer.writerow([sample, flags, f"0x{flags:02X}", bit_names])
    return out.getvalue()


def _sync_node(index: int, declared_m: float) -> dict:
    raw_m = declared_m * 3.2 if declared_m else 0.9
    return {
        "ftm_status": "ok",
        "ftm_sessions": 4,
        "ftm_frames_total": 32,
        "ftm_frames_valid": 29,
        "ftm_window": 16,
        "rtt_median_ps": raw_m * 2.0 / 299792458e-12,
        "rtt_mad_ps": 130.0,
        "rtt_p95_ps": 410.0,
        "rssi_median_dbm": -51,
        "channel": 6,
        "clock_skew_ppb": 18.0,
        "clock_residual_std_ns": 2.3,
        "ftm_age_ms": 12,
        "distance_declared_m": declared_m,
        "distance_ftm_raw_m": raw_m,
        "distance_ftm_sdk_m": raw_m,
        "distance_ftm_calibrated_m": declared_m,
        "distance_pair": f"S{index}-maestro",
        "calibration": {
            "scale": 0.3125,
            "offset_m": 0.0,
            "n_points": 3,
            "n_distances": 3,
            "model": "scale_only",
            "max_abs_residual_m": 0.03,
            "points": [[3.2, 1.0], [6.4, 2.0], [9.6, 3.0]],
        },
        "sample_timestamp_method": "scheduled_index",
    }


def _node(
    index: int,
    role: str,
    node_type: str,
    position_m: float,
    lost_batches: list[int],
    marks: list[tuple[int, int]],
) -> tuple[dict, np.ndarray, str]:
    pcb_id = f"S{index}"
    directory = f"{node_type.lower()}_{pcb_id.lower()}"
    raw_file = f"{directory}/raw_f32le.bin"
    marks_file = f"{directory}/noise_marks.csv"
    node = {
        "index": index,
        "node_id": index,
        "pcb_id": pcb_id,
        "physical_id": pcb_id,
        "name": f"Esclavo {index}",
        "type": node_type,
        "role": role,
        "geo_number": index if role == "geo" else None,
        "slave_id": pcb_id,
        "fs": FS,
        "fs_known": True,
        "fs_exact_known": True,
        "connected": True,
        "data_dir": directory,
        "raw_file": raw_file,
        "filt_file": None,
        "raw_csv_file": None,
        "filt_csv_file": None,
        "noise_marks_file": marks_file,
        "raw_count": SAMPLE_COUNT,
        "filt_count": 0,
        "batch_count": SAMPLE_COUNT // SAMPLES_PER_BATCH,
        "total_samples": SAMPLE_COUNT,
        "capture_truncated": False,
        "dropped_samples": len(lost_batches) * SAMPLES_PER_BATCH,
        "lost_batches": lost_batches,
        "sync": _sync_node(index, position_m),
        "filter_scope": "full",
        "filter_start_index": 0,
        "pga_code": 2,
        "pga_gain": 4,
        "adc_config": 1,
        "adc_range_v": 2.5,
        "adc_counts_per_volt": 131072 / 2.5,
        "hammer_offset_m": position_m,
        "offset_m_from_hammer": position_m,
        "position_m": position_m,
        "sample_offset": 0,
        "visible": True,
        "fir_cmd": "numtaps 31 bp 5 120" if role == "geo" else "numtaps 21 hp 5",
        "dc_remove": True,
        "notch_enabled": True,
        "notch_harm": 3,
        "clean_i2c": True,
        "clean_sd": True,
        "clean_radio_tx": True,
        "clean_beacon": True,
        "noise_signatures_placa": "fixture-board",
        "noise_signatures_fecha": "2026-10-25",
        "invert_signal": False,
        "units": "V",
        "dtype": "float32",
        "endian": "little",
        "sd_marks": [[sample, flags] for sample, flags in marks],
        "hw_class": 1 if role == "hammer" else 0,
        "hw_type": role.upper(),
    }
    return node, _raw_signal(index, lost_batches), _marks_csv(marks)


def write_capture_zip(path: str | Path) -> Path:
    """Escribe dos GEO y un HAMMER sin señales filtradas ni CSV de señal."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    specs = [
        (3, "hammer", "Hammer", 0.0, [1], [(45, 0x04), (150, 0x20)]),
        (1, "geo", "Geo1", 2.0, [2], [(42, 0x08), (151, 0x03)]),
        (2, "geo", "Geo2", 4.0, [5], [(44, 0x10), (152, 0x24)]),
    ]
    nodes = []
    payloads: list[tuple[str, bytes | str]] = []
    for spec in specs:
        node, raw, marks_csv = _node(*spec)
        nodes.append(node)
        payloads.append((node["raw_file"], raw.astype("<f4").tobytes()))
        payloads.append((node["noise_marks_file"], marks_csv))

    sync = {
        "sync_mode": "ftm_active",
        "sync_mode_code": 0,
        "sync_schema": "geophone_sync_v1",
        "started_at": "2026-10-25T12:00:00.000Z",
        "start_method": "broadcast_start",
        "note": "START por broadcast ESP-NOW; fixture de prueba.",
    }
    master_node = {
        "index": 0,
        "node_id": 0,
        "pcb_id": "M",
        "physical_id": "M",
        "name": "Maestro",
        "type": "Maestro",
        "role": "unknown",
        "geo_number": None,
        "slave_id": "M",
        "fs": FS,
        "fs_known": True,
        "connected": False,
        "data_dir": None,
        "raw_file": None,
        "filt_file": None,
        "raw_csv_file": None,
        "filt_csv_file": None,
        "noise_marks_file": None,
        "raw_count": 0,
        "filt_count": 0,
        "lost_batches": [],
        "sync": None,
        "visible": False,
        "units": "V",
        "dtype": "float32",
        "endian": "little",
    }
    metadata = {
        "schema": "geophone_scope_web_zip_v4",
        "payload": "raw_metadata_only",
        "payload_note": "Solo crudo, marcas y metadatos: el filtrado y la limpieza se rehacen con la configuracion de cada nodo.",
        "schema_target": "geophone_scope_mat_node_prefix_v1",
        "created_at": "2026-10-25T12:00:00.000Z",
        "save_time": "20261025_120000",
        "source": "esp32_master_web_ui",
        "web_app_version": "field-study-42",
        "layout": "maestro_metadata_plus_capture_metadata_and_pcb_dirs",
        "id_semantics": {
            "hammer_origin_m": 0,
            "position_m": "Distance from Hammer/source origin in meters. Hammer is always 0.",
            "geo_number": "Assigned by position_m ascending among GEO receivers at capture/export time.",
            "pcb_id": "Physical PCB/slave identifier (S1, S2, ...); use only to identify the hardware board used.",
            "slave_id": "Compatibility alias for pcb_id.",
        },
        "fs": FS,
        "n_slaves": 3,
        "n_batches": SAMPLE_COUNT // SAMPLES_PER_BATCH,
        "samples_per_batch": SAMPLES_PER_BATCH,
        "signal_units": "V",
        "adc_counts_per_volt": 131072 / 2.5,
        "adc_configs": [{"code": 1, "label": "+/-2.5 V", "rangeV": 2.5, "countsPerVolt": 131072 / 2.5}],
        "vdac_step_volts": 0.004,
        "notch_f0": 50.0,
        "display_secs": 1,
        "max_buf_secs": 10,
        "visible_nodes": [3, 1, 2],
        "sync": sync,
        "exported_nodes": [3, 1, 2],
        "exported_pcb_ids": ["S3", "S1", "S2"],
        "capture_count": 1,
        "preserved_count": 0,
        "truncated": False,
        "combined_csv_file": None,
        "capture_summary_csv_file": None,
        "node_summary_csv_file": "combined/nodes.csv",
        "capture_node_summary_csv_file": None,
        "metadata_files": ["metadata.json", "maestro/metadata.json", "maestro/config.json", "combined/nodes.csv"],
        "captures": [],
        "dtype": "float32",
        "endian": "little",
        "nodes": [master_node, *nodes],
    }
    master_config = {
        "created_at": metadata["created_at"],
        "save_time": metadata["save_time"],
        "fs": FS,
        "n_slaves": 3,
        "n_batches": metadata["n_batches"],
        "samples_per_batch": SAMPLES_PER_BATCH,
        "active_slave_indices": [3, 1, 2],
        "exported_slave_indices": [3, 1, 2],
        "exported_pcb_ids": ["S3", "S1", "S2"],
        "capture_count": 1,
        "preserved_count": 0,
        "combined_csv_file": None,
        "capture_summary_csv_file": None,
        "node_summary_csv_file": "combined/nodes.csv",
        "capture_node_summary_csv_file": None,
        "id_semantics": metadata["id_semantics"],
        "metadata_files": metadata["metadata_files"],
        "slaves": nodes,
    }
    node_csv = io.StringIO(newline="")
    writer = csv.writer(node_csv, lineterminator="\n")
    writer.writerow([
        "node_index", "node_name", "type", "role", "geo_number", "slave_id",
        "pcb_id", "mac", "fs_hz", "fs_known", "fs_exact_known", "adc_config",
        "adc_range_v", "adc_counts_per_volt", "hw_class", "hw_type", "position_m",
        "hammer_offset_m", "connected", "visible", "raw_count", "filt_count",
        "pga_code", "pga_gain", "vdac_byte", "cal_vdacs", "cal_vdac_details",
        "psoc_ok", "invert_signal", "fir_cmd",
    ])
    for node in nodes:
        writer.writerow([
            node["index"], node["name"], node["type"], node["role"],
            node["geo_number"] if node["geo_number"] is not None else "", node["slave_id"],
            node["pcb_id"], "", FS, 1, 1, 1, 2.5, 131072 / 2.5,
            node["hw_class"], node["hw_type"], node["position_m"], node["hammer_offset_m"],
            1, 1, SAMPLE_COUNT, 0, node["pga_code"], node["pga_gain"], 128,
            "[]", "[]", 1, 0, node["fir_cmd"],
        ])

    metadata_json = json.dumps(metadata, indent=2, ensure_ascii=False) + "\n"
    with ZipFile(path, "w", compression=ZIP_STORED) as zf:
        zf.writestr("metadata.json", metadata_json)
        zf.writestr("maestro/metadata.json", metadata_json)
        zf.writestr("maestro/config.json", json.dumps(master_config, indent=2, ensure_ascii=False) + "\n")
        zf.writestr("combined/nodes.csv", node_csv.getvalue())
        for name, data in payloads:
            zf.writestr(name, data)
    return path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="capture_raw_metadata_only.zip")
    print(write_capture_zip(parser.parse_args().path))
