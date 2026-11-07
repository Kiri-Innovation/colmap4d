"""Bounded, read-only access to COLMAP/colmap4d components.

No HTTP, viewer, platform, NumPy or pycolmap dependency. Preview records are
ordered by source row, not ID. Observations and tracks are deliberately omitted.
Partial reads are not full-model validation; use the existing validators for that.
"""

from __future__ import annotations

import json
import math
import random
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from colmap4d.colmap_io import Camera, Image, Point3D, read_cameras_bin, read_cameras_txt

_U64 = struct.Struct("<Q")
_IMAGE = struct.Struct("<IdddddddI")
_POINT = struct.Struct("<QdddBBBdQ")
_MAX_SMALL = 16 * 1024 * 1024


@dataclass
class ComponentSizes:
    """Source byte sizes, not decoded-memory estimates; absent components are zero."""

    files: dict[str, int]
    cameras_bytes: int
    points_bytes: int
    images_bytes: int | None
    points_geometry_bytes: int
    points_time_bytes: int
    image_size_error: str | None = None


@dataclass
class CameraPage:
    cameras: dict[int, Camera]
    images: dict[int, Image]
    times: dict[int, int]
    time_meta: dict
    total: int
    offset: int
    next_offset: int | None


@dataclass
class PointBatch:
    points: list[Point3D]
    times: dict[int, int]
    total: int
    offset: int
    next_offset: int | None


@dataclass
class PreviewFiles:
    """Small ordinary COLMAP files suitable for any existing COLMAP reader.

    Empty tracks/observations and selected time sidecars make this a display
    subset, not an authoritative reconstruction export. Missing point times
    remain absent (temporally unbounded). All integer IDs/ns remain integers.
    """

    files: dict[str, bytes]
    total: int
    shown: int
    image_names: list[str]
    estimated: bool
    camera_bounds: dict | None
    next_offset: int | None


def _directory(path: str | Path) -> Path:
    root = Path(path)
    for candidate in (root / "sparse" / "0", root / "sparse", root):
        if any(
            (candidate / f"{stem}.{ext}").is_file()
            for stem in ("cameras", "images", "points3D")
            for ext in ("bin", "txt")
        ):
            return candidate
    raise FileNotFoundError(f"No COLMAP component tables in {root}")


def _table(root: Path, stem: str, *, optional: bool = False) -> Path | None:
    for ext in ("bin", "txt"):
        path = root / f"{stem}.{ext}"
        if path.is_file():
            return path
    if optional:
        return None
    raise FileNotFoundError(f"Missing {stem} in {root}")


def inspect_components(path: str | Path) -> ComponentSizes:
    """Stat component tables and every pixel file, without parsing table bodies.

    ``path`` can be a bundle root, sparse directory, or split component root.
    Pixel symlinks/IO errors yield an unknown size rather than an underestimate.
    """
    root = Path(path)
    sparse = _directory(root)
    files = {}
    for stem in ("cameras", "images", "times", "points3D", "points_t"):
        table = _table(sparse, stem, optional=True)
        if table is not None:
            files[table.name] = table.stat().st_size
    meta = sparse / "time_meta.json"
    if meta.is_file():
        files[meta.name] = meta.stat().st_size
    cameras = sum(n for name, n in files.items() if not name.startswith(("points3D.", "points_t.")))
    geometry = sum(n for name, n in files.items() if name.startswith("points3D."))
    point_times = sum(n for name, n in files.items() if name.startswith("points_t."))
    points = geometry + point_times
    pixels = root / "images"
    if not pixels.exists() and root.name == "0" and root.parent.name == "sparse":
        pixels = root.parent.parent / "images"
    elif not pixels.exists() and root.name == "sparse":
        pixels = root.parent / "images"
    pixel_bytes = 0
    try:
        if pixels.is_symlink():
            raise ValueError("Pixel directory contains a symlink")
        if pixels.exists():
            for child in pixels.rglob("*"):
                if child.is_symlink():
                    raise ValueError("Pixel directory contains a symlink")
                if child.is_file():
                    pixel_bytes += child.stat().st_size
    except (OSError, ValueError) as exc:
        return ComponentSizes(files, cameras, points, None, geometry, point_times, str(exc))
    return ComponentSizes(files, cameras, points, pixel_bytes, geometry, point_times)


def _read(stream, n: int) -> bytes:
    data = stream.read(n)
    if len(data) != n:
        raise ValueError("Truncated COLMAP table")
    return data


def _image_headers(path: Path):
    if path.suffix == ".txt":
        with path.open(encoding="utf-8") as stream:
            pose = True
            for line in stream:
                if line.lstrip().startswith("#"):
                    continue
                if pose:
                    if not line.strip():
                        continue
                    fields = line.split(maxsplit=9)
                    if len(fields) != 10:
                        raise ValueError("Invalid image header")
                    yield Image(
                        int(fields[0]),
                        fields[9].strip(),
                        int(fields[8]),
                        list(map(float, fields[1:5])),
                        list(map(float, fields[5:8])),
                        None,
                        None,
                    )
                pose = not pose
            if not pose:
                raise ValueError("Missing image observation row")
        return
    size = path.stat().st_size
    with path.open("rb") as stream:
        count = _U64.unpack(_read(stream, 8))[0]
        if count > max(size - 8, 0) // 73:
            raise ValueError("Invalid image count")
        for _ in range(count):
            iid, qw, qx, qy, qz, tx, ty, tz, camera = _IMAGE.unpack(_read(stream, 64))
            name = bytearray()
            while True:
                char = _read(stream, 1)
                if char == b"\0":
                    break
                name += char
                if len(name) > 65536:
                    raise ValueError("Image name exceeds preview budget")
            observations = _U64.unpack(_read(stream, 8))[0]
            end = stream.tell() + observations * 24
            if end > size:
                raise ValueError("Truncated image observations")
            stream.seek(end)
            yield Image(
                iid, name.decode("utf-8"), camera, [qw, qx, qy, qz], [tx, ty, tz], None, None
            )
        if stream.tell() != size:
            raise ValueError("Trailing image bytes")


def _selected_times(path: Path | None, ids: set[int], *, point: bool) -> dict[int, int]:
    if path is None or not ids:
        return {}
    result = {}
    if path.suffix == ".txt":
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                fields = line.split()
                if len(fields) != 2:
                    raise ValueError("Invalid timestamp row")
                key = int(fields[0])
                if key in ids:
                    result[key] = int(fields[1])
        return result
    width = 16 if point else 12
    key = struct.Struct("<Q" if point else "<I")
    size = path.stat().st_size
    if size < 8:
        raise ValueError("Truncated timestamp count")
    with path.open("rb") as stream:
        count = _U64.unpack(_read(stream, 8))[0]
        if size != 8 + count * width:
            raise ValueError("Invalid timestamp table length")
        remaining = count
        while remaining:
            take = min(32768, remaining)
            data = _read(stream, take * width)
            for offset in range(0, len(data), width):
                entity_id = key.unpack_from(data, offset)[0]
                if entity_id in ids:
                    result[entity_id] = struct.unpack_from("<q", data, offset + width - 8)[0]
            remaining -= take
    return result


def _bounds(offset: int, limit: int) -> None:
    if (
        not isinstance(offset, int)
        or not isinstance(limit, int)
        or isinstance(offset, bool)
        or isinstance(limit, bool)
        or offset < 0
        or limit < 1
    ):
        raise ValueError("offset must be a nonnegative integer and limit a positive integer")


def read_camera_page(path: str | Path, *, offset: int = 0, limit: int = 100_000) -> CameraPage:
    """Read a page of poses and their times; never open points or pixel bodies.

    Scans pose headers to determine the total; BIN observations are seeked over.
    All intrinsics are returned so each page is independently interpretable.
    """
    _bounds(offset, limit)
    root = _directory(path)
    camera_path = _table(root, "cameras")
    if camera_path.stat().st_size > _MAX_SMALL:
        raise ValueError("Camera table exceeds preview budget")
    cameras = (read_cameras_bin if camera_path.suffix == ".bin" else read_cameras_txt)(camera_path)
    images = {}
    total = 0
    retained_bytes = 8
    for row, image in enumerate(_image_headers(_table(root, "images"))):
        total = row + 1
        if offset <= row < offset + limit:
            retained_bytes += 73 + len(image.name.encode("utf-8"))
            if retained_bytes > _MAX_SMALL:
                raise ValueError("Camera page exceeds preview byte budget; request a smaller page")
            images[image.id] = image
    meta_path = root / "time_meta.json"
    if meta_path.is_file() and meta_path.stat().st_size > _MAX_SMALL:
        raise ValueError("Metadata exceeds preview budget")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    if not isinstance(meta, dict):
        raise ValueError("Invalid time_meta.json")
    times = _selected_times(_table(root, "times", optional=True), set(images), point=False)
    return CameraPage(
        cameras,
        images,
        times,
        meta,
        total,
        offset,
        offset + limit if offset + limit < total else None,
    )


def _text_rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip() and not line.lstrip().startswith("#"):
                yield line


def _point_count(path: Path) -> int:
    if path.suffix == ".txt":
        return sum(1 for _ in _text_rows(path))
    with path.open("rb") as stream:
        count = _U64.unpack(_read(stream, 8))[0]
    size = path.stat().st_size
    if count > max(size - 8, 0) // 51 or (count == 0 and size != 8):
        raise ValueError("Invalid point count")
    return count


def _points_at(path: Path, indices: list[int]) -> list[Point3D]:
    if not indices:
        return []
    result = []
    wanted = iter(indices)
    target = next(wanted)
    if path.suffix == ".txt":
        for row, line in enumerate(_text_rows(path)):
            if row != target:
                continue
            f = line.split(maxsplit=8)
            if len(f) < 8:
                raise ValueError("Invalid point row")
            result.append(
                Point3D(
                    int(f[0]), list(map(float, f[1:4])), list(map(int, f[4:7])), float(f[7]), None
                )
            )
            target = next(wanted, None)
            if target is None:
                break
        if target is not None:
            raise ValueError("Truncated point table")
        return result
    size = path.stat().st_size
    with path.open("rb") as stream:
        count = _U64.unpack(_read(stream, 8))[0]
        offset = 8
        for row in range(indices[-1] + 1):
            header = _read(stream, 51)
            tracks = _U64.unpack_from(header, 43)[0]
            end = offset + 51 + tracks * 8
            if end > size:
                raise ValueError("Truncated point tracks")
            if row == target:
                pid, x, y, z, r, g, b, error, _ = _POINT.unpack_from(header)
                result.append(Point3D(pid, [x, y, z], [r, g, b], error, None))
                target = next(wanted, None)
            if tracks:
                stream.seek(tracks * 8, 1)
            offset = end
        if indices[-1] == count - 1 and offset != size:
            raise ValueError("Trailing point bytes")
    return result


def _point_batch(path: str | Path, offset: int, indices_fn) -> PointBatch:
    root = _directory(path)
    table = _table(root, "points3D")
    total = _point_count(table)
    indices = indices_fn(total)
    points = _points_at(table, indices)
    times = _selected_times(
        _table(root, "points_t", optional=True), {p.id for p in points}, point=True
    )
    next_offset = indices[-1] + 1 if indices and indices[-1] + 1 < total else None
    return PointBatch(points, times, total, offset, next_offset)


def read_point_page(path: str | Path, *, offset: int = 0, limit: int = 200_000) -> PointBatch:
    """Read consecutive source rows without decoding skipped geometry/tracks.

    Variable-length BIN tables require walking preceding track-count headers;
    this is O(offset + limit) header work, not constant-time random access.
    TXT counts rows in a separate streaming pass. No persistent index is built.
    """
    _bounds(offset, limit)
    return _point_batch(path, offset, lambda n: list(range(min(offset, n), min(offset + limit, n))))


def sample_points(path: str | Path, *, budget: int = 200_000, seed: int = 0) -> PointBatch:
    """Stratified jitter sample of exactly ``min(total, budget)`` source rows.

    Divide row order into disjoint integer strata and choose one row uniformly
    inside each stratum, independently. This breaks fixed-stride aliasing without
    count fluctuations, duplicate rows, or forced endpoints. Small models keep
    all rows. Unequal stratum widths differ by at most one row; this is stratified
    coverage, not an independent equal-probability Bernoulli sample of all rows.

    A fresh local Random(seed) makes repeated calls reproducible in the same
    Python runtime without touching global RNG state. Only selected geometry is
    decoded; tracks are skipped. Timestamp joining and pagination are unchanged.
    """
    _bounds(0, budget)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")

    def indices(n):
        take = min(n, budget)
        if take == n:
            return list(range(n))
        rng = random.Random(seed)
        return [rng.randrange(i * n // take, (i + 1) * n // take) for i in range(take)]

    batch = _point_batch(path, 0, indices)
    batch.next_offset = None
    return batch


def _time_bytes(times: dict[int, int], *, point: bool) -> bytes:
    record = struct.Struct("<Qq" if point else "<Iq")
    return _U64.pack(len(times)) + b"".join(record.pack(k, v) for k, v in sorted(times.items()))


def _camera_bounds(images: dict[int, Image]) -> dict | None:
    positions = []
    for image in images.values():
        w, x, y, z = image.qvec
        tx, ty, tz = image.tvec
        p = [
            -(
                (1 - 2 * y * y - 2 * z * z) * tx
                + (2 * x * y + 2 * w * z) * ty
                + (2 * x * z - 2 * w * y) * tz
            ),
            -(
                (2 * x * y - 2 * w * z) * tx
                + (1 - 2 * x * x - 2 * z * z) * ty
                + (2 * y * z + 2 * w * x) * tz
            ),
            -(
                (2 * x * z + 2 * w * y) * tx
                + (2 * y * z - 2 * w * x) * ty
                + (1 - 2 * x * x - 2 * y * y) * tz
            ),
        ]
        if all(math.isfinite(v) for v in p):
            positions.append(p)
    if not positions:
        return None
    center = [sum(p[i] for p in positions) / len(positions) for i in range(3)]
    radius = max(math.sqrt(sum((p[i] - center[i]) ** 2 for i in range(3))) for p in positions)
    return {"center": center, "radius": radius}


def preview_files(
    path: str | Path,
    *,
    part: Literal["cameras", "points"],
    budget: int = 200_000,
    seed: int = 0,
    offset: int = 0,
    limit: int | None = None,
) -> PreviewFiles:
    """Encode only the requested component as a bounded ordinary COLMAP fileset.

    Points default to seeded stratified jitter; supplying ``limit`` selects consecutive
    pagination instead. Cameras default to a 100,000-pose page. No source writes.
    """
    if part == "points":
        batch = (
            sample_points(path, budget=budget, seed=seed)
            if limit is None
            else read_point_page(path, offset=offset, limit=limit)
        )
        points = _U64.pack(len(batch.points)) + b"".join(
            _POINT.pack(p.id, *p.xyz, *p.rgb, p.error, 0) for p in batch.points
        )
        return PreviewFiles(
            {"points3D.bin": points, "points_t.bin": _time_bytes(batch.times, point=True)},
            batch.total,
            len(batch.points),
            [],
            False,
            None,
            batch.next_offset,
        )
    if part != "cameras":
        raise ValueError("part must be cameras or points")
    page = read_camera_page(path, offset=offset, limit=100_000 if limit is None else limit)
    root = _directory(path)
    table = _table(root, "cameras")
    images = _U64.pack(len(page.images)) + b"".join(
        _IMAGE.pack(im.id, *im.qvec, *im.tvec, im.camera_id)
        + im.name.encode("utf-8")
        + b"\0"
        + _U64.pack(0)
        for im in page.images.values()
    )
    conversion = page.time_meta.get("conversion")
    conversion = conversion if isinstance(conversion, dict) else {}
    estimated = conversion.get("estimated") is True or (
        "synthetic" in str(conversion.get("timestamp_mode", ""))
    )
    return PreviewFiles(
        {
            table.name: table.read_bytes(),
            "images.bin": images,
            "points3D.bin": _U64.pack(0),
            "times.bin": _time_bytes(page.times, point=False),
            "time_meta.json": json.dumps(page.time_meta).encode("utf-8"),
        },
        page.total,
        0,
        [im.name for im in page.images.values()],
        estimated,
        _camera_bounds(page.images),
        page.next_offset,
    )
