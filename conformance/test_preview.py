"""Bounded preview access is additive; source data and existing readers stay intact."""

import struct
from pathlib import Path

import pytest

import colmap4d.preview as preview
from colmap4d.colmap_io import (
    Camera,
    Image,
    Point3D,
    read_images_bin,
    read_points3D_bin,
    write_cameras_bin,
    write_images_bin,
    write_points3D_bin,
)
from colmap4d.sidecar import read_points_t_bin, write_time_meta, write_times_bin


@pytest.fixture
def model(tmp_path):
    root = tmp_path / "bundle"
    sparse = root / "sparse" / "0"
    sparse.mkdir(parents=True)
    write_cameras_bin(
        sparse / "cameras.bin", {3: Camera(3, "PINHOLE", 640, 480, [500, 500, 320, 240])}
    )
    images = {
        i: Image(i, f"nested/{i}.png", 3, [1, 0, 0, 0], [i, 0, 0], [(1, 2)] * i, [10] * i)
        for i in range(1, 5)
    }
    write_images_bin(sparse / "images.bin", images)
    points = {
        10 + i: Point3D(
            10 + i, [i, i + 0.25, -i], [i, 20, 255], 0.1, [(1, j) for j in range(i % 3)]
        )
        for i in range(7)
    }
    write_points3D_bin(sparse / "points3D.bin", points)
    ns = 1700000000000000001
    write_times_bin(sparse / "times.bin", {i: ns + i for i in images})
    pairs = [(16, ns + 16), (10, ns + 10), (13, ns), (13, ns + 13)]
    (sparse / "points_t.bin").write_bytes(
        struct.pack("<Q", len(pairs)) + b"".join(struct.pack("<Qq", *pair) for pair in pairs)
    )
    write_time_meta(sparse / "time_meta.json", {"conversion": {"estimated": True}})
    (root / "images" / "nested").mkdir(parents=True)
    (root / "images" / "nested" / "1.png").write_bytes(b"pixels")
    (root / "images" / "unused.png").write_bytes(b"unused")
    return root, sparse, ns


def test_component_stat_includes_all_pixels_but_never_parses_tables(model, monkeypatch):
    root, sparse, _ = model
    monkeypatch.setattr(Path, "read_bytes", lambda _: pytest.fail("stat must not read bytes"))
    result = preview.inspect_components(root)
    assert result.images_bytes == 12
    assert result.points_bytes == sum(
        (sparse / name).stat().st_size for name in ("points3D.bin", "points_t.bin")
    )
    assert result.cameras_bytes + result.points_bytes == sum(result.files.values())
    assert preview.inspect_components(sparse).images_bytes == 12
    (root / "images" / "link").symlink_to(root / "images" / "unused.png")
    assert preview.inspect_components(root).images_bytes is None


def test_cameras_are_separate_and_paged_without_points_or_observations(model, monkeypatch):
    root, sparse, ns = model
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert not path.name.startswith("points")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    page = preview.read_camera_page(root, offset=1, limit=2)
    assert list(page.images) == [2, 3]
    assert (page.total, page.next_offset) == (4, 3)
    assert page.times == {2: ns + 2, 3: ns + 3}
    assert all(im.xys is None and im.point3D_ids is None for im in page.images.values())
    (sparse / "points3D.bin").unlink()
    (sparse / "points_t.bin").unlink()
    assert preview.preview_files(root, part="cameras").shown == 0


@pytest.mark.parametrize(
    "budget,expected",
    [(1, [16]), (3, [11, 13, 14]), (5, [10, 11, 12, 14, 16]), (100, list(range(10, 17)))],
)
def test_jitter_samples_only_decode_chosen_geometry(model, monkeypatch, budget, expected):
    root, _, ns = model
    decoded = []
    original = preview._POINT

    class Decoder:
        @staticmethod
        def unpack_from(data, offset=0):
            decoded.append(offset)
            return original.unpack_from(data, offset)

    monkeypatch.setattr(preview, "_POINT", Decoder())
    monkeypatch.setattr(Path, "read_bytes", lambda _: pytest.fail("no full-file materialization"))
    sample = preview.sample_points(root, budget=budget)
    assert [p.id for p in sample.points] == expected
    assert len(decoded) == len(expected) <= budget
    assert sample.total == 7 and sample.next_offset is None
    assert all(p.track is None for p in sample.points)
    assert sample.times == {i: ns + i for i in expected if i in {10, 13, 16}}


def test_point_page_and_unbounded_times(model):
    root, _, _ = model
    page = preview.read_point_page(root, offset=1, limit=3)
    assert [p.id for p in page.points] == [11, 12, 13]
    assert page.next_offset == 4
    assert 11 not in page.times  # timeless stays absent, not zero/synthetic
    assert preview.read_point_page(root, offset=100, limit=2).points == []


def test_files_are_regular_colmap_with_bounded_tracks_and_exact_timestamps(model, tmp_path):
    root, _, ns = model
    points = preview.preview_files(root, part="points", budget=3)
    assert points.total == 7 and points.shown == 3
    assert len(points.files["points3D.bin"]) == 8 + 3 * 51
    for name, data in points.files.items():
        (tmp_path / name).write_bytes(data)
    assert list(read_points3D_bin(tmp_path / "points3D.bin")) == [11, 13, 14]
    assert read_points_t_bin(tmp_path / "points_t.bin") == {13: ns + 13}
    cameras = preview.preview_files(root, part="cameras")
    for name, data in cameras.files.items():
        (tmp_path / name).write_bytes(data)
    assert cameras.estimated and cameras.camera_bounds["center"] == [-2.5, 0, 0]
    assert len(read_images_bin(tmp_path / "images.bin")) == 4
    assert read_points3D_bin(tmp_path / "points3D.bin") == {}
    assert preview.preview_files(root, part="points", offset=2, limit=2).next_offset == 4


def test_text_fallback_empty_observations_and_large_ids(tmp_path):
    (tmp_path / "cameras.txt").write_text("3 PINHOLE 640 480 500 500 320 240\n")
    (tmp_path / "images.txt").write_text("# comment\n1 1 0 0 0 0 0 0 3 nested/图像.png\n\n")
    pid, ns = 2**53 + 1, 1700000000000000001
    (tmp_path / "points3D.txt").write_text(f"{pid} 1 2 3 4 5 6 .1 1 0\n2 2 3 4 5 6 7 .2\n")
    (tmp_path / "points_t.txt").write_text(f"{pid} {ns}\n")
    assert preview.read_camera_page(tmp_path).images[1].name == "nested/图像.png"
    sample = preview.sample_points(tmp_path, budget=2)
    assert [p.id for p in sample.points] == [pid, 2]
    assert sample.times == {pid: ns}
    (tmp_path / "points3D.txt").write_text("# no points\n")
    assert preview.sample_points(tmp_path).points == []


@pytest.mark.parametrize("offset,limit", [(-1, 2), (0, 0), (0, True), ("0", 1), (0, 1.5)])
def test_invalid_page_bounds(model, offset, limit):
    with pytest.raises(ValueError):
        preview.read_point_page(model[0], offset=offset, limit=limit)


@pytest.mark.parametrize("stem", ["points3D.bin", "points_t.bin", "images.bin"])
def test_truncated_binary_tables_fail_explicitly(model, stem):
    root, sparse, _ = model
    path = sparse / stem
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError):
        if stem == "images.bin":
            preview.read_camera_page(root)
        else:
            # Include every row so the truncated tail is part of the requested data.
            preview.sample_points(root, budget=100)


def test_empty_binary_and_unknown_component(tmp_path):
    (tmp_path / "points3D.bin").write_bytes(struct.pack("<Q", 0))
    assert preview.sample_points(tmp_path).points == []
    with pytest.raises(ValueError, match="part"):
        preview.preview_files(tmp_path, part="invalid")


def test_sampling_repeats_without_global_rng_state_and_accepts_explicit_seed(model):
    import random

    root = model[0]
    state = random.getstate()
    first = preview.sample_points(root, budget=3)
    assert first == preview.sample_points(root, budget=3)
    assert first == preview.sample_points(root, budget=3, seed=0)
    assert first != preview.sample_points(root, budget=3, seed=1)
    assert random.getstate() == state
    assert preview.preview_files(root, part="points", budget=3, seed=0) == (
        preview.preview_files(root, part="points", budget=3, seed=0)
    )
    assert preview.preview_files(root, part="points", budget=3, seed=0).files != (
        preview.preview_files(root, part="points", budget=3, seed=1).files
    )


def test_jitter_breaks_periodic_spatial_aliasing_with_exact_count(tmp_path):
    # A raster-ordered cloud: fixed stride 20 only visits 32 of 128 columns.
    table = tmp_path / "points3D.bin"
    points = {i + 1: Point3D(i + 1, [i % 128, i // 128, 0], [1, 2, 3], 0, []) for i in range(10000)}
    write_points3D_bin(table, points)
    batch = preview.sample_points(tmp_path, budget=500)
    ranks = [p.id - 1 for p in batch.points]
    assert len(ranks) == len(set(ranks)) == 500
    assert ranks == sorted(ranks)
    assert [rank // 20 for rank in ranks] == list(range(500))
    assert len({rank % 128 for rank in ranks}) > 100
    assert len({b - a for a, b in zip(ranks, ranks[1:])}) > 10


@pytest.mark.parametrize("seed", [True, "0", 0.5])
def test_invalid_seed_is_explicit(model, seed):
    with pytest.raises(ValueError, match="seed"):
        preview.sample_points(model[0], seed=seed)
