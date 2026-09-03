"""Full colmap4d model = COLMAP base model + time sidecars.

The COLMAP base model (cameras/images/points3D, plus optional rigs/frames) is read via
**pycolmap**, an OPTIONAL dependency — colmap4d never reimplements COLMAP's own parsers
("only add"). Install it with ``pip install 'colmap4d[model]'`` (or ``pip install pycolmap``).
Importing this module without pycolmap is fine; only calling into it raises, with guidance.

This layer is where model-aware behavior lives: joining the timeless (Part I.A) and dangling
(Part I.D) rules against the actual set of model ids, and wiring :mod:`colmap4d.validate`'s
graded checks to that id set.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from colmap4d import sidecar, validate
from colmap4d.sidecar import TIMELESS, Sidecars

if TYPE_CHECKING:  # avoid importing the optional dep at module load
    import pycolmap


def _require_pycolmap() -> Any:
    try:
        import pycolmap
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "colmap4d.model needs pycolmap to read the COLMAP base model. "
            "Install it with:  pip install 'colmap4d[model]'   (or: pip install pycolmap)"
        ) from e
    return pycolmap


def read_reconstruction(model_dir: str | Path) -> pycolmap.Reconstruction:
    """Read a standard COLMAP sparse model (bin or txt) via pycolmap."""
    pycolmap = _require_pycolmap()
    return pycolmap.Reconstruction(str(model_dir))


@dataclass
class ModelView:
    """A COLMAP reconstruction joined with its colmap4d time sidecars.

    The join applies the two Part I rules that need the base model:
      * temporally-unbounded (I.A): a model point absent from ``points_t`` has time ``None``.
      * dangling ids ignored (I.D): sidecar ids absent from the model are dropped from the
        ``effective_*`` views (they remain in ``sidecars`` raw, and are a ``validate`` warning).
    """

    reconstruction: pycolmap.Reconstruction
    sidecars: Sidecars

    def image_ids(self) -> set[int]:
        return {int(i) for i in self.reconstruction.images}

    def point_ids(self) -> set[int]:
        return {int(i) for i in self.reconstruction.points3D}

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
    """Read the COLMAP model + sidecars and return a joined :class:`ModelView`."""
    rec = read_reconstruction(model_dir)
    sc = sidecar.load_sidecars(model_dir, strict=strict)
    return ModelView(rec, sc)


def validate_full(model_dir: str | Path) -> list[validate.Problem]:
    """Run the complete validate suite, wiring the model's id sets so that dangling-id
    checks (Part I.D) actually fire. Compute process status with ``validate.exit_code``.
    """
    rec = read_reconstruction(model_dir)
    image_ids = {int(i) for i in rec.images}
    point_ids = {int(i) for i in rec.points3D}
    return validate.validate(model_dir, image_ids=image_ids, point_ids=point_ids)
