"""Model-aware tests: the base-model + sidecar join, and the pure-Python COLMAP reader.

These run with NO external dependency (the reader is pure Python). A small extra test
exercises the pycolmap-backed path when pycolmap happens to be installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from colmap4d import colmap_io, model, validate

GOLDEN = Path(__file__).parent / "golden"
MIN_SCENE = GOLDEN / "minimal_scene" / "sparse"
PLAIN = GOLDEN / "plain_colmap" / "sparse"
DANGLING = GOLDEN / "dangling_ids" / "sparse"
DUP_IDS = GOLDEN / "dup_ids" / "sparse"


# --------------------------------------------------------------------------- #
# pure-Python base-model reader
# --------------------------------------------------------------------------- #
def test_read_base_model_txt():
    m = model.read_base_model(MIN_SCENE)
    assert m.image_ids() == {1, 2, 3}
    assert m.point_ids() == {1, 2, 3, 4, 5, 6}
    assert m.cameras[1].model == "PINHOLE"
    assert m.images[1].name == "device_a/000001.jpg"
    # 2D observation ids line up with points3D tracks
    assert m.points3D[1].track[0] == (1, 0)


def test_colmap_io_bin_roundtrip(tmp_path):
    # Round-trip a hand-read txt model through the classic binary writers/readers.
    m = colmap_io.read_model(MIN_SCENE)
    colmap_io.write_cameras_bin(tmp_path / "cameras.bin", m.cameras)
    colmap_io.write_images_bin(tmp_path / "images.bin", m.images)
    colmap_io.write_points3D_bin(tmp_path / "points3D.bin", m.points3D)
    back = colmap_io.read_model(tmp_path)  # bin preferred
    assert back.image_ids() == m.image_ids()
    assert back.point_ids() == m.point_ids()
    assert back.cameras[1].params == m.cameras[1].params
    assert back.images[1].name == m.images[1].name
    assert back.points3D[6].track == m.points3D[6].track


# --------------------------------------------------------------------------- #
# ModelView join (zero-dep)
# --------------------------------------------------------------------------- #
def test_model_view_join_minimal_scene():
    mv = model.load_model_view(MIN_SCENE)
    assert mv.image_ids() == {1, 2, 3}
    assert mv.point_ids() == {1, 2, 3, 4, 5, 6}
    assert mv.image_time(1) == 1699999999123456789
    assert mv.effective_times() == {
        1: 1699999999123456789,
        2: 1699999999156789012,
        3: 1699999999140000000,
    }
    # point 5 has no points_t entry -> temporally-unbounded
    assert mv.point_time(5) is None
    assert mv.point_time(1) == 1699999999140081934
    assert set(mv.effective_points_t()) == {1, 2, 3, 4, 6}


def test_model_view_rejects_non_model_ids():
    mv = model.load_model_view(MIN_SCENE)
    with pytest.raises(KeyError):
        mv.point_time(999)
    with pytest.raises(KeyError):
        mv.image_time(999)


def test_plain_colmap_all_timeless_via_model():
    mv = model.load_model_view(PLAIN)
    assert mv.effective_times() == {}
    assert mv.effective_points_t() == {}
    for pid in mv.point_ids():
        assert mv.point_time(pid) is None


def test_effective_points_t_drops_dangling():
    mv = model.load_model_view(DANGLING)
    assert set(mv.effective_points_t()) == {1, 2}
    assert 999 not in mv.effective_points_t()


# --------------------------------------------------------------------------- #
# validate_full orchestration (zero-dep: model ids read in pure Python)
# --------------------------------------------------------------------------- #
def test_validate_full_flags_dangling_warning():
    problems = model.validate_full(DANGLING)
    dangling = [p for p in problems if p.code.endswith("dangling_id")]
    assert len(dangling) == 1
    assert dangling[0].severity == validate.WARNING
    assert validate.exit_code(problems) == 0
    assert validate.exit_code(problems, strict=True) == 1


def test_validate_full_flags_duplicate_error():
    problems = model.validate_full(DUP_IDS)
    assert validate.exit_code(problems) == 1
    assert any(p.code.endswith("duplicate_id") and p.severity == validate.ERROR for p in problems)


def test_validate_full_clean_on_minimal_scene():
    assert validate.exit_code(model.validate_full(MIN_SCENE)) == 0


# --------------------------------------------------------------------------- #
# pycolmap-backed reader (only when installed)
# --------------------------------------------------------------------------- #
def test_read_reconstruction_matches_pure_python():
    pytest.importorskip("pycolmap")
    rec = model.read_reconstruction(MIN_SCENE)
    assert rec.num_images() == 3
    assert rec.num_points3D() == 6
    assert {int(i) for i in rec.images} == model.read_base_model(MIN_SCENE).image_ids()
