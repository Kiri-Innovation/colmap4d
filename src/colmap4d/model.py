"""Full colmap4d model = COLMAP base model + time sidecars.

This is the reference reader's core entry point: ``import colmap4d`` then
``colmap4d.load_model_view(dir)`` gives you a model whose images and points carry time.

The COLMAP base model (cameras/images/points3D) is read by :mod:`colmap4d.colmap_io`, a
zero-dependency pure-Python reader — so the default path needs no compiled dependency.
Where **pycolmap** is installed it is preferred (authoritative, and it understands the 3.12
rigs/frames binary variants); :func:`read_reconstruction` exposes it for callers that need
full geometry (e.g. the per-frame converter).

Model-aware behavior lives here: the timeless (Part I.A) and dangling-ignored (Part I.D)
rules are applied against the actual set of model ids, and :mod:`colmap4d.validate`'s graded
checks are wired to that id set.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from colmap4d import colmap_io, sidecar, validate
from colmap4d.colmap_io import BaseModel
from colmap4d.sidecar import TIMELESS, Sidecars


def read_base_model(model_dir: str | Path) -> BaseModel:
    """Read the COLMAP base model with the zero-dependency pure-Python reader."""
    return colmap_io.read_model(model_dir)


def read_reconstruction(model_dir: str | Path) -> Any:
    """Read the base model via pycolmap (optional dependency), returning its
    ``pycolmap.Reconstruction``. Use when you need full geometry (poses, 2D observations,
    rigs/frames). Raises a clear error if pycolmap is not installed."""
    try:
        import pycolmap
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "read_reconstruction needs pycolmap. Install it with:  pip install 'colmap4d[model]'"
            "   (or: pip install pycolmap). For a dependency-free read use read_base_model()."
        ) from e
    return pycolmap.Reconstruction(str(model_dir))


@dataclass
class ModelView:
    """A COLMAP base model joined with its colmap4d time sidecars.

    The join applies the two Part I rules that need the base model:
      * temporally-unbounded (I.A): a model point absent from ``points_t`` has time ``None``.
      * dangling ids ignored (I.D): sidecar ids absent from the model are dropped from the
        ``effective_*`` views (they remain in ``sidecars`` raw, and are a ``validate`` warning).
    """

    base: BaseModel
    sidecars: Sidecars

    def image_ids(self) -> set[int]:
        return self.base.image_ids()

    def point_ids(self) -> set[int]:
        return self.base.point_ids()

    def effective_times(self) -> dict[int, int]:
        """image_id -> t_ns, restricted to images present in the model."""
        ids = self.image_ids()
        return {i: t for i, t in self.sidecars.times.items() if i in ids}

    def effective_points_t(self) -> dict[int, int]:
        """point3d_id -> t_ns, restricted to points present in the model (dangling dropped)."""
        ids = self.point_ids()
        return {p: t for p, t in self.sidecars.points_t.items() if p in ids}

    def image_time(self, image_id: int) -> int | None:
        """t_ns for a model image, or ``None`` if it carries no timestamp."""
        if image_id not in self.image_ids():
            raise KeyError(f"image {image_id} not in model")
        return self.sidecars.times.get(image_id, TIMELESS)

    def point_time(self, point3d_id: int) -> int | None:
        """t_ns for a model point, or ``TIMELESS`` (None) if temporally-unbounded."""
        if point3d_id not in self.point_ids():
            raise KeyError(f"point3D {point3d_id} not in model")
        return self.sidecars.points_t.get(point3d_id, TIMELESS)


def load_model_view(model_dir: str | Path, strict: bool = False) -> ModelView:
    """Read the COLMAP model (zero-dep) + sidecars and return a joined :class:`ModelView`."""
    base = read_base_model(model_dir)
    sc = sidecar.load_sidecars(model_dir, strict=strict)
    return ModelView(base, sc)


def validate_full(model_dir: str | Path) -> list[validate.Problem]:
    """Run the complete validate suite, wiring the model's id sets (read zero-dep) so the
    dangling-id checks (Part I.D) actually fire. Compute status with ``validate.exit_code``."""
    base = read_base_model(model_dir)
    return validate.validate(model_dir, image_ids=base.image_ids(), point_ids=base.point_ids())
