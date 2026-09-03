"""Derived pseudo-frame grouping. (PLACEHOLDER)

Intent (not implemented in WP0): ``group_by_time(times, eps_ns)`` clusters images
whose timestamps fall within eps_ns into pseudo-frames, and optional read/write of a
`groups.txt` derived view. Per spec Part III, grouping is a DERIVED view: the source
of truth is always ``times`` + a chosen epsilon, recomputable on demand. groups.txt is
never required and never authoritative.
"""

from __future__ import annotations


def group_by_time(times: dict[int, int], eps_ns: int) -> list[list[int]]:  # noqa: ARG001
    raise NotImplementedError("colmap4d.groups is a WP0 placeholder.")
