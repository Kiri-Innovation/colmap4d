"""Optional NumPy point reader. No change to the COLMAP/colmap4d storage protocol.

Tracks are bounds-checked but never materialized. Arrays retain float64 XYZ and
uint8 RGB; callers choose their compute precision. Records are returned in sorted
id order with the same last-record-wins behavior as the object reader.
"""

from __future__ import annotations

import struct
from pathlib import Path


def read_point_arrays(path: str | Path):
    """Return ``(ids[u64], xyz[f64,N,3], rgb[u8,N,3])``; NumPy is optional.

    Uniform-track binary files (including dense triangulator output) use a
    validated strided view. Other binary files use an offset scan, not objects.
    Text files retain the reference reader's semantics.
    """
    import numpy as np

    from colmap4d.colmap_io import read_points3D_txt

    path = Path(path)
    if path.suffix == ".txt":
        points = read_points3D_txt(path, include_tracks=False)
        ids = np.array(sorted(points), dtype=np.uint64)
        return (
            ids,
            np.array([points[int(i)].xyz for i in ids], dtype=np.float64).reshape(-1, 3),
            np.array([points[int(i)].rgb for i in ids], dtype=np.uint8).reshape(-1, 3),
        )
    data = path.read_bytes()
    if len(data) < 8:
        raise ValueError(f"truncated point count: {path}")
    count = struct.unpack_from("<Q", data)[0]
    payload = len(data) - 8
    if count > payload // 51:
        raise ValueError(f"truncated points: {path}")
    if not count:
        if payload:
            raise ValueError(f"trailing point bytes: {path}")
        return (
            np.empty(0, dtype=np.uint64),
            np.empty((0, 3), dtype=np.float64),
            np.empty((0, 3), dtype=np.uint8),
        )
    stride, rem = divmod(payload, count)
    records = None
    if not rem and stride >= 51 and (stride - 51) % 8 == 0:
        dtype = np.dtype(
            {
                "names": ["id", "xyz", "rgb", "tracks"],
                "formats": ["<u8", ("<f8", 3), ("u1", 3), "<u8"],
                "offsets": [0, 8, 32, 43],
                "itemsize": stride,
            }
        )
        candidate = np.frombuffer(data, dtype=dtype, count=count, offset=8)
        if np.all(candidate["tracks"] == (stride - 51) // 8):
            records = candidate
    if records is not None:
        ids, xyz, rgb = records["id"].copy(), records["xyz"].copy(), records["rgb"].copy()
    else:
        ids = np.empty(count, dtype=np.uint64)
        xyz = np.empty((count, 3), dtype=np.float64)
        rgb = np.empty((count, 3), dtype=np.uint8)
        head = struct.Struct("<QdddBBBdQ").unpack_from
        off = 8
        for row in range(count):
            if off + 51 > len(data):
                raise ValueError(f"truncated point header: {path}")
            pid, x, y, z, r, g, b, _error, tracks = head(data, off)
            off += 51 + tracks * 8
            if off > len(data):
                raise ValueError(f"truncated point track: {path}")
            ids[row], xyz[row], rgb[row] = pid, (x, y, z), (r, g, b)
        if off != len(data):
            raise ValueError(f"trailing point bytes: {path}")
    if len(ids) > 1 and not np.all(ids[1:] > ids[:-1]):
        # Stable sorting lets the last duplicate match the reference dictionary.
        order = np.argsort(ids, kind="stable")
        ordered = ids[order]
        keep = np.r_[ordered[1:] != ordered[:-1], True]
        order = order[keep]
        ids, xyz, rgb = ids[order], xyz[order], rgb[order]
    return ids, xyz, rgb


def read_image_headers(path: str | Path):
    """Read poses/names without reading observation payloads from the filesystem."""
    from colmap4d.colmap_io import Image, read_images_txt

    path = Path(path)
    if path.suffix == ".txt":
        return read_images_txt(path, include_observations=False)
    size = path.stat().st_size
    images = {}
    head = struct.Struct("<IdddddddI")
    with path.open("rb") as stream:
        data = stream.read(8)
        if len(data) != 8:
            raise ValueError(f"truncated image count: {path}")
        count = struct.unpack("<Q", data)[0]
        if count > max(size - 8, 0) // (head.size + 9):
            raise ValueError(f"invalid image count: {path}")
        for _ in range(count):
            data = stream.read(head.size)
            if len(data) != head.size:
                raise ValueError(f"truncated image header: {path}")
            iid, qw, qx, qy, qz, tx, ty, tz, camera = head.unpack(data)
            name = bytearray()
            while True:
                char = stream.read(1)
                if not char:
                    raise ValueError(f"unterminated image name: {path}")
                if char == b"\0":
                    break
                name += char
            data = stream.read(8)
            if len(data) != 8:
                raise ValueError(f"truncated observation count: {path}")
            observations = struct.unpack("<Q", data)[0]
            end = stream.tell() + observations * 24
            if end > size:
                raise ValueError(f"truncated observations: {path}")
            stream.seek(end)
            images[iid] = Image(
                iid, name.decode("utf-8"), camera, [qw, qx, qy, qz], [tx, ty, tz], None, None
            )
        if stream.tell() != size:
            raise ValueError(f"trailing image bytes: {path}")
    return images
