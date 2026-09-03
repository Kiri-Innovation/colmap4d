"""Converters into colmap4d — the format's "writer" side. (PLACEHOLDER PACKAGE)

Intent (not implemented in WP0): one importer per stock data layout, turning
already-existing data into colmap4d so the format's data "exists" in the world:

    per_frame_colmap  -- N single-timestamp COLMAP dirs  <->  one colmap4d model
    nerfstudio        -- transforms.json (time field), bidirectional
    neu3d / dynerf    -- first-frame COLMAP + video frame indices
    hypernerf / nerfies

Each importer emits standard COLMAP files (untouched) plus the three time sidecars.
"""

from __future__ import annotations
