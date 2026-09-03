"""colmap4d — standard COLMAP sparse model + per-image and per-point timestamps.

The zero-dependency time layer lives in :mod:`colmap4d.sidecar`. Base-model parsing
(cameras/images/points3D) is delegated to pycolmap in :mod:`colmap4d.model` (optional
dependency, not implemented yet).
"""

from __future__ import annotations

from colmap4d.sidecar import (
    TIMELESS,
    Sidecars,
    is_temporally_unbounded,
    load_sidecars,
    read_points_t_bin,
    read_points_t_txt,
    read_time_meta,
    read_times_bin,
    read_times_txt,
    rebase_to_seconds_f32,
    write_points_t_bin,
    write_points_t_txt,
    write_time_meta,
    write_times_bin,
    write_times_txt,
)

__version__ = "0.2.0.dev0"

__all__ = [
    "TIMELESS",
    "Sidecars",
    "is_temporally_unbounded",
    "load_sidecars",
    "rebase_to_seconds_f32",
    "read_points_t_bin",
    "read_points_t_txt",
    "read_time_meta",
    "read_times_bin",
    "read_times_txt",
    "write_points_t_bin",
    "write_points_t_txt",
    "write_time_meta",
    "write_times_bin",
    "write_times_txt",
    "__version__",
]
