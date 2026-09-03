"""Real-data end-to-end verification of the per-frame -> single-colmap4d converter.

Data path is provided via the COLMAP4D_REAL_FRAMES env var (the directory containing
``colmap_0/ .. colmap_N/``, each with ``sparse/0/`` holding cameras/images/points3D). When
the env var is unset or the data is absent, every test here is skipped, so this file is safe
in CI. No large-data path is hard-coded.

Everything is read with the zero-dependency ``colmap_io`` reader (proven field-equal to
pycolmap on this data), so the verification itself needs no compiled dependency.
"""

from __future__ import annotations

import math
import os
from collections import Counter
from pathlib import Path

import pytest

from colmap4d import colmap_io, model, sidecar
from colmap4d.convert.per_frame_colmap import DEFAULT_FRAME_INTERVAL_NS, convert_per_frame_colmap
from colmap4d.validate import exit_code

_BASE = os.environ.get("COLMAP4D_REAL_FRAMES")
_FRAMES = [Path(_BASE) / f"colmap_{i}" / "sparse" / "0" for i in range(5)] if _BASE else []
_HAVE = bool(_FRAMES) and all(d.exists() for d in _FRAMES)

pytestmark = pytest.mark.skipif(
    not _HAVE, reason="set COLMAP4D_REAL_FRAMES to a dir containing colmap_0..colmap_4"
)


def _q(v: float) -> float:
    return round(v, 5)


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    out = tmp_path_factory.mktemp("real4d")
    res = convert_per_frame_colmap(_FRAMES, out)
    return out, res


@pytest.fixture(scope="module")
def sources():
    return [colmap_io.read_model(d) for d in _FRAMES]


def test_counts_align_with_sum_of_frames(converted, sources):
    _, res = converted
    assert res.num_frames == 5
    assert res.num_images == sum(len(s.images) for s in sources) == 5 * 21
    assert res.num_cameras == sum(len(s.cameras) for s in sources)
    assert res.num_points == sum(len(s.points3D) for s in sources)


def test_extrinsics_and_intrinsics_passthrough(converted, sources):
    out, _ = converted
    merged = colmap_io.read_model(out)
    by_name = {im.name: im for im in merged.images.values()}
    for f, src in enumerate(sources):
        for src_im in src.images.values():
            m_im = by_name[f"frame_{f:04d}/{src_im.name}"]
            # extrinsics: quaternion + translation, field by field
            for a, b in zip(m_im.qvec, src_im.qvec):
                assert math.isclose(a, b, abs_tol=1e-9)
            for a, b in zip(m_im.tvec, src_im.tvec):
                assert math.isclose(a, b, abs_tol=1e-9)
            # intrinsics: the merged image's camera params equal the source image's camera
            m_params = merged.cameras[m_im.camera_id].params
            s_params = src.cameras[src_im.camera_id].params
            assert merged.cameras[m_im.camera_id].model == src.cameras[src_im.camera_id].model
            for a, b in zip(m_params, s_params):
                assert math.isclose(a, b, abs_tol=1e-9)


def test_points_are_exact_union_of_frames(converted, sources):
    out, _ = converted
    merged = colmap_io.read_model(out)
    merged_ms = Counter(
        (_q(p.xyz[0]), _q(p.xyz[1]), _q(p.xyz[2]), *p.rgb) for p in merged.points3D.values()
    )
    source_ms = Counter(
        (_q(p.xyz[0]), _q(p.xyz[1]), _q(p.xyz[2]), *p.rgb)
        for s in sources
        for p in s.points3D.values()
    )
    assert merged_ms == source_ms  # exact multiset: every point's xyz+rgb preserved, no dedup


def test_sidecars_cover_everything(converted):
    out, res = converted
    sc = sidecar.load_sidecars(out)
    assert len(sc.times) == res.num_images == 105  # every image has a timestamp
    assert len(sc.points_t) == res.num_points  # per-frame import: every point has a t
    assert set(sc.times.values()) == {i * DEFAULT_FRAME_INTERVAL_NS for i in range(5)}
    assert sc.time_meta["clock_domain"] == "synthetic_uniform"


def test_model_view_and_validate(converted):
    out, _ = converted
    assert exit_code(model.validate_full(out)) == 0
    mv = model.load_model_view(out)
    assert len(mv.image_ids()) == 105
    # spot-check: an image's time is its frame index * interval; its points share that instant
    iid = min(mv.image_ids())
    assert mv.image_time(iid) == 0
    pid = min(mv.point_ids())
    assert mv.point_time(pid) in {i * DEFAULT_FRAME_INTERVAL_NS for i in range(5)}
