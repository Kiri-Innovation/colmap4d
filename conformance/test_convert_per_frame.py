"""Tests for the per-frame-COLMAP -> single-colmap4d converter.

Two layers:
  * golden-as-frames: uses small golden models as pseudo-frames, so it runs in CI without
    any external data (needs pycolmap; skipped otherwise).
  * real data: the cook_subset 5-frame / 21-camera capture, skipped when the fixture path is
    absent. Fixture path is a test constant, never a library default.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("pycolmap")

from colmap4d import model, sidecar, validate  # noqa: E402
from colmap4d.convert.per_frame_colmap import (  # noqa: E402
    DEFAULT_FRAME_INTERVAL_NS,
    convert_per_frame_colmap,
)

GOLDEN = Path(__file__).parent / "golden"
FRAME_A = GOLDEN / "plain_colmap" / "sparse"  # 1 cam, 2 imgs, 2 pts
FRAME_B = GOLDEN / "no_devices" / "sparse"  # 1 cam, 2 imgs, 2 pts

# Real-data fixture: point COLMAP4D_REAL_FRAMES_DIR at a directory containing
# per-frame models (colmap_0/sparse/0, colmap_1/sparse/0, ...). Tests skip
# when unset — no machine-specific path is hardcoded here.
_REAL_FRAMES_DIR = os.environ.get("COLMAP4D_REAL_FRAMES_DIR")
REAL_FRAMES = (
    [Path(_REAL_FRAMES_DIR) / f"colmap_{i}" / "sparse" / "0" for i in range(5)]
    if _REAL_FRAMES_DIR
    else None
)


# --------------------------------------------------------------------------- #
# golden-as-frames (CI-friendly)
# --------------------------------------------------------------------------- #
def test_merges_two_pseudo_frames(tmp_path):
    out = tmp_path / "merged"
    res = convert_per_frame_colmap([FRAME_A, FRAME_B], out)
    assert res.num_frames == 2
    assert res.num_cameras == 2  # 1 + 1, remapped to global ids
    assert res.num_images == 4  # 2 + 2
    assert res.num_points == 4  # 2 + 2, per-frame independent (no dedup)
    assert res.synthetic_times is True


def test_merged_model_is_readable_and_valid(tmp_path):
    out = tmp_path / "merged"
    convert_per_frame_colmap([FRAME_A, FRAME_B], out)
    mv = model.load_model_view(out)
    assert len(mv.image_ids()) == 4
    assert len(mv.point_ids()) == 4
    # every point and image carries a timestamp; two distinct frame instants
    assert set(mv.effective_times().values()) == {0, DEFAULT_FRAME_INTERVAL_NS}
    assert len(mv.effective_points_t()) == 4
    assert validate.exit_code(model.validate_full(out)) == 0


def test_names_encode_frame_and_times_match_frame(tmp_path):
    out = tmp_path / "merged"
    convert_per_frame_colmap([FRAME_A, FRAME_B], out)
    mv = model.load_model_view(out)
    names = {iid: im.name for iid, im in mv.base.images.items()}
    for iid, name in names.items():
        prefix = "frame_0000/" if mv.image_time(iid) == 0 else "frame_0001/"
        assert name.startswith(prefix)


def test_explicit_frame_times(tmp_path):
    out = tmp_path / "merged"
    res = convert_per_frame_colmap(
        [FRAME_A, FRAME_B], out, frame_times_ns=[1_000_000_000, 2_000_000_000]
    )
    assert res.synthetic_times is False
    sc = sidecar.load_sidecars(out)
    assert set(sc.times.values()) == {1_000_000_000, 2_000_000_000}
    assert sc.time_meta["clock_domain"] == "provided"


def test_dedup_points_is_reserved_not_implemented(tmp_path):
    with pytest.raises(NotImplementedError, match="dedup"):
        convert_per_frame_colmap([FRAME_A, FRAME_B], tmp_path / "x", dedup_points=True)


def test_frame_times_length_mismatch_raises(tmp_path):
    with pytest.raises(ValueError, match="frame_times_ns"):
        convert_per_frame_colmap([FRAME_A, FRAME_B], tmp_path / "x", frame_times_ns=[1])


# --------------------------------------------------------------------------- #
# real data (skipped when the fixture is absent)
# --------------------------------------------------------------------------- #
_HAVE_REAL = bool(REAL_FRAMES) and all(d.exists() for d in REAL_FRAMES)


@pytest.mark.skipif(not _HAVE_REAL, reason="cook_subset fixture not present")
def test_real_cook_subset_5frames(tmp_path):
    out = tmp_path / "cook"
    res = convert_per_frame_colmap(REAL_FRAMES, out)
    assert res.num_frames == 5
    assert res.num_images == 5 * 21  # 21 physical cameras x 5 frames
    # cameras are kept per frame: frame 0 shares 1 intrinsic, frames 1-4 have 21 each
    assert res.num_cameras == 1 + 21 * 4
    # points are the sum of per-frame reconstructions (no cross-frame dedup)
    expected_points = sum(model.read_reconstruction(d).num_points3D() for d in REAL_FRAMES)
    assert res.num_points == expected_points

    mv = model.load_model_view(out)
    assert len(mv.image_ids()) == 105
    assert len(mv.point_ids()) == expected_points
    # 5 distinct frame instants, one per frame
    assert len(set(mv.effective_times().values())) == 5
    # clean under the full validate suite (dangling + duplicate + device uniqueness)
    assert validate.exit_code(model.validate_full(out)) == 0
    # device provenance: 21 physical cameras, each bound to its per-frame camera_ids
    assert len(mv.sidecars.time_meta["devices"]) == 21
    assert validate.check_device_camera_ids_unique(mv.sidecars.time_meta) == []
