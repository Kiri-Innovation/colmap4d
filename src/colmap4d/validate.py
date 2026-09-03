"""`colmap4d validate` — model sanity checks with graded severity and exit codes.

Conformance is a claim a CI job must be able to reject, so `validate` grades problems
instead of only warning (all-warnings ⇒ exit 0 always ⇒ "conforms" becomes unfalsifiable):

    ERROR   -- violates a spec MUST; exit code is non-zero. Duplicate ids and
               device/camera_id conflicts are the writer's bug and land here.
    WARNING -- suspicious but has legitimate uses (e.g. a sidecar kept as a superset
               of a filtered model); exit code stays 0 unless the caller opts into
               `strict=True`, which promotes WARNINGs to failures for strict pipelines.

Reader tolerance (last-wins on duplicates, ignore model-absent ids) is defined in the
spec and implemented in :mod:`colmap4d.sidecar`; this module reports on those situations
rather than silently absorbing them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from colmap4d import sidecar

ERROR = "ERROR"
WARNING = "WARNING"


@dataclass
class Problem:
    severity: str  # ERROR | WARNING
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.message}"


def exit_code(problems: list[Problem], strict: bool = False) -> int:
    """0 if clean; non-zero if any ERROR, or (when ``strict``) any WARNING."""
    if any(p.severity == ERROR for p in problems):
        return 1
    if strict and any(p.severity == WARNING for p in problems):
        return 1
    return 0


# --------------------------------------------------------------------------- #
# individual checks
# --------------------------------------------------------------------------- #
def check_duplicate_ids(pairs: list[tuple[int, int]], kind: str) -> list[Problem]:
    """ERROR per id that appears more than once (writer MUST NOT emit duplicates;
    readers last-wins, so the earlier timestamps are silently lost)."""
    counts: dict[int, int] = {}
    order: list[int] = []
    for key, _ in pairs:
        if key not in counts:
            order.append(key)
        counts[key] = counts.get(key, 0) + 1
    problems: list[Problem] = []
    for key in order:
        if counts[key] > 1:
            problems.append(
                Problem(
                    ERROR,
                    f"{kind}.duplicate_id",
                    f"id {key} appears {counts[key]} times in the {kind} sidecar; a writer "
                    f"MUST NOT emit duplicates (readers take last-wins, losing the rest).",
                )
            )
    return problems


def check_dangling_ids(keys: list[int], valid_ids: set[int], kind: str) -> list[Problem]:
    """WARNING if the sidecar references ids absent from the model.

    Worded to name the real hazard: after an SfM re-run the dangerous case is not the id
    that vanished but the one that still exists yet now points at a *different* image —
    a silently mislabeled timestamp. A dangling id is often the only visible symptom.
    """
    dangling = sorted({k for k in keys if k not in valid_ids})
    if not dangling:
        return []
    sample = ", ".join(str(k) for k in dangling[:5]) + (" …" if len(dangling) > 5 else "")
    return [
        Problem(
            WARNING,
            f"{kind}.dangling_id",
            f"{len(dangling)} id(s) in the {kind} sidecar are not in the model ({sample}). "
            f"If SfM was re-run, ids may be globally misaligned — surviving ids can now "
            f"point at different entities, silently mislabeling timestamps. Regenerate the "
            f"sidecar from the current model rather than trusting the survivors.",
        )
    ]


def check_device_camera_ids_unique(time_meta: dict | None) -> list[Problem]:
    """ERROR if a CAMERA_ID is attributed to more than one device (spec Part I: one
    image cannot be timestamped by two clocks). Absent time_meta/devices/camera_ids is OK.
    """
    if not time_meta:
        return []
    devices = time_meta.get("devices")
    if not isinstance(devices, dict):
        return []
    problems: list[Problem] = []
    seen: dict[int, str] = {}
    for device_id, dev in devices.items():
        if not isinstance(dev, dict):
            continue
        for cam_id in dev.get("camera_ids", []) or []:
            prior = seen.get(cam_id)
            if prior is not None and prior != device_id:
                problems.append(
                    Problem(
                        ERROR,
                        "time_meta.camera_id_conflict",
                        f"CAMERA_ID {cam_id} is attributed to multiple devices "
                        f"({prior!r} and {device_id!r}); a camera belongs to one clock.",
                    )
                )
            else:
                seen[cam_id] = device_id
    return problems


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def validate(
    model_dir: str | Path,
    *,
    image_ids: set[int] | None = None,
    point_ids: set[int] | None = None,
) -> list[Problem]:
    """Run all available checks over a model directory and return graded problems.

    Duplicate-id and device/camera checks need no base model. Dangling-id checks run only
    when the caller supplies the model's id sets (``image_ids`` / ``point_ids``); wiring
    those from the base model via pycolmap belongs to :mod:`colmap4d.model` (not yet built).
    Compute process status with :func:`exit_code`.
    """
    d = Path(model_dir)
    problems: list[Problem] = []

    for bin_name, txt_name, kind, valid in (
        (sidecar.TIMES_BIN, sidecar.TIMES_TXT, "times", image_ids),
        (sidecar.POINTS_T_BIN, sidecar.POINTS_T_TXT, "points_t", point_ids),
    ):
        path = (
            d / bin_name
            if (d / bin_name).exists()
            else (d / txt_name if (d / txt_name).exists() else None)
        )
        if path is None:
            continue
        pairs = (
            sidecar.read_times_pairs(path) if kind == "times" else sidecar.read_points_t_pairs(path)
        )
        problems += check_duplicate_ids(pairs, kind)
        if valid is not None:
            problems += check_dangling_ids([k for k, _ in pairs], valid, kind)

    sc = sidecar.load_sidecars(d)
    problems += check_device_camera_ids_unique(sc.time_meta)
    return problems
