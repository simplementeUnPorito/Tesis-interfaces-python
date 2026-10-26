from __future__ import annotations

import json
from zipfile import ZIP_STORED, ZipFile

import numpy as np
import pytest

from geophone_scope import field_review_data as frd
from server.pipeline import Pipeline
from tests.capture_zip_fixture import write_capture_zip


def _marks(fs_hz: float, offset_us: float, count: int = 12) -> tuple[frd.TimeMark, ...]:
    samples = np.arange(count, dtype=np.int64) * 2000
    master = offset_us + samples * (1_000_000.0 / fs_hz)
    # Cruza deliberadamente el contador MAC de 32 bits.
    wrapped = np.rint(master).astype(np.int64) & 0xFFFFFFFF
    return tuple(
        frd.TimeMark(int(sample), int(timestamp), 300, "periodic", True)
        for sample, timestamp in zip(samples, wrapped, strict=True)
    )


def test_fit_time_marks_recovers_drift_offset_and_wrap() -> None:
    expected_fs = 2000.0 * (1.0 + 37e-6)
    expected_offset = float((1 << 32) - 750_000)

    fit = frd.fit_time_marks(_marks(expected_fs, expected_offset))

    assert fit.fs_hz == pytest.approx(expected_fs, rel=2e-7)
    assert fit.offset_us == pytest.approx(expected_offset, abs=0.6)
    assert fit.residual_rms_us < 0.4
    assert fit.samples_used == 12


def test_fit_time_marks_tolerates_one_sample_anchor_noise() -> None:
    expected_fs = 2000.0 * (1.0 - 24e-6)
    offset_us = 2_345_678.0
    true_samples = np.arange(20, dtype=np.int64) * 4000
    jitter = np.resize(np.asarray([-1, 0, 1, 0], dtype=np.int64), true_samples.size)
    timestamps = np.rint(offset_us + true_samples * (1_000_000.0 / expected_fs)).astype(np.int64)
    marks = tuple(
        frd.TimeMark(int(sample + error), int(timestamp & 0xFFFFFFFF), 600,
                     "periodic", True)
        for sample, error, timestamp in zip(true_samples, jitter, timestamps, strict=True)
    )

    fit = frd.fit_time_marks(marks)

    assert fit.fs_hz == pytest.approx(expected_fs, rel=8e-6)
    assert fit.offset_us == pytest.approx(offset_us, abs=180.0)
    assert fit.residual_rms_us < 0.75 * (1_000_000.0 / expected_fs)


def test_common_master_grid_aligns_channels_with_different_offsets() -> None:
    fs_hz = 2000.0
    channel_a = frd.ChannelRef(
        role="geo", node_index=1, pcb_id="S1", raw_file=None,
        filt_file=None, fs=fs_hz, position_m=1.0,
        time_marks=_marks(fs_hz, 1_000_000.0),
    )
    channel_b = frd.ChannelRef(
        role="geo", node_index=2, pcb_id="S2", raw_file=None,
        filt_file=None, fs=fs_hz, position_m=2.0,
        time_marks=_marks(fs_hz, 1_000_500.0),
    )
    signal_a = np.arange(20, dtype=float)
    signal_b = np.arange(20, dtype=float) + 100.0

    grid_us, aligned = frd.align_channels_to_master_grid(
        [(channel_a, signal_a), (channel_b, signal_b)], target_fs=fs_hz
    )

    assert grid_us[0] == pytest.approx(
        1_000_500.0 - frd.ADC_SOC_LATENCY_S * 1_000_000.0
    )
    assert aligned[channel_a.node_index][0] == pytest.approx(1.0)
    assert aligned[channel_b.node_index][0] == pytest.approx(100.0)

    uncorrected_grid_us, _ = frd.align_channels_to_master_grid(
        [(channel_a, signal_a), (channel_b, signal_b)],
        target_fs=fs_hz,
        absolute_time_latency_s=0.0,
    )
    assert uncorrected_grid_us[0] == pytest.approx(1_000_500.0)


def test_channel_discovery_loads_time_marks_csv(tmp_path) -> None:
    source = write_capture_zip(tmp_path / "source.zip")
    with ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    metadata = json.loads(entries["metadata.json"])
    node = next(item for item in metadata["nodes"] if item["index"] == 1)
    node["sample_rate_mhz"] = 2_000_000
    node["fs"] = 2000.0
    node["time_marks_file"] = "geo1_s1/time_marks.csv"
    entries[node["time_marks_file"]] = (
        b"sample,master_us,uncert,kind,ok\n"
        b"0,4294967000,400,start,1\n"
        b"2000,999704,450,periodic,1\n"
    )
    encoded = (json.dumps(metadata, ensure_ascii=False) + "\n").encode()
    entries["metadata.json"] = encoded
    entries["maestro/metadata.json"] = encoded
    capture = tmp_path / "with-time-marks.zip"
    with ZipFile(capture, "w", compression=ZIP_STORED) as archive:
        for name, contents in entries.items():
            archive.writestr(name, contents)

    dataset = frd.discover_dataset(capture)
    channel = next(shot.geo for shot in dataset.shots if shot.geo.node_index == 1)

    assert channel.fs == pytest.approx(2000.0)
    assert channel.time_marks_file is not None
    assert [mark.kind for mark in channel.time_marks] == ["start", "periodic"]
    assert frd.fit_time_marks(channel.time_marks).fs_hz == pytest.approx(2000.0)


def test_pipeline_rejects_declared_missing_time_marks(tmp_path) -> None:
    source = write_capture_zip(tmp_path / "source.zip")
    with ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    metadata = json.loads(entries["metadata.json"])
    node = next(item for item in metadata["nodes"] if item["index"] == 1)
    node["time_marks_file"] = "geo1_s1/time_marks.csv"
    encoded = (json.dumps(metadata, ensure_ascii=False) + "\n").encode()
    entries["metadata.json"] = encoded
    entries["maestro/metadata.json"] = encoded
    capture = tmp_path / "missing-time-marks.zip"
    with ZipFile(capture, "w", compression=ZIP_STORED) as archive:
        for name, contents in entries.items():
            archive.writestr(name, contents)

    pipeline = Pipeline.__new__(Pipeline)
    with pytest.raises(ValueError, match="time_marks"):
        pipeline._validate_archive(capture)
