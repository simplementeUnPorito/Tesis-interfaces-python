"""Convert a Geophone Scope browser ZIP export into a MATLAB .mat file.

The target schema intentionally follows the desktop GUI exporter in
gui/main_window.py: flat keys such as node1_raw, node1_filt, node1_fir_cmd,
plus redundant metadata captured by the web client.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
from scipy.io import savemat


def _read_json(zf: ZipFile, name: str) -> dict:
    with zf.open(name) as fh:
        return json.loads(fh.read().decode("utf-8"))


def _read_f32le(zf: ZipFile, name: str | None) -> np.ndarray:
    if not name:
        return np.array([], dtype=np.float32)
    try:
        data = zf.read(name)
    except KeyError:
        return np.array([], dtype=np.float32)
    return np.frombuffer(data, dtype="<f4").astype(np.float32, copy=True)


def _as_float_array(value: object) -> np.ndarray:
    return np.asarray(value or [], dtype=np.float64)


def _as_str(value: object) -> str:
    return "" if value is None else str(value)


def zip_to_mat_dict(zip_path: str | Path) -> dict:
    """Read a browser ZIP export and return a savemat-ready dict."""
    zip_path = Path(zip_path)
    with ZipFile(zip_path, "r") as zf:
        meta = _read_json(zf, "metadata.json")

        save_dict: dict = {
            "fs": float(meta.get("fs", 0.0) or 0.0),
            "n_slaves": int(meta.get("n_slaves", 0) or 0),
            "n_batches": int(meta.get("n_batches", 0) or 0),
            "save_time": _as_str(meta.get("save_time", "")),
            "created_at": _as_str(meta.get("created_at", "")),
            "source": _as_str(meta.get("source", "esp32_master_web_ui")),
            "schema": _as_str(meta.get("schema", "")),
            "schema_target": _as_str(meta.get("schema_target", "")),
            "metadata_json": json.dumps(meta, ensure_ascii=False, sort_keys=True),
            "samples_per_batch": int(meta.get("samples_per_batch", 0) or 0),
            "signal_units": _as_str(meta.get("signal_units", "V")),
            "adc_counts_per_volt": float(meta.get("adc_counts_per_volt", np.nan)),
            "vdac_step_volts": float(meta.get("vdac_step_volts", np.nan)),
            "notch_f0": float(meta.get("notch_f0", np.nan)),
            "display_secs": float(meta.get("display_secs") or np.nan),
            "max_buf_secs": float(meta.get("max_buf_secs") or np.nan),
            "visible_nodes": np.asarray(meta.get("visible_nodes", []), dtype=np.int16),
            "zip_file": str(zip_path),
        }

        for node in meta.get("nodes", []):
            idx = int(node.get("index", 0))
            prefix = f"node{idx}_"
            raw_v = _read_f32le(zf, node.get("raw_file"))
            filt_v = _read_f32le(zf, node.get("filt_file"))

            save_dict[prefix + "fs"] = float(node.get("fs", meta.get("fs", 0.0)) or 0.0)
            save_dict[prefix + "fs_known"] = bool(node.get("fs_known", False))
            save_dict[prefix + "raw"] = raw_v
            save_dict[prefix + "filt"] = filt_v
            save_dict[prefix + "raw_v"] = raw_v
            save_dict[prefix + "filt_v"] = filt_v
            save_dict[prefix + "raw_count"] = int(node.get("raw_count", raw_v.size) or 0)
            save_dict[prefix + "filt_count"] = int(node.get("filt_count", filt_v.size) or 0)
            save_dict[prefix + "batch_count"] = int(node.get("batch_count", 0) or 0)
            save_dict[prefix + "total_samples"] = int(node.get("total_samples", raw_v.size) or 0)
            save_dict[prefix + "pga_code"] = int(node.get("pga_code", 0) or 0)
            save_dict[prefix + "vdac_byte"] = int(node.get("vdac_byte", 0) or 0)
            save_dict[prefix + "pgavdac_code"] = int(node.get("pgavdac_code", 0) or 0)
            save_dict[prefix + "visible"] = bool(node.get("visible", False))
            save_dict[prefix + "fir_cmd"] = _as_str(node.get("fir_cmd", ""))
            save_dict[prefix + "dc_remove"] = bool(node.get("dc_remove", False))
            save_dict[prefix + "notch_enabled"] = bool(node.get("notch_enabled", False))
            save_dict[prefix + "notch_harm"] = int(node.get("notch_harm", 0) or 0)
            save_dict[prefix + "pga_gain"] = (
                np.nan if node.get("pga_gain") is None else float(node.get("pga_gain"))
            )
            save_dict[prefix + "pgavdac_gain"] = (
                np.nan if node.get("pgavdac_gain") is None else float(node.get("pgavdac_gain"))
            )
            save_dict[prefix + "slave_id"] = _as_str(node.get("slave_id", ""))
            save_dict[prefix + "psoc_ok"] = (
                np.nan if node.get("psoc_ok") is None else bool(node.get("psoc_ok"))
            )
            save_dict[prefix + "mac"] = _as_str(node.get("mac", ""))
            save_dict[prefix + "health"] = int(node.get("health", 0) or 0)
            save_dict[prefix + "drift_hist"] = _as_float_array(node.get("drift_hist", []))
            save_dict[prefix + "latency_hist"] = _as_float_array(node.get("latency_hist", []))
            save_dict[prefix + "raw_file"] = _as_str(node.get("raw_file", ""))
            save_dict[prefix + "filt_file"] = _as_str(node.get("filt_file", ""))

        return save_dict


def convert(zip_path: str | Path, mat_path: str | Path | None = None) -> Path:
    """Convert zip_path to mat_path and return the written path."""
    zip_path = Path(zip_path)
    mat_path = Path(mat_path) if mat_path is not None else zip_path.with_suffix(".mat")
    save_dict = zip_to_mat_dict(zip_path)
    savemat(mat_path, save_dict, do_compression=True, oned_as="column")
    return mat_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_path", help="Browser ZIP export")
    parser.add_argument("mat_path", nargs="?", help="Output .mat path (default: same basename)")
    args = parser.parse_args()
    out = convert(args.zip_path, args.mat_path)
    print(out)


if __name__ == "__main__":
    main()
