"""Derived pseudo-frame grouping.

``group_by_time(times, eps_ns)`` clusters images whose timestamps fall within eps_ns
into pseudo-frames. Optional read/write of a `groups.txt` derived view. Per spec Part
III, grouping is a DERIVED view: the source of truth is always ``times`` + a chosen
epsilon, recomputable on demand. groups.txt is never required and never authoritative.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GridSample:
    """One timestamped sample supplied to :func:`group_by_grid`.

    ``device_id`` identifies the source device for within-bucket de-duplication.
    ``value`` is an opaque caller payload (for example an image path or record id)
    returned unchanged in the grouping result.
    """

    device_id: str
    time_ns: int
    value: Any = None


@dataclass(frozen=True)
class GridGroup:
    """One retained fixed-grid bucket after per-device de-duplication."""

    grid_index: int
    t_center_ns: int
    members: tuple[GridSample, ...]


@dataclass(frozen=True)
class GridDiscard:
    """A sample omitted from the retained grouping, with a stable reason."""

    sample: GridSample
    grid_index: int
    reason: str
    kept_sample: GridSample | None = None


@dataclass(frozen=True)
class GridGrouping:
    """Result and provenance of :func:`group_by_grid`."""

    grid_ns: int
    origin_ns: int | None
    expected_device_count: int
    groups: tuple[GridGroup, ...]
    discarded: tuple[GridDiscard, ...]
    trim_edges: bool
    first_full_grid_index: int | None
    last_full_grid_index: int | None
    trimmed_head_buckets: int
    trimmed_tail_buckets: int


def group_by_grid(
    samples: Iterable[GridSample],
    grid_ns: int,
    *,
    origin_ns: int | None = None,
    trim_edges: bool = False,
    expected_device_count: int | None = None,
) -> GridGrouping:
    """Materialize timestamped device samples on a fixed time grid.

    This is the fixed-grid policy used by HoloLab's rig temporal grouping:

    1. Choose ``origin_ns`` from the earliest sample when it is not supplied.
    2. Assign each sample to ``floor((time_ns - origin_ns) / grid_ns)``.
    3. Within each bucket and device, retain the sample nearest the bucket
       center. An exact-distance tie keeps the earlier timestamp; equal
       timestamps preserve caller input order.
    4. Sort retained members by ``device_id``.
    5. If ``trim_edges`` is true, keep the inclusive span from the first to
       the last bucket containing exactly ``expected_device_count`` devices.
       Incomplete buckets inside that span remain present.

    ``expected_device_count`` defaults to the number of distinct device ids in
    the complete input. It controls only completeness and trimming, never bucket
    membership. The historical bucket-center expression intentionally performs
    the same integer-plus-float calculation as the established implementation:
    ``int(origin_ns + (grid_index + 0.5) * grid_ns)``. At epoch-nanosecond scale
    this can differ by a few hundred nanoseconds from an exact rational center;
    preserving it is necessary for compatible nearest-sample tie-breaking.

    Args:
        samples: Timestamped samples. The iterable is consumed but not mutated.
        grid_ns: Positive bucket width in nanoseconds.
        origin_ns: Grid origin. ``None`` chooses the earliest sample timestamp.
        trim_edges: Whether to remove leading/trailing incomplete buckets.
        expected_device_count: Number of devices required for a complete bucket.
            ``None`` infers it from all input device ids.

    Returns:
        A :class:`GridGrouping` containing retained groups, trim metadata, and
        every omitted candidate. ``discarded`` uses ``device_duplicate`` for
        within-device losers and ``trimmed_edge`` for winners in trimmed buckets.

    Raises:
        ValueError: If ``grid_ns`` is not positive, the expected device count is
            negative, or edge trimming is requested without a complete bucket.
    """
    if grid_ns <= 0:
        raise ValueError("grid_ns must be positive")
    if expected_device_count is not None and expected_device_count < 0:
        raise ValueError("expected_device_count must be non-negative")

    indexed = list(enumerate(samples))
    inferred_count = len({sample.device_id for _, sample in indexed})
    complete_count = inferred_count if expected_device_count is None else expected_device_count

    if not indexed:
        return GridGrouping(
            grid_ns=grid_ns,
            origin_ns=origin_ns,
            expected_device_count=complete_count,
            groups=(),
            discarded=(),
            trim_edges=trim_edges,
            first_full_grid_index=None,
            last_full_grid_index=None,
            trimmed_head_buckets=0,
            trimmed_tail_buckets=0,
        )

    # Python's sort is stable. The input index makes that compatibility rule
    # explicit for equal timestamps and avoids relying on GridSample ordering.
    ordered = sorted(indexed, key=lambda item: (item[1].time_ns, item[0]))
    resolved_origin = ordered[0][1].time_ns if origin_ns is None else origin_ns
    buckets: dict[int, list[tuple[int, GridSample]]] = {}
    for input_index, sample in ordered:
        grid_index = (sample.time_ns - resolved_origin) // grid_ns
        buckets.setdefault(grid_index, []).append((input_index, sample))

    groups: list[GridGroup] = []
    discarded: list[GridDiscard] = []
    for grid_index in sorted(buckets):
        center = int(resolved_origin + (grid_index + 0.5) * grid_ns)
        # Match the established stable ``sorted(..., key=device_id)`` pass.
        candidates = sorted(buckets[grid_index], key=lambda item: item[1].device_id)
        picked: dict[str, tuple[int, GridSample]] = {}
        for input_index, sample in candidates:
            previous = picked.get(sample.device_id)
            if previous is None:
                picked[sample.device_id] = (input_index, sample)
                continue
            previous_sample = previous[1]
            if abs(sample.time_ns - center) < abs(previous_sample.time_ns - center):
                discarded.append(
                    GridDiscard(
                        sample=previous_sample,
                        grid_index=grid_index,
                        reason="device_duplicate",
                        kept_sample=sample,
                    )
                )
                picked[sample.device_id] = (input_index, sample)
            else:
                discarded.append(
                    GridDiscard(
                        sample=sample,
                        grid_index=grid_index,
                        reason="device_duplicate",
                        kept_sample=previous_sample,
                    )
                )
        members = tuple(
            item[1] for item in sorted(picked.values(), key=lambda item: item[1].device_id)
        )
        groups.append(GridGroup(grid_index=grid_index, t_center_ns=center, members=members))

    first_full: int | None = None
    last_full: int | None = None
    head_count = 0
    tail_count = 0
    if trim_edges:
        full_positions = [
            position
            for position, group in enumerate(groups)
            if len(group.members) == complete_count
        ]
        if not full_positions:
            raise ValueError(
                "trim_edges requires at least one complete bucket "
                f"(expected_device_count={complete_count})"
            )
        first_position, last_position = full_positions[0], full_positions[-1]
        first_full = groups[first_position].grid_index
        last_full = groups[last_position].grid_index
        head_count = first_position
        tail_count = len(groups) - last_position - 1
        for group in groups[:first_position] + groups[last_position + 1 :]:
            discarded.extend(
                GridDiscard(sample=sample, grid_index=group.grid_index, reason="trimmed_edge")
                for sample in group.members
            )
        groups = groups[first_position : last_position + 1]

    return GridGrouping(
        grid_ns=grid_ns,
        origin_ns=resolved_origin,
        expected_device_count=complete_count,
        groups=tuple(groups),
        discarded=tuple(discarded),
        trim_edges=trim_edges,
        first_full_grid_index=first_full,
        last_full_grid_index=last_full,
        trimmed_head_buckets=head_count,
        trimmed_tail_buckets=tail_count,
    )


def group_by_time(
    times: dict[int, int], eps_ns: int, method: str = "greedy_window"
) -> list[tuple[int, list[int]]]:
    """Cluster images by timestamp into pseudo-frames.

    Args:
        times: Map from IMAGE_ID to TIMESTAMP_NS
        eps_ns: Time window radius (half-width) in nanoseconds
        method: Grouping method (currently only "greedy_window" supported)

    Returns:
        List of (t_center_ns, image_ids) tuples, sorted by t_center

    Raises:
        ValueError: If method is not supported

    Algorithm (greedy_window):
        1. Sort images by timestamp
        2. Start with first ungrouped image as seed
        3. Create group with all images within ±eps_ns of seed
        4. Use group median as t_center
        5. Repeat with next ungrouped image
    """
    if method != "greedy_window":
        raise ValueError(f"Unsupported grouping method: {method}")

    if not times:
        return []

    # Sort by timestamp
    sorted_items = sorted(times.items(), key=lambda x: x[1])
    grouped = [False] * len(sorted_items)
    groups = []

    for i, (_seed_id, seed_t) in enumerate(sorted_items):
        if grouped[i]:
            continue

        # Collect all images within eps_ns of seed
        group_ids = []
        group_times = []

        for j, (img_id, img_t) in enumerate(sorted_items):
            if not grouped[j] and abs(img_t - seed_t) <= eps_ns:
                group_ids.append(img_id)
                group_times.append(img_t)
                grouped[j] = True

        if group_ids:
            # Use median timestamp as group center
            group_times_sorted = sorted(group_times)
            t_center = group_times_sorted[len(group_times_sorted) // 2]
            groups.append((t_center, group_ids))

    return groups


def write_groups_txt(
    groups: list[tuple[int, list[int]]],
    output_path: Path | str,
    eps_ns: int,
    method: str = "greedy_window",
) -> None:
    """Write groups.txt derived view.

    Args:
        groups: List of (t_center_ns, image_ids) tuples
        output_path: Output file path
        eps_ns: Time window parameter used to generate groups
        method: Grouping method used
    """
    output_path = Path(output_path)

    lines = [
        f"# groups.txt — derived, eps_ns={eps_ns}, method={method}\n",
        "# GROUP_ID, T_CENTER_NS, IMAGE_IDS...\n",
    ]

    for group_id, (t_center, image_ids) in enumerate(groups, start=1):
        image_ids_str = " ".join(str(img_id) for img_id in sorted(image_ids))
        lines.append(f"{group_id} {t_center} {image_ids_str}\n")

    output_path.write_text("".join(lines))


def read_groups_txt(
    path: Path | str,
) -> tuple[list[tuple[int, list[int]]], dict[str, str]]:
    """Read groups.txt derived view.

    Args:
        path: Path to groups.txt file

    Returns:
        Tuple of (groups, params) where:
        - groups: List of (t_center_ns, image_ids) tuples
        - params: Dict with 'eps_ns', 'method' extracted from header

    Raises:
        ValueError: If header is missing required parameters
    """
    path = Path(path)
    lines = path.read_text().splitlines()

    params = {}
    groups = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("#"):
            # Parse header for parameters
            if "eps_ns=" in line:
                for part in line.split(","):
                    part = part.strip()
                    if "=" in part:
                        key, value = part.split("=", 1)
                        params[key.strip()] = value.strip()
            continue

        # Parse data line: GROUP_ID T_CENTER_NS IMAGE_IDS...
        parts = line.split()
        if len(parts) < 3:
            continue

        group_id = int(parts[0])  # noqa: F841
        t_center = int(parts[1])
        image_ids = [int(x) for x in parts[2:]]

        groups.append((t_center, image_ids))

    if "eps_ns" not in params or "method" not in params:
        raise ValueError("groups.txt header missing required parameters (eps_ns, method)")

    return groups, params
