"""Optional compute API preserves reference-reader values and ID semantics."""

import struct

import pytest

from colmap4d.arrays import read_point_arrays
from colmap4d.colmap_io import Point3D, read_points3D_bin, write_points3D_bin

np = pytest.importorskip("numpy")


@pytest.mark.parametrize("tracks", [[], [(1, 2)], [(1, 2), (3, 4)]])
def test_uniform_and_sorted(tmp_path, tracks):
    path = tmp_path / "points3D.bin"
    points = {
        3: Point3D(3, [0.1, 2.123456789, -3], [0, 255, 17], 0, tracks),
        1: Point3D(1, [5, 6, 7], [1, 2, 3], 0, tracks),
    }
    write_points3D_bin(path, points)
    ids, xyz, rgb = read_point_arrays(path)
    assert ids.tolist() == [1, 3]
    assert xyz.dtype == np.float64
    np.testing.assert_array_equal(xyz, [points[i].xyz for i in ids])
    np.testing.assert_array_equal(rgb, [points[i].rgb for i in ids])


def test_variable_duplicate_last_wins(tmp_path):
    path = tmp_path / "points3D.bin"
    rows = [(9, 1, []), (2, 2, [(1, 1)]), (9, 3, [(1, 1), (2, 2)])]
    data = struct.pack("<Q", len(rows))
    for pid, x, tracks in rows:
        data += struct.pack("<QdddBBBdQ", pid, x, 0, 0, 10, 20, 30, 0, len(tracks))
        data += b"".join(struct.pack("<II", *t) for t in tracks)
    path.write_bytes(data)
    ref = read_points3D_bin(path)
    ids, xyz, rgb = read_point_arrays(path)
    assert ids.tolist() == sorted(ref)
    np.testing.assert_array_equal(xyz, [ref[i].xyz for i in ids])
    np.testing.assert_array_equal(rgb, [ref[i].rgb for i in ids])
    for length in (0, 7, len(data) - 1):
        path.write_bytes(data[:length])
        with pytest.raises(ValueError):
            read_point_arrays(path)


def test_empty_and_text(tmp_path):
    path = tmp_path / "points3D.bin"
    path.write_bytes(struct.pack("<Q", 0))
    assert read_point_arrays(path)[1].shape == (0, 3)
    path = tmp_path / "points3D.txt"
    path.write_text("# points\n3 0.1 2 3 1 2 255 0\n")
    ids, xyz, rgb = read_point_arrays(path)
    assert ids.tolist() == [3]
    np.testing.assert_array_equal(xyz, [[0.1, 2, 3]])
    np.testing.assert_array_equal(rgb, [[1, 2, 255]])


def test_image_header_seek_matches_reference(tmp_path):
    from colmap4d.arrays import read_image_headers
    from colmap4d.colmap_io import Image, read_images_bin, write_images_bin

    path = tmp_path / "images.bin"
    images = {
        5: Image(5, "nested/图像.png", 3, [1, 0, 0, 0], [0.1, 0.2, 0.3], [(1, 2), (3, 4)], [1, 2])
    }
    write_images_bin(path, images)
    assert read_image_headers(path) == read_images_bin(path, include_observations=False)
    data = path.read_bytes()
    for length in (0, 7, len(data) - 1):
        path.write_bytes(data[:length])
        with pytest.raises(ValueError):
            read_image_headers(path)
