"""Zero-dependency reader/writer for the colmap4d time sidecars.

This module handles ONLY the colmap4d delta over a standard COLMAP sparse model:

    times      -- per-image timestamps      (times.txt      / times.bin)
    points_t   -- per-point timestamps       (points_t.txt   / points_t.bin)
    time_meta  -- time-axis semantics/prov.  (time_meta.json)

It deliberately does NOT parse the COLMAP base model (cameras/images/points3D).
That belongs in ``model.py`` and is delegated to pycolmap (an optional dependency),
keeping "I just want to read the timestamps" a standard-library-only path.

Timestamps are int64 nanoseconds (see spec Part I.B). ``points_t`` is a PARTIAL map:
a point absent from it is *temporally-unbounded* and is represented here by simple
absence from the returned dict (see :func:`is_temporally_unbounded`).

Binary layout is defined normatively by the spec, section **I.E "Binary sidecar layout"**
(little-endian, uint64 count prefix; times record = uint32 image_id + int64 t_ns; points_t
record = uint64 point3d_id + int64 t_ns; id widths match COLMAP's image_t/point3D_t). This
module implements that section — the spec, not this docstring, is the source of truth.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

# Canonical sidecar file names, relative to a COLMAP sparse model directory.
TIMES_TXT = "times.txt"
TIMES_BIN = "times.bin"
POINTS_T_TXT = "points_t.txt"
POINTS_T_BIN = "points_t.bin"
TIME_META_JSON = "time_meta.json"

# Struct formats (little-endian). Count prefix is a uint64.
_COUNT = struct.Struct("<Q")
_TIMES_REC = struct.Struct("<Iq")  # uint32 image_id, int64 t_ns
_POINTS_T_REC = struct.Struct("<Qq")  # uint64 point3d_id, int64 t_ns

# Sentinel for a point with no timestamp (present at all t).
TIMELESS = None


# --------------------------------------------------------------------------- #
# raw pair parsing (duplicates preserved) + normative collapse to a dict
# --------------------------------------------------------------------------- #
def _parse_txt_pairs(path: str | Path) -> list[tuple[int, int]]:
    """All ``(ID, T_NS)`` rows of a text sidecar, in file order, dups preserved."""
    pairs: list[tuple[int, int]] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                raise ValueError(f"{path}: malformed line: {raw!r}")
            pairs.append((int(parts[0]), int(parts[1])))
    return pairs


def _parse_bin_pairs(path: str | Path, rec: struct.Struct) -> list[tuple[int, int]]:
    """All ``(ID, T_NS)`` records of a binary sidecar, in file order, dups preserved."""
    data = Path(path).read_bytes()
    (count,) = _COUNT.unpack_from(data, 0)
    off = _COUNT.size
    pairs: list[tuple[int, int]] = []
    for _ in range(count):
        key, t_ns = rec.unpack_from(data, off)
        pairs.append((key, t_ns))
        off += rec.size
    return pairs


def _pairs_to_dict(pairs: list[tuple[int, int]], strict: bool, path: str | Path) -> dict[int, int]:
    """Collapse raw pairs to id->t_ns. Duplicate ids resolve **last-wins** (spec Part I:
    a writer MUST NOT emit duplicates; a reader SHOULD accept and take the last one).
    ``strict=True`` opts into raising on a duplicate instead."""
    out: dict[int, int] = {}
    for key, t_ns in pairs:
        if strict and key in out:
            raise ValueError(f"{path}: duplicate id {key} (strict mode)")
        out[key] = t_ns  # last-wins
    return out


def _write_id_time_txt(path: str | Path, mapping: dict[int, int], header: str) -> None:
    lines = [header.rstrip("\n") + "\n"]
    for key in sorted(mapping):
        lines.append(f"{key} {mapping[key]}\n")
    Path(path).write_text("".join(lines), encoding="utf-8")


def _write_id_time_bin(path: str | Path, mapping: dict[int, int], rec: struct.Struct) -> None:
    buf = bytearray(_COUNT.pack(len(mapping)))
    for key in sorted(mapping):
        buf += rec.pack(key, mapping[key])
    Path(path).write_bytes(bytes(buf))


# --------------------------------------------------------------------------- #
# times
# --------------------------------------------------------------------------- #
def read_times_txt(path: str | Path, strict: bool = False) -> dict[int, int]:
    """image_id -> t_ns, from times.txt (last-wins on dup ids; see :func:`_pairs_to_dict`)."""
    return _pairs_to_dict(_parse_txt_pairs(path), strict, path)


def read_times_bin(path: str | Path, strict: bool = False) -> dict[int, int]:
    """image_id -> t_ns, from times.bin (last-wins on dup ids)."""
    return _pairs_to_dict(_parse_bin_pairs(path, _TIMES_REC), strict, path)


def read_times_pairs(path: str | Path) -> list[tuple[int, int]]:
    """Raw (image_id, t_ns) rows with duplicates preserved (for validation)."""
    if str(path).endswith(".bin"):
        return _parse_bin_pairs(path, _TIMES_REC)
    return _parse_txt_pairs(path)


def write_times_txt(path: str | Path, times: dict[int, int]) -> None:
    _write_id_time_txt(
        path,
        times,
        f"# colmap4d times: IMAGE_ID, T_NS (int64 ns)\n# Number of images: {len(times)}",
    )


def write_times_bin(path: str | Path, times: dict[int, int]) -> None:
    _write_id_time_bin(path, times, _TIMES_REC)


# --------------------------------------------------------------------------- #
# points_t
# --------------------------------------------------------------------------- #
def read_points_t_txt(path: str | Path, strict: bool = False) -> dict[int, int]:
    """point3d_id -> t_ns, from points_t.txt (PARTIAL map; last-wins on dup ids)."""
    return _pairs_to_dict(_parse_txt_pairs(path), strict, path)


def read_points_t_bin(path: str | Path, strict: bool = False) -> dict[int, int]:
    """point3d_id -> t_ns, from points_t.bin (PARTIAL map; last-wins on dup ids)."""
    return _pairs_to_dict(_parse_bin_pairs(path, _POINTS_T_REC), strict, path)


def read_points_t_pairs(path: str | Path) -> list[tuple[int, int]]:
    """Raw (point3d_id, t_ns) rows with duplicates preserved (for validation)."""
    if str(path).endswith(".bin"):
        return _parse_bin_pairs(path, _POINTS_T_REC)
    return _parse_txt_pairs(path)


def write_points_t_txt(path: str | Path, points_t: dict[int, int]) -> None:
    _write_id_time_txt(
        path,
        points_t,
        "# colmap4d points_t: POINT3D_ID, T_NS (int64 ns); PARTIAL map, "
        "absent point == temporally-unbounded\n"
        f"# Number of points with a timestamp: {len(points_t)}",
    )


def write_points_t_bin(path: str | Path, points_t: dict[int, int]) -> None:
    _write_id_time_bin(path, points_t, _POINTS_T_REC)


def is_temporally_unbounded(points_t: dict[int, int], point3d_id: int) -> bool:
    """True if the point has no timestamp (present at all t). See spec Part I.A."""
    return point3d_id not in points_t


# --------------------------------------------------------------------------- #
# time_meta
# --------------------------------------------------------------------------- #
def read_time_meta(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_time_meta(path: str | Path, meta: dict) -> None:
    Path(path).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# directory-level loading (backward compatible)
# --------------------------------------------------------------------------- #
@dataclass
class Sidecars:
    """The colmap4d time layer for one sparse model directory.

    A plain COLMAP model (no sidecars) yields empty maps and ``time_meta is None``;
    this is the backward-compatibility baseline and must never raise.
    """

    times: dict[int, int] = field(default_factory=dict)
    points_t: dict[int, int] = field(default_factory=dict)
    time_meta: dict | None = None

    def point_time(self, point3d_id: int) -> int | None:
        """t_ns for a point, or ``TIMELESS`` (None) if temporally-unbounded."""
        return self.points_t.get(point3d_id, TIMELESS)

    def t0_ns(self) -> int | None:
        """Rebase origin = min timestamp over the **raw** sidecar records — an APPROXIMATION.

        The authoritative t0 (spec I.B) is the min over the model-joined, dangling-dropped
        records; this method has no base model, so a dangling id with an early timestamp would
        pull it earlier. Use ``colmap4d.model.ModelView.t0_ns()`` when the base model is
        available. ``None`` when there are no timestamps at all.
        """
        vals = list(self.times.values()) + list(self.points_t.values())
        return min(vals) if vals else None


def load_sidecars(model_dir: str | Path, strict: bool = False) -> Sidecars:
    """Load the time sidecars from a COLMAP sparse model directory.

    Binary is preferred over text when both are present (matching COLMAP). Missing
    sidecars are treated as empty, never as an error. Duplicate ids resolve last-wins
    (spec Part I); pass ``strict=True`` to raise on a duplicate instead.

    Note: this reader does not know the base model, so it cannot drop ids that are
    absent from the model. Ignoring model-absent (dangling) ids is the consuming
    layer's job (spec Part I: a reader MUST ignore ids not present in the model); see
    ``colmap4d.model`` and ``colmap4d.validate``.
    """
    d = Path(model_dir)
    sc = Sidecars()

    if (d / TIMES_BIN).exists():
        sc.times = read_times_bin(d / TIMES_BIN, strict=strict)
    elif (d / TIMES_TXT).exists():
        sc.times = read_times_txt(d / TIMES_TXT, strict=strict)

    if (d / POINTS_T_BIN).exists():
        sc.points_t = read_points_t_bin(d / POINTS_T_BIN, strict=strict)
    elif (d / POINTS_T_TXT).exists():
        sc.points_t = read_points_t_txt(d / POINTS_T_TXT, strict=strict)

    if (d / TIME_META_JSON).exists():
        sc.time_meta = read_time_meta(d / TIME_META_JSON)

    return sc


def rebase_to_seconds_f32(t_ns: int, t0_ns: int) -> float:
    """Convert an int64-ns timestamp to a rebased float ``(t - t0)`` in seconds.

    Rendering/GPU code MUST rebase before narrowing to float32: raw ns (~1.7e18)
    overflows float32's 24-bit mantissa, but ``(t - t0)`` over a capture spans only
    seconds and keeps ~microsecond precision as float32. See spec Part I.B.
    """
    return (t_ns - t0_ns) / 1e9
