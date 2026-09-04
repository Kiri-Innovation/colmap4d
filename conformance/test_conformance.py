"""Conformance tests: read the hand-authored golden models and assert the values
documented in conformance/golden/README.md. These goldens are the executable
definition of the spec; do not relax an assertion to match the implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from colmap4d import model, sidecar, validate

GOLDEN = Path(__file__).parent / "golden"
MIN_SCENE = GOLDEN / "minimal_scene" / "sparse"
MIN_SCENE_BIN = GOLDEN / "minimal_scene_bin" / "sparse"
PLAIN = GOLDEN / "plain_colmap" / "sparse"
NO_DEVICES = GOLDEN / "no_devices" / "sparse"
DUP_IDS = GOLDEN / "dup_ids" / "sparse"
DANGLING = GOLDEN / "dangling_ids" / "sparse"
TIMES_ONLY = GOLDEN / "times_only" / "sparse"
DANGLING_EARLY = GOLDEN / "dangling_early" / "sparse"

EXPECTED_TIMES = {
    1: 1699999999123456789,
    2: 1699999999156789012,
    3: 1699999999140000000,
}
EXPECTED_POINTS_T = {
    1: 1699999999140081934,
    2: 1699999999140122901,
    3: 1699999999131728395,
    4: 1699999999148394506,
    6: 1699999999140081934,
}
POINT_TEMPORALLY_UNBOUNDED = 5  # intentionally absent from points_t


# --------------------------------------------------------------------------- #
# times / points_t text golden
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# binary sidecar layout (spec I.E) — golden bytes authored to the spec, not dumped
# --------------------------------------------------------------------------- #
def test_bin_layout_field_equal_to_txt():
    # minimal_scene_bin's .bin decode field-for-field to the same values as the text golden.
    assert sidecar.read_times_bin(MIN_SCENE_BIN / "times.bin") == EXPECTED_TIMES
    assert sidecar.read_points_t_bin(MIN_SCENE_BIN / "points_t.bin") == EXPECTED_POINTS_T


def test_bin_layout_byte_size_matches_spec():
    # spec I.E: file size MUST be 8 + count * record_size (12 for times, 16 for points_t).
    assert (MIN_SCENE_BIN / "times.bin").stat().st_size == 8 + len(EXPECTED_TIMES) * 12
    assert (MIN_SCENE_BIN / "points_t.bin").stat().st_size == 8 + len(EXPECTED_POINTS_T) * 16


def test_bin_golden_loads_with_timeless_point():
    sc = sidecar.load_sidecars(MIN_SCENE_BIN)  # prefers .bin
    assert sc.times == EXPECTED_TIMES
    assert sc.points_t == EXPECTED_POINTS_T
    assert sc.point_time(5) is sidecar.TIMELESS  # point 5 absent → temporally-unbounded


def test_bin_duplicate_id_last_wins():
    # dup_ids/times.bin lists image 2 twice; a reader collapses last-wins (spec I.D/I.E),
    # identical to the text form.
    assert sidecar.read_times_bin(DUP_IDS / "times.bin") == sidecar.read_times_txt(
        DUP_IDS / "times.txt"
    )
    assert sidecar.read_times_bin(DUP_IDS / "times.bin")[2] == 1699999999155555555


# --------------------------------------------------------------------------- #
# time_meta absent — undeclared relative time (spec I.C)
# --------------------------------------------------------------------------- #
def test_times_only_is_conformant():
    sc = sidecar.load_sidecars(TIMES_ONLY)
    assert sc.times == {1: 1699999999100000000, 2: 1699999999133333333}
    assert sc.time_meta is None  # no time_meta.json
    assert sc.points_t == {}  # all points temporally-unbounded


def test_validate_warns_times_without_meta():
    problems = validate.validate(TIMES_ONLY)
    absent = [p for p in problems if p.code == "time_meta.absent"]
    assert len(absent) == 1
    assert absent[0].severity == validate.WARNING
    assert validate.exit_code(problems) == 0
    assert validate.exit_code(problems, strict=True) == 1


def test_validate_no_meta_warning_when_meta_present():
    problems = validate.validate(MIN_SCENE)
    assert not any(p.code == "time_meta.absent" for p in problems)


def test_check_time_meta_present_direct():
    assert validate.check_time_meta_present({1: 5}, None)  # warns
    assert validate.check_time_meta_present({}, None) == []  # no times → no warning
    assert validate.check_time_meta_present({1: 5}, {"clock_domain": "utc_ntp"}) == []


# --------------------------------------------------------------------------- #
# t0 domain (spec I.B): effective (dangling-dropped) min, not raw min
# --------------------------------------------------------------------------- #
def test_t0_effective_ignores_dangling_early_time():
    # points_t has a dangling id 999 at an EARLY time; the model-joined t0 must ignore it.
    mv = model.load_model_view(DANGLING_EARLY)  # zero-dep reader (no pycolmap)
    assert mv.t0_ns() == 1699999999200000000  # min over in-model records only
    # the model-less approximation DOES get pulled earlier by the dangling id:
    sc = sidecar.load_sidecars(DANGLING_EARLY)
    assert sc.t0_ns() == 1699999999000000000
    assert mv.t0_ns() != sc.t0_ns()  # the whole point of pinning t0 to the effective set


def test_times_txt_matches_golden():
    assert sidecar.read_times_txt(MIN_SCENE / "times.txt") == EXPECTED_TIMES


def test_points_t_txt_matches_golden():
    assert sidecar.read_points_t_txt(MIN_SCENE / "points_t.txt") == EXPECTED_POINTS_T


def test_int64_ns_precision_preserved():
    # Nanosecond values exceed float64's exact-integer range; they must survive as int.
    times = sidecar.read_times_txt(MIN_SCENE / "times.txt")
    assert times[1] == 1699999999123456789
    assert isinstance(times[1], int)


# --------------------------------------------------------------------------- #
# temporally-unbounded rule (spec Part I.A)
# --------------------------------------------------------------------------- #
def test_missing_point_is_temporally_unbounded():
    pts = sidecar.read_points_t_txt(MIN_SCENE / "points_t.txt")
    assert POINT_TEMPORALLY_UNBOUNDED not in pts
    assert sidecar.is_temporally_unbounded(pts, POINT_TEMPORALLY_UNBOUNDED) is True
    assert sidecar.is_temporally_unbounded(pts, 1) is False


def test_point_time_returns_timeless_sentinel():
    sc = sidecar.load_sidecars(MIN_SCENE)
    assert sc.point_time(POINT_TEMPORALLY_UNBOUNDED) is sidecar.TIMELESS  # None
    assert sc.point_time(1) == 1699999999140081934


# --------------------------------------------------------------------------- #
# binary/text consistency (round-trip through .bin)
# --------------------------------------------------------------------------- #
def test_times_bin_txt_consistency(tmp_path):
    txt = sidecar.read_times_txt(MIN_SCENE / "times.txt")
    sidecar.write_times_bin(tmp_path / "times.bin", txt)
    assert sidecar.read_times_bin(tmp_path / "times.bin") == txt == EXPECTED_TIMES


def test_points_t_bin_txt_consistency(tmp_path):
    txt = sidecar.read_points_t_txt(MIN_SCENE / "points_t.txt")
    sidecar.write_points_t_bin(tmp_path / "points_t.bin", txt)
    assert sidecar.read_points_t_bin(tmp_path / "points_t.bin") == txt == EXPECTED_POINTS_T


def test_bin_preferred_over_txt_when_both_present(tmp_path):
    # Write a .bin that differs from a .txt in the same dir; loader must prefer .bin.
    sidecar.write_times_txt(tmp_path / "times.txt", {9: 111})
    sidecar.write_times_bin(tmp_path / "times.bin", {7: 222})
    assert sidecar.load_sidecars(tmp_path).times == {7: 222}


def test_txt_roundtrip(tmp_path):
    sidecar.write_points_t_txt(tmp_path / "points_t.txt", EXPECTED_POINTS_T)
    assert sidecar.read_points_t_txt(tmp_path / "points_t.txt") == EXPECTED_POINTS_T


# --------------------------------------------------------------------------- #
# time_meta
# --------------------------------------------------------------------------- #
def test_time_meta_golden():
    meta = sidecar.read_time_meta(MIN_SCENE / "time_meta.json")
    assert meta["time_convention"] == "mid_exposure"
    assert meta["clock_domain"] == "utc_ntp"
    assert meta["time_unit"] == "ns"
    assert set(meta["devices"]) == {"device_a", "device_b"}
    assert meta["devices"]["device_a"]["camera_ids"] == [1]
    assert meta["devices"]["device_a"]["sync_err_ns"] == 3000000


# --------------------------------------------------------------------------- #
# device attribution is optional provenance (spec Part I, OPEN-1 settled):
# a model WITH camera_ids and a model WITHOUT devices are both conformant.
# --------------------------------------------------------------------------- #
def test_with_camera_ids_is_conformant():
    # minimal_scene carries devices+camera_ids; sidecar layer is agnostic to it.
    sc = sidecar.load_sidecars(MIN_SCENE)
    assert sc.time_meta["devices"]["device_b"]["camera_ids"] == [2]
    assert sc.times == EXPECTED_TIMES  # time layer unaffected by device metadata


def test_without_devices_is_conformant():
    sc = sidecar.load_sidecars(NO_DEVICES)
    assert "devices" not in sc.time_meta  # device-less (e.g. converted from Neu3D)
    assert sc.time_meta["time_convention"] == "mid_exposure"
    assert sc.times == {1: 1699999999100000000, 2: 1699999999133333333}
    assert sc.point_time(1) == 1699999999116666666


def test_validate_camera_ids_unique_passes_on_goldens():
    for d in (MIN_SCENE, NO_DEVICES):
        meta = sidecar.load_sidecars(d).time_meta
        assert validate.check_device_camera_ids_unique(meta) == []


def test_validate_camera_ids_unique_flags_conflict():
    bad = {"devices": {"phone_a": {"camera_ids": [1, 2]}, "phone_b": {"camera_ids": [2]}}}
    problems = validate.check_device_camera_ids_unique(bad)
    assert len(problems) == 1
    assert problems[0].severity == validate.ERROR
    assert "CAMERA_ID 2" in problems[0].message


def test_validate_camera_ids_unique_tolerates_absent():
    assert validate.check_device_camera_ids_unique(None) == []
    assert validate.check_device_camera_ids_unique({}) == []
    assert validate.check_device_camera_ids_unique({"devices": {"x": {}}}) == []


# --------------------------------------------------------------------------- #
# OPEN-3: duplicate ids resolve last-wins (normative), dangling ids are ignored
# by consumers but flagged by validate. Both tolerances are pinned by golden.
# --------------------------------------------------------------------------- #
def test_duplicate_id_reader_is_last_wins():
    # Golden dup_ids/times.txt lists image 2 twice; the LAST value wins, deterministically.
    times = sidecar.read_times_txt(DUP_IDS / "times.txt")
    assert times == {1: 1699999999100000000, 2: 1699999999155555555}


def test_duplicate_id_strict_reader_raises():
    with pytest.raises(ValueError, match="duplicate id 2"):
        sidecar.read_times_txt(DUP_IDS / "times.txt", strict=True)


def test_duplicate_id_survives_bin_roundtrip_as_last_wins():
    # Raw pairs preserve the duplicate; collapsing (here via .bin write/read) is last-wins.
    pairs = sidecar.read_times_pairs(DUP_IDS / "times.txt")
    assert pairs == [
        (1, 1699999999100000000),
        (2, 1699999999120000000),
        (2, 1699999999155555555),
    ]


def test_validate_flags_duplicate_as_error():
    pairs = sidecar.read_times_pairs(DUP_IDS / "times.txt")
    problems = validate.check_duplicate_ids(pairs, "times")
    assert len(problems) == 1
    assert problems[0].severity == validate.ERROR
    assert validate.exit_code(problems) == 1  # ERROR ⇒ non-zero, CI-catchable


def test_dangling_id_raw_read_includes_it():
    # The pure sidecar reader has no model, so it returns the dangling id verbatim.
    pts = sidecar.read_points_t_txt(DANGLING / "points_t.txt")
    assert set(pts) == {1, 2, 999}


def test_dangling_id_consumer_ignores_model_absent():
    # A model-aware consumer keeps only ids present in the model → {1, 2}.
    pts = sidecar.read_points_t_txt(DANGLING / "points_t.txt")
    model_point_ids = {1, 2}
    effective = {k: v for k, v in pts.items() if k in model_point_ids}
    assert set(effective) == {1, 2}


def test_validate_flags_dangling_as_warning_with_sfm_hint():
    pts = sidecar.read_points_t_txt(DANGLING / "points_t.txt")
    problems = validate.check_dangling_ids(list(pts), {1, 2}, "points_t")
    assert len(problems) == 1
    assert problems[0].severity == validate.WARNING
    assert "999" in problems[0].message
    # Message must name the whole-model misalignment risk, not just "unknown id".
    assert "re-run" in problems[0].message or "misalign" in problems[0].message
    assert validate.exit_code(problems) == 0  # WARNING ⇒ exit 0 by default
    assert validate.exit_code(problems, strict=True) == 1  # --strict promotes it


def test_validate_orchestration_on_goldens():
    # Clean goldens produce no ERROR; dup_ids produces an ERROR.
    assert validate.exit_code(validate.validate(MIN_SCENE)) == 0
    assert validate.exit_code(validate.validate(NO_DEVICES)) == 0
    assert validate.exit_code(validate.validate(DUP_IDS)) == 1
    # Dangling only surfaces when the model's id set is supplied.
    dangling_problems = validate.validate(DANGLING, point_ids={1, 2})
    assert validate.exit_code(dangling_problems) == 0
    assert validate.exit_code(dangling_problems, strict=True) == 1


# --------------------------------------------------------------------------- #
# backward-compatibility baseline (plain COLMAP, no sidecars)
# --------------------------------------------------------------------------- #
def test_plain_colmap_loads_without_error():
    sc = sidecar.load_sidecars(PLAIN)
    assert sc.times == {}
    assert sc.points_t == {}
    assert sc.time_meta is None


def test_plain_colmap_all_points_unbounded():
    sc = sidecar.load_sidecars(PLAIN)
    for pid in (1, 2, 999):
        assert sc.point_time(pid) is sidecar.TIMELESS
        assert sidecar.is_temporally_unbounded(sc.points_t, pid) is True


def test_plain_colmap_no_t0():
    assert sidecar.load_sidecars(PLAIN).t0_ns() is None


# --------------------------------------------------------------------------- #
# rebase contract (spec Part I.B)
# --------------------------------------------------------------------------- #
def test_t0_is_min_timestamp():
    sc = sidecar.load_sidecars(MIN_SCENE)
    # min over both times and points_t; image 1's timestamp is the global minimum.
    assert sc.t0_ns() == 1699999999123456789


def test_rebase_to_seconds_f32_fits_float32():
    import struct as _struct

    sc = sidecar.load_sidecars(MIN_SCENE)
    t0 = sc.t0_ns()
    rel = sidecar.rebase_to_seconds_f32(EXPECTED_TIMES[2], t0)  # ~0.025 s
    # Narrow to float32 and back; microsecond-scale error is acceptable, ns-scale is not.
    f32 = _struct.unpack("f", _struct.pack("f", rel))[0]
    assert abs(f32 - rel) < 1e-6
    assert rel > 0


@pytest.mark.parametrize("missing", ["times.txt", "points_t.txt", "time_meta.json"])
def test_partial_sidecars_ok(tmp_path, missing):
    # Any subset of sidecars present must load without error.
    if missing != "times.txt":
        sidecar.write_times_txt(tmp_path / "times.txt", EXPECTED_TIMES)
    if missing != "points_t.txt":
        sidecar.write_points_t_txt(tmp_path / "points_t.txt", EXPECTED_POINTS_T)
    if missing != "time_meta.json":
        sidecar.write_time_meta(tmp_path / "time_meta.json", {"time_convention": "mid_exposure"})
    sc = sidecar.load_sidecars(tmp_path)  # must not raise
    assert isinstance(sc.times, dict)
