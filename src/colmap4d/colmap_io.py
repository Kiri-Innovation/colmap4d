"""Zero-dependency reader/writer for the COLMAP base model (cameras/images/points3D).

This is the pure-Python fallback so ``import colmap4d`` can read a timestamped model with no
compiled dependency. pycolmap remains the *preferred* backend where installed (it is
authoritative and also understands the 3.12 rigs/frames binary variants); this reader targets
the classic cameras/images/points3D text and binary layouts, which cover the vast majority of
models in the wild and every model colmap4d itself writes.

Binary layout follows COLMAP's ``reconstruction_io`` (little-endian, count-prefixed):
    cameras.bin  : u64 count, then [u32 id, i32 model_id, u64 w, u64 h, f64 params[k]]
    images.bin   : u64 count, then [u32 id, f64 qw qx qy qz, f64 tx ty tz, u32 cam_id,
                   name\\0, u64 n2d, [f64 x, f64 y, u64 point3D_id] * n2d]
    points3D.bin : u64 count, then [u64 id, f64 x y z, u8 r g b, f64 error,
                   u64 track_len, [u32 image_id, u32 point2D_idx] * track_len]

The binary readers decode each file from one in-memory buffer, slicing the fixed-size runs
(2D observations, tracks) in bulk instead of one ``struct`` call per element. Callers that only
need poses / point positions can skip the per-observation data with ``include_observations`` /
``include_tracks``; the skipped fields are then ``None`` rather than lists.
"""

from __future__ import annotations

import gc
import struct
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

# COLMAP camera model id <-> name and parameter counts.
_MODEL_ID_TO_NAME = {
    0: "SIMPLE_PINHOLE",
    1: "PINHOLE",
    2: "SIMPLE_RADIAL",
    3: "RADIAL",
    4: "OPENCV",
    5: "OPENCV_FISHEYE",
    6: "FULL_OPENCV",
    7: "FOV",
    8: "SIMPLE_RADIAL_FISHEYE",
    9: "RADIAL_FISHEYE",
    10: "THIN_PRISM_FISHEYE",
}
_MODEL_NAME_TO_ID = {v: k for k, v in _MODEL_ID_TO_NAME.items()}
_MODEL_NUM_PARAMS = {0: 3, 1: 4, 2: 4, 3: 5, 4: 8, 5: 8, 6: 12, 7: 5, 8: 4, 9: 5, 10: 12}

# COLMAP's sentinel for "this 2D point has no 3D point".
INVALID_POINT3D_ID = 18446744073709551615


@dataclass
class Camera:
    id: int
    model: str
    width: int
    height: int
    params: list[float]


@dataclass
class Image:
    id: int
    name: str
    camera_id: int
    qvec: list[float]  # [qw, qx, qy, qz]
    tvec: list[float]  # [tx, ty, tz]
    # Both are None when read with include_observations=False.
    xys: list[tuple[float, float]] | None = field(default_factory=list)
    point3D_ids: list[int] | None = field(default_factory=list)  # INVALID_POINT3D_ID where none


@dataclass
class Point3D:
    id: int
    xyz: list[float]
    rgb: list[int]
    error: float
    track: list[tuple[int, int]] | None  # (image_id, point2D_idx); None if not read


@dataclass
class BaseModel:
    cameras: dict[int, Camera] = field(default_factory=dict)
    images: dict[int, Image] = field(default_factory=dict)
    points3D: dict[int, Point3D] = field(default_factory=dict)

    def image_ids(self) -> set[int]:
        return set(self.images)

    def point_ids(self) -> set[int]:
        return set(self.points3D)


# --------------------------------------------------------------------------- #
# binary helpers
# --------------------------------------------------------------------------- #
def _r(f, fmt: str):
    s = struct.Struct("<" + fmt)
    return s.unpack(f.read(s.size))


def _w(f, fmt: str, *vals) -> None:
    f.write(struct.pack("<" + fmt, *vals))


_U64 = struct.Struct("<Q")
_IMAGE_HEAD = struct.Struct("<IdddddddI")
_OBSERVATION = struct.Struct("<ddQ")
_POINT_HEAD = struct.Struct("<QdddBBBdQ")
_TRACK_ELEM = struct.Struct("<II")
# memoryview.cast decodes in native byte order, which matches the file only on little-endian.
_NATIVE_LITTLE_ENDIAN = sys.byteorder == "little"


@contextmanager
def _gc_paused() -> Iterator[None]:
    """Pause the cyclic GC while building millions of acyclic records; repeated collections
    over the growing heap otherwise cost more than the parsing itself."""
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


def _span(data: bytes, off: int, size: int) -> int:
    """End offset of ``size`` bytes at ``off``; raise like a short ``struct`` read if past EOF."""
    end = off + size
    if end > len(data):
        raise struct.error(f"unpack requires a buffer of {size} bytes")
    return end


def _observations(mv: memoryview, off: int, n2d: int) -> tuple[list, list]:
    """``(xys, point3D_ids)`` from ``n2d`` packed ``[f64 x, f64 y, u64 id]`` records."""
    block = mv[off : off + n2d * _OBSERVATION.size]
    if _NATIVE_LITTLE_ENDIAN:
        coords = block.cast("d")
        xys = list(zip(coords[0::3].tolist(), coords[1::3].tolist()))
        return xys, block.cast("Q")[2::3].tolist()
    rows = list(_OBSERVATION.iter_unpack(block))
    return [(x, y) for x, y, _ in rows], [pid for _, _, pid in rows]


def _require_loaded(value, what: str, record_id: int):
    if value is None:
        raise ValueError(f"{what} {record_id} was read without that data; cannot write it")
    return value


# --------------------------------------------------------------------------- #
# text parsing helpers
# --------------------------------------------------------------------------- #
def _data_lines(path: str | Path) -> list[str]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


# --------------------------------------------------------------------------- #
# cameras
# --------------------------------------------------------------------------- #
def read_cameras_txt(path: str | Path) -> dict[int, Camera]:
    cams = {}
    for line in _data_lines(path):
        p = line.split()
        cid = int(p[0])
        cams[cid] = Camera(cid, p[1], int(p[2]), int(p[3]), [float(x) for x in p[4:]])
    return cams


def read_cameras_bin(path: str | Path) -> dict[int, Camera]:
    cams = {}
    with open(path, "rb") as f:
        (n,) = _r(f, "Q")
        for _ in range(n):
            cid, model_id, w, h = _r(f, "IiQQ")
            k = _MODEL_NUM_PARAMS[model_id]
            params = list(_r(f, f"{k}d"))
            cams[cid] = Camera(cid, _MODEL_ID_TO_NAME[model_id], w, h, params)
    return cams


def write_cameras_bin(path: str | Path, cams: dict[int, Camera]) -> None:
    with open(path, "wb") as f:
        _w(f, "Q", len(cams))
        for cid in sorted(cams):
            c = cams[cid]
            _w(f, "IiQQ", cid, _MODEL_NAME_TO_ID[c.model], c.width, c.height)
            _w(f, f"{len(c.params)}d", *c.params)


# --------------------------------------------------------------------------- #
# images
# --------------------------------------------------------------------------- #
def read_images_txt(path: str | Path, *, include_observations: bool = True) -> dict[int, Image]:
    lines = _data_lines(path)
    images = {}
    for i in range(0, len(lines), 2):
        head = lines[i].split()
        iid = int(head[0])
        qvec = [float(x) for x in head[1:5]]
        tvec = [float(x) for x in head[5:8]]
        cam_id = int(head[8])
        name = head[9]
        if not include_observations:
            images[iid] = Image(iid, name, cam_id, qvec, tvec, None, None)
            continue
        xys, ids = [], []
        pts = lines[i + 1].split() if i + 1 < len(lines) else []
        for j in range(0, len(pts), 3):
            xys.append((float(pts[j]), float(pts[j + 1])))
            pid = int(pts[j + 2])
            ids.append(INVALID_POINT3D_ID if pid < 0 else pid)
        images[iid] = Image(iid, name, cam_id, qvec, tvec, xys, ids)
    return images


def read_images_bin(path: str | Path, *, include_observations: bool = True) -> dict[int, Image]:
    """Read images.bin. With ``include_observations=False`` the 2D observations are skipped
    and ``xys`` / ``point3D_ids`` are ``None``."""
    data = Path(path).read_bytes()
    mv = memoryview(data)
    images = {}
    (n,) = _U64.unpack_from(data, 0)
    off = _U64.size
    with _gc_paused():
        for _ in range(n):
            iid, qw, qx, qy, qz, tx, ty, tz, cam_id = _IMAGE_HEAD.unpack_from(data, off)
            off += _IMAGE_HEAD.size
            name_end = data.find(b"\x00", off)
            if name_end < 0:
                name_end = len(data)
            name = data[off:name_end].decode("utf-8")
            off = name_end + 1
            (n2d,) = _U64.unpack_from(data, off)
            off += _U64.size
            end = _span(data, off, n2d * _OBSERVATION.size)
            xys, ids = _observations(mv, off, n2d) if include_observations else (None, None)
            off = end
            images[iid] = Image(iid, name, cam_id, [qw, qx, qy, qz], [tx, ty, tz], xys, ids)
    return images


def write_images_bin(path: str | Path, images: dict[int, Image]) -> None:
    with open(path, "wb") as f:
        _w(f, "Q", len(images))
        for iid in sorted(images):
            im = images[iid]
            _w(f, "Iddddddd", iid, *im.qvec, *im.tvec)
            _w(f, "I", im.camera_id)
            f.write(im.name.encode("utf-8") + b"\x00")
            xys = _require_loaded(im.xys, "image", iid)
            _w(f, "Q", len(xys))
            for (x, y), pid in zip(xys, im.point3D_ids):
                _w(f, "ddQ", x, y, pid)


# --------------------------------------------------------------------------- #
# points3D
# --------------------------------------------------------------------------- #
def read_points3D_txt(path: str | Path, *, include_tracks: bool = True) -> dict[int, Point3D]:
    pts = {}
    for line in _data_lines(path):
        p = line.split()
        pid = int(p[0])
        xyz = [float(x) for x in p[1:4]]
        rgb = [int(x) for x in p[4:7]]
        error = float(p[7])
        rest = p[8:]
        track = (
            [(int(rest[k]), int(rest[k + 1])) for k in range(0, len(rest), 2)]
            if include_tracks
            else None
        )
        pts[pid] = Point3D(pid, xyz, rgb, error, track)
    return pts


def read_points3D_bin(path: str | Path, *, include_tracks: bool = True) -> dict[int, Point3D]:
    """Read points3D.bin. With ``include_tracks=False`` the tracks are skipped and
    ``track`` is ``None``."""
    data = Path(path).read_bytes()
    mv = memoryview(data)
    head = _POINT_HEAD.unpack_from
    head_size = _POINT_HEAD.size
    track_rows = _TRACK_ELEM.iter_unpack
    size = len(data)
    pts = {}
    (n,) = _U64.unpack_from(data, 0)
    off = _U64.size
    with _gc_paused():
        for _ in range(n):
            pid, x, y, z, r, g, b, error, tl = head(data, off)
            off += head_size
            end = off + tl * 8  # u32 image_id + u32 point2D_idx; inline: hot loop
            if end > size:
                _span(data, off, tl * 8)
            track = list(track_rows(mv[off:end])) if include_tracks else None
            off = end
            pts[pid] = Point3D(pid, [x, y, z], [r, g, b], error, track)
    return pts


def write_points3D_bin(path: str | Path, pts: dict[int, Point3D]) -> None:
    with open(path, "wb") as f:
        _w(f, "Q", len(pts))
        for pid in sorted(pts):
            p = pts[pid]
            track = _require_loaded(p.track, "point3D", pid)
            _w(f, "QdddBBBdQ", pid, *p.xyz, *p.rgb, p.error, len(track))
            for img_id, idx in track:
                _w(f, "II", img_id, idx)


# --------------------------------------------------------------------------- #
# whole model
# --------------------------------------------------------------------------- #
def read_model(
    model_dir: str | Path, *, include_observations: bool = True, include_tracks: bool = True
) -> BaseModel:
    """Read cameras/images/points3D from a COLMAP model dir. Binary is preferred over text
    when both are present (matching COLMAP).

    ``include_observations=False`` skips the per-image 2D observations and
    ``include_tracks=False`` the per-point tracks (those fields are then ``None``); use them
    when only poses, point positions and ids are needed — they dominate time and memory."""
    d = Path(model_dir)

    def pick(stem: str):
        if (d / f"{stem}.bin").exists():
            return d / f"{stem}.bin", "bin"
        if (d / f"{stem}.txt").exists():
            return d / f"{stem}.txt", "txt"
        raise FileNotFoundError(f"no {stem}.bin or {stem}.txt in {d}")

    cpath, ck = pick("cameras")
    ipath, ik = pick("images")
    ppath, pk = pick("points3D")
    read_images = read_images_bin if ik == "bin" else read_images_txt
    read_points = read_points3D_bin if pk == "bin" else read_points3D_txt
    return BaseModel(
        cameras=read_cameras_bin(cpath) if ck == "bin" else read_cameras_txt(cpath),
        images=read_images(ipath, include_observations=include_observations),
        points3D=read_points(ppath, include_tracks=include_tracks),
    )
