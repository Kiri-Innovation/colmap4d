#!/usr/bin/env python3
"""Lay out a colmap4d model's images by their model NAME, so a viewer can resolve each
camera's photo. Companion to the per-frame converter (which writes only the sparse model +
sidecars, not the images).

Each model image NAME (e.g. ``frame_0007/cam03.png``) is placed at ``<out>/<NAME>``. The source
file is found from a template keyed by the frame index parsed from the ``frame_<N>/`` prefix,
e.g. ``--source-template '/data/colmap_{frame}/images'`` → source ``colmap_7/images/cam03.png``.

By default images are copied faithfully. ``--max-size``/``--jpeg-quality`` recompress to JPEG
(useful to fit a viewer's archive-size cap); the destination keeps the NAME's extension but holds
JPEG bytes — browser viewers decode by content, not extension, so a ``.png`` name is fine.

Paths are CLI arguments only; nothing machine-specific is baked in.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from colmap4d import colmap_io  # noqa: E402

_FRAME_RE = re.compile(r"^frame_(\d+)/(.+)$")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("model_dir", help="colmap4d model dir (reads image NAMEs)")
    ap.add_argument("out_images_dir", help="destination images/ root")
    ap.add_argument(
        "--source-template",
        required=True,
        help="source images dir per frame, with {frame} for the integer frame index, "
        "e.g. '/data/colmap_{frame}/images'",
    )
    ap.add_argument(
        "--max-size", type=int, default=0, help="recompress: max long side px (0 = copy as-is)"
    )
    ap.add_argument("--jpeg-quality", type=int, default=85, help="JPEG quality when recompressing")
    args = ap.parse_args()

    out_root = Path(args.out_images_dir)
    model = colmap_io.read_model(args.model_dir)
    recompress = args.max_size > 0
    if recompress:
        from PIL import Image  # local import so plain copy needs no Pillow

    copied = 0
    missing = []
    for im in model.images.values():
        m = _FRAME_RE.match(im.name)
        if not m:
            missing.append((im.name, "name lacks frame_<N>/ prefix"))
            continue
        frame_idx, rel = int(m.group(1)), m.group(2)
        src = Path(args.source_template.format(frame=frame_idx)) / rel
        dst = out_root / im.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not src.exists():
            missing.append((im.name, f"source not found: {src}"))
            continue
        if recompress:
            img = Image.open(src).convert("RGB")
            img.thumbnail((args.max_size, args.max_size))
            img.save(dst, format="JPEG", quality=args.jpeg_quality)
        else:
            shutil.copy2(src, dst)
        copied += 1

    print(f"placed {copied}/{model.images.__len__()} images under {out_root}")
    if missing:
        print(f"WARNING: {len(missing)} unresolved:")
        for name, why in missing[:10]:
            print(f"  {name}: {why}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
