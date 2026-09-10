"""Derived pseudo-frame grouping.

``group_by_time(times, eps_ns)`` clusters images whose timestamps fall within eps_ns
into pseudo-frames. Optional read/write of a `groups.txt` derived view. Per spec Part
III, grouping is a DERIVED view: the source of truth is always ``times`` + a chosen
epsilon, recomputable on demand. groups.txt is never required and never authoritative.
"""

from __future__ import annotations

from pathlib import Path


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

    for i, (seed_id, seed_t) in enumerate(sorted_items):
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
