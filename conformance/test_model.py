"""Model-aware tests: the pycolmap-backed join of base model + sidecars.

Skipped entirely when pycolmap is not installed (it is an optional dependency).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pycolmap")

from colmap4d import model, validate  # noqa: E402  (after importorskip)

GOLDEN = Path(__file__).parent / "golden"
MIN_SCENE = GOLDEN / "minimal_scene" / "sparse"
PLAIN = GOLDEN / "plain_colmap" / "sparse"
DANGLING = GOLDEN / "dangling_ids" / "sparse"
DUP_IDS = GOLDEN / "dup_ids" / "sparse"


def test_read_reconstruction_counts():
    rec = model.read_reconstruction(MIN_SCENE)
    assert rec.num_images() == 3
    assert rec.num_points3D() == 6


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
    assert 5 not in mv.effective_points_t()
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
    # points_t references point 999, absent from the model -> dropped from effective view.
    mv = model.load_model_view(DANGLING)
    assert set(mv.effective_points_t()) == {1, 2}
    assert 999 not in mv.effective_points_t()


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
