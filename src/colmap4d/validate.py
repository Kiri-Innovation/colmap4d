"""`colmap4d validate` — model sanity checks. (PLACEHOLDER)

Intent (not implemented in WP0): check timestamp coverage, per-camera time
monotonicity, clock-domain declaration in time_meta, and that every points_t /
times key refers to an existing point / image in the base model. Emits warnings,
not hard errors, for RECOMMENDED-but-optional conventions.
"""

from __future__ import annotations


def validate(model_dir: str) -> list[str]:  # noqa: ARG001 (placeholder signature)
    raise NotImplementedError("colmap4d.validate is a WP0 placeholder.")
