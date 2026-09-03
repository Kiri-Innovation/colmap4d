"""Converters into colmap4d — the format's "writer" side.

One importer per stock data layout, turning already-existing data into colmap4d so the
format's data "exists" in the world:

    per_frame_colmap  -- N per-frame COLMAP dirs -> one colmap4d model   (IMPLEMENTED)
    nerfstudio        -- transforms.json (time field), bidirectional     (planned)
    neu3d / dynerf    -- first-frame COLMAP + video frame indices         (planned)
    hypernerf / nerfies                                                   (planned)

Each importer emits standard COLMAP files plus the three time sidecars.
"""

from __future__ import annotations

from colmap4d.convert.per_frame_colmap import convert_per_frame_colmap

__all__ = ["convert_per_frame_colmap"]
