"""The bulk binary reader against a one-record-at-a-time ``struct`` oracle, plus the
``include_observations`` / ``include_tracks`` partial reads. Zero external dependencies."""

from __future__ import annotations

import gc
import struct
from pathlib import Path

import pytest

from colmap4d import colmap_io, model, validate

GOLDEN = Path(__file__).parent / "golden"
MIN_SCENE = GOLDEN / "minimal_scene" / "sparse"
INVALID = colmap_io.INVALID_POINT3D_ID


def _oracle_images(path: Path) -> dict:
    """Spec-literal decode of images.bin, one ``struct.unpack`` per field group."""
    out = {}
    with open(path, "rb") as f:
        (n,) = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            iid, *pose, cam = struct.unpack("<IdddddddI", f.read(64))
            name = b""
            while (ch := f.read(1)) != b"\x00":
                name += ch
            (n2d,) = struct.unpack("<Q", f.read(8))
            obs = [struct.unpack("<ddQ", f.read(24)) for _ in range(n2d)]
            out[iid] = (name.decode(), cam, pose[:4], pose[4:], obs)
    return out


def _oracle_points(path: Path) -> dict:
    out = {}
    with open(path, "rb") as f:
        (n,) = struct.unpack("<Q", f.read(8))
        for _ in range(n):
            pid, x, y, z, r, g, b, err, tl = struct.unpack("<QdddBBBdQ", f.read(51))
            track = [struct.unpack("<II", f.read(8)) for _ in range(tl)]
            out[pid] = ([x, y, z], [r, g, b], err, track)
    return out


def _synthetic_model(tmp_path: Path) -> colmap_io.BaseModel:
    """Edge cases the golden scene lacks: empty observation lists and tracks, the invalid
    point sentinel, a non-ASCII nested name, large ids and odd record alignment."""
    cams = {7: colmap_io.Camera(7, "PINHOLE", 640, 480, [500.0, 501.0, 320.5, 239.5])}
    images = {
        3: colmap_io.Image(
            3,
            "dev_é/0001.png",
            7,
            [0.5, 0.5, -0.5, 0.5],
            [1.0, -2.0, 3.25],
            [(0.5, 1.5), (-3.0, 1e-300), (1e300, 2.0)],
            [11, INVALID, 2**40],
        ),
        1: colmap_io.Image(1, "a.png", 7, [1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [], []),
        2**31: colmap_io.Image(2**31, "b", 7, [1.0, 0, 0, 0], [0, 0, 0], [(9.0, 8.0)], [11]),
    }
    points = {
        11: colmap_io.Point3D(11, [0.1, -0.2, 3.0], [255, 0, 7], 0.75, [(3, 0), (2**31, 0)]),
        2**40: colmap_io.Point3D(2**40, [1.0, 2.0, 3.0], [1, 2, 3], 0.0, [(3, 2)]),
        5: colmap_io.Point3D(5, [4.0, 5.0, 6.0], [9, 9, 9], 1.5, []),
    }
    colmap_io.write_cameras_bin(tmp_path / "cameras.bin", cams)
    colmap_io.write_images_bin(tmp_path / "images.bin", images)
    colmap_io.write_points3D_bin(tmp_path / "points3D.bin", points)
    return colmap_io.BaseModel(cams, images, points)


def test_bulk_reader_matches_struct_oracle(tmp_path):
    src = _synthetic_model(tmp_path)
    images = colmap_io.read_images_bin(tmp_path / "images.bin")
    points = colmap_io.read_points3D_bin(tmp_path / "points3D.bin")
    assert images == src.images and points == src.points3D
    for iid, (name, cam, qvec, tvec, obs) in _oracle_images(tmp_path / "images.bin").items():
        im = images[iid]
        assert (im.name, im.camera_id, im.qvec, im.tvec) == (name, cam, qvec, tvec)
        assert im.xys == [(x, y) for x, y, _ in obs]
        assert im.point3D_ids == [pid for _, _, pid in obs]
        assert all(type(xy) is tuple and len(xy) == 2 for xy in im.xys)
        assert all(type(pid) is int for pid in im.point3D_ids)
    for pid, (xyz, rgb, err, track) in _oracle_points(tmp_path / "points3D.bin").items():
        p = points[pid]
        assert (p.xyz, p.rgb, p.error, p.track) == (xyz, rgb, err, track)
        assert all(type(t) is tuple for t in p.track)


def test_bulk_reader_matches_text_reader_on_golden(tmp_path):
    m = colmap_io.read_model(MIN_SCENE)  # txt
    colmap_io.write_images_bin(tmp_path / "images.bin", m.images)
    colmap_io.write_points3D_bin(tmp_path / "points3D.bin", m.points3D)
    assert colmap_io.read_images_bin(tmp_path / "images.bin") == m.images
    assert colmap_io.read_points3D_bin(tmp_path / "points3D.bin") == m.points3D


@pytest.mark.parametrize("fmt", ["bin", "txt"])
def test_partial_read_skips_only_observations_and_tracks(tmp_path, fmt):
    if fmt == "bin":
        _synthetic_model(tmp_path)
        model_dir = tmp_path
    else:
        model_dir = MIN_SCENE
    full = colmap_io.read_model(model_dir)
    light = colmap_io.read_model(model_dir, include_observations=False, include_tracks=False)
    assert light.cameras == full.cameras
    assert light.image_ids() == full.image_ids() and light.point_ids() == full.point_ids()
    for iid, im in full.images.items():
        got = light.images[iid]
        assert (got.name, got.camera_id, got.qvec, got.tvec) == (
            im.name,
            im.camera_id,
            im.qvec,
            im.tvec,
        )
        assert got.xys is None and got.point3D_ids is None
    for pid, p in full.points3D.items():
        got = light.points3D[pid]
        assert (got.xyz, got.rgb, got.error) == (p.xyz, p.rgb, p.error)
        assert got.track is None
    # The two switches are independent.
    obs_only = colmap_io.read_model(model_dir, include_tracks=False)
    assert obs_only.images == full.images
    assert all(p.track is None for p in obs_only.points3D.values())


def test_partial_model_view_and_validate_full_ids():
    full = model.load_model_view(MIN_SCENE)
    light = model.load_model_view(MIN_SCENE, include_observations=False, include_tracks=False)
    assert light.image_ids() == full.image_ids() and light.point_ids() == full.point_ids()
    assert light.effective_times() == full.effective_times()
    assert light.effective_points_t() == full.effective_points_t()
    assert model.validate_full(MIN_SCENE) == validate.validate(
        MIN_SCENE, image_ids=full.image_ids(), point_ids=full.point_ids()
    )


def test_writers_refuse_partially_read_records(tmp_path):
    _synthetic_model(tmp_path)
    light = colmap_io.read_model(tmp_path, include_observations=False, include_tracks=False)
    with pytest.raises(ValueError, match="image 1 was read without"):
        colmap_io.write_images_bin(tmp_path / "out_images.bin", light.images)
    with pytest.raises(ValueError, match="point3D 5 was read without"):
        colmap_io.write_points3D_bin(tmp_path / "out_points.bin", light.points3D)


@pytest.mark.parametrize("stem", ["images", "points3D"])
@pytest.mark.parametrize("include", [True, False])
def test_truncated_binary_raises(tmp_path, stem, include):
    _synthetic_model(tmp_path)
    path = tmp_path / f"{stem}.bin"
    path.write_bytes(path.read_bytes()[:-4])
    with pytest.raises(struct.error):
        if stem == "images":
            colmap_io.read_images_bin(path, include_observations=include)
        else:
            colmap_io.read_points3D_bin(path, include_tracks=include)


def test_reader_restores_gc_state(tmp_path):
    _synthetic_model(tmp_path)
    assert gc.isenabled()
    colmap_io.read_model(tmp_path)
    assert gc.isenabled()
    gc.disable()
    try:
        colmap_io.read_model(tmp_path)
        assert not gc.isenabled()
    finally:
        gc.enable()
