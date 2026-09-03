"""`colmap4d validate` — model sanity checks.

The full validator (coverage, per-camera time monotonicity, clock-domain declaration,
dangling keys) is a WP0 placeholder: :func:`validate`. One targeted rule is implemented
now because it guards a spec invariant (see spec Part I, time_meta consistency rule):
a `CAMERA_ID` may be attributed to at most one device — one image cannot be timestamped
by two clocks. `camera_ids` is optional to write (MAY), but not free to write inconsistently.
"""

from __future__ import annotations


def check_device_camera_ids_unique(time_meta: dict | None) -> list[str]:
    """Return a list of human-readable problems (empty == OK).

    Verifies that no ``CAMERA_ID`` appears under more than one device in
    ``time_meta.devices``. Absent ``time_meta``/``devices``/``camera_ids`` is fine
    (device attribution is optional provenance, not a conformance gate).
    """
    problems: list[str] = []
    if not time_meta:
        return problems
    devices = time_meta.get("devices")
    if not isinstance(devices, dict):
        return problems

    seen: dict[int, str] = {}
    for device_id, dev in devices.items():
        if not isinstance(dev, dict):
            continue
        for cam_id in dev.get("camera_ids", []) or []:
            prior = seen.get(cam_id)
            if prior is not None and prior != device_id:
                problems.append(
                    f"CAMERA_ID {cam_id} is attributed to multiple devices "
                    f"({prior!r} and {device_id!r}); a camera belongs to one clock."
                )
            else:
                seen[cam_id] = device_id
    return problems


def validate(model_dir: str) -> list[str]:  # noqa: ARG001 (placeholder signature)
    raise NotImplementedError("colmap4d.validate.validate is a WP0 placeholder.")
