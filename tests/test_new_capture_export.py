from __future__ import annotations

import json
from zipfile import ZIP_STORED, ZipFile

import numpy as np

from geophone_scope import field_review_data as frd
from geophone_scope.zip_to_mat import zip_to_mat_dict
from server.catalog import scan_catalog
from tests.capture_zip_fixture import NOISE_SIGNATURES, SAMPLE_COUNT, write_capture_zip


def test_fixture_matches_raw_metadata_only_contract(tmp_path) -> None:
    zip_path = write_capture_zip(tmp_path / "fixture.zip")
    with ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        meta = json.loads(zf.read("metadata.json"))
        signatures = json.loads(zf.read("noise_signatures.json"))

    assert meta["schema"] == "geophone_scope_web_zip_v4"
    assert meta["payload"] == "raw_metadata_only"
    assert len([node for node in meta["nodes"] if node["role"] == "geo"]) == 2
    assert len([node for node in meta["nodes"] if node["role"] == "hammer"]) == 1
    assert not any(name.endswith("filt_f32le.bin") for name in names)
    assert not any(name.endswith("raw.csv") or name.endswith("filt.csv") for name in names)
    assert sum(name.endswith("noise_marks.csv") for name in names) == 3
    assert meta["noise_signatures_file"] == "noise_signatures.json"
    assert signatures == NOISE_SIGNATURES


def test_field_review_loads_zip_and_reconstructs_filtered_trace(tmp_path) -> None:
    zip_path = write_capture_zip(tmp_path / "fixture.zip")
    dataset = frd.discover_dataset(zip_path)

    assert len(dataset.shots) == 2
    channel = dataset.shots[0].geo
    raw = frd.load_signal(channel, prefer_filtered=False, apply_invert=False)
    filtered = frd.load_signal(channel, prefer_filtered=True, apply_invert=False)

    assert raw.size == SAMPLE_COUNT
    assert np.isnan(raw).sum() == 30
    assert filtered.size == raw.size
    assert np.isfinite(filtered).all()
    assert not np.allclose(np.nan_to_num(raw), filtered)
    assert channel.noise_marks
    assert channel.metadata["lost_batches"]
    assert channel.metadata["sync"]["distance_ftm_calibrated_m"] == 2.0
    assert channel.metadata["_noise_signatures"] == NOISE_SIGNATURES


def test_embedded_noise_signatures_replace_interpolation_fallback(tmp_path) -> None:
    current = write_capture_zip(tmp_path / "with-signatures.zip")
    with ZipFile(current) as source:
        entries = {name: source.read(name) for name in source.namelist()}

    metadata = json.loads(entries["metadata.json"])
    metadata.pop("noise_signatures_file")
    metadata_json = (json.dumps(metadata, indent=2, ensure_ascii=False) + "\n").encode()
    entries["metadata.json"] = metadata_json
    entries["maestro/metadata.json"] = metadata_json
    entries.pop("noise_signatures.json")
    fallback = tmp_path / "without-signatures.zip"
    with ZipFile(fallback, "w", compression=ZIP_STORED) as target:
        for name, contents in entries.items():
            target.writestr(name, contents)

    templated_dataset = frd.discover_dataset(current)
    fallback_dataset = frd.discover_dataset(fallback)
    templated_channel = next(
        shot.geo for shot in templated_dataset.shots
        if shot.geo.node_index == 1
    )
    fallback_channel = next(
        shot.geo for shot in fallback_dataset.shots
        if shot.geo.node_index == 1
    )
    templated = frd.load_signal(templated_channel, prefer_filtered=True, apply_invert=False)
    interpolated = frd.load_signal(fallback_channel, prefer_filtered=True, apply_invert=False)

    assert not np.allclose(templated, interpolated)


def test_zip_to_mat_reconstructs_filter_and_exports_new_metadata(tmp_path) -> None:
    zip_path = write_capture_zip(tmp_path / "fixture.zip")
    data = zip_to_mat_dict(zip_path)

    assert np.isnan(data["node1_raw_v"]).sum() == 30
    assert data["node1_filt_v"].size == SAMPLE_COUNT
    assert np.isfinite(data["node1_filt_v"]).all()
    assert data["node1_noise_marks"].shape[1] == 2
    assert data["node1_lost_batches"].tolist() == [2]
    assert data["node1_distance_ftm_calibrated_m"] == 2.0
    assert data["sync_mode"] == "ftm_active"


def test_catalog_exposes_noise_and_sync_metadata(tmp_path) -> None:
    zip_path = write_capture_zip(tmp_path / "fixture.zip")
    dataset = frd.discover_dataset(zip_path)
    catalog = scan_catalog(dataset.raw_root)

    assert catalog["folders"][0]["captures"][0]["sync"]["sync_mode"] == "ftm_active"
    nodes = catalog["folders"][0]["captures"][0]["nodes"]
    geo1 = next(node for node in nodes if node["pcb_id"] == "S1")
    assert geo1["noise_mark_count"] == 2
    assert geo1["lost_batches"] == [2]
    assert geo1["sync"]["distance_declared_m"] == 2.0


def test_old_zip_keeps_using_stored_filtered_signal(tmp_path) -> None:
    current = write_capture_zip(tmp_path / "current.zip")
    old = tmp_path / "old.zip"
    expected = np.full(SAMPLE_COUNT, 0.123, dtype="<f4")
    with ZipFile(current) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    metadata = json.loads(entries["metadata.json"])
    node = next(item for item in metadata["nodes"] if item["index"] == 1)
    node["filt_file"] = "geo1_s1/filt_f32le.bin"
    node["filt_count"] = SAMPLE_COUNT
    metadata_json = (json.dumps(metadata, indent=2, ensure_ascii=False) + "\n").encode()
    entries["metadata.json"] = metadata_json
    entries["maestro/metadata.json"] = metadata_json
    entries[node["filt_file"]] = expected.tobytes()
    with ZipFile(old, "w", compression=ZIP_STORED) as target:
        for name, contents in entries.items():
            target.writestr(name, contents)

    dataset = frd.discover_dataset(old)
    channel = next(shot.geo for shot in dataset.shots if shot.geo.node_index == 1)
    np.testing.assert_array_equal(
        frd.load_signal(channel, prefer_filtered=True, apply_invert=False),
        expected,
    )
    np.testing.assert_array_equal(zip_to_mat_dict(old)["node1_filt_v"], expected)
