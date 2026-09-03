"""Full colmap4d model = COLMAP base model + time sidecars. (PLACEHOLDER)

Intent (not implemented in WP0): read a standard COLMAP sparse model via pycolmap
(``pycolmap.Reconstruction``) and attach the :class:`colmap4d.sidecar.Sidecars`
time layer, exposing per-image and per-point (x, y, z, t). pycolmap is an OPTIONAL
dependency; importing this module without it should degrade gracefully.

Kept as a stub so the package layout and the "only add" philosophy (never reimplement
COLMAP's own parsers) are visible from day 0.
"""

from __future__ import annotations

raise NotImplementedError("colmap4d.model is a WP0 placeholder; see docstring.")
