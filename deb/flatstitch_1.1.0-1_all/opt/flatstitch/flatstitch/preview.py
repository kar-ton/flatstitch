"""Render a quick on-screen preview PNG from any image flatstitch can
load (any bit depth/channel layout).

Why this exists: the native GTK/Qt frontends run under the system
Python (so they can see the system's GTK/Qt bindings), not the isolated
venv - so they can't import flatstitch.io_utils directly, and their own
toolkit's native image loader may not handle 16-bit or RGBA TIFF
consistently across distros. Rather than duplicate the bit-depth-aware
loading logic in two more languages/toolkits, the frontends shell out to
this (via the venv's python3, same as for the actual stitching) to get
back an ordinary 8-bit PNG that any image widget can display.
"""
from __future__ import annotations

import sys

import cv2
import numpy as np

from . import io_utils


def to_display_rgb8(img: np.ndarray) -> np.ndarray:
    """Normalize any array flatstitch can load down to 8-bit RGB,
    compositing any alpha channel over white for a flat preview (the
    saved file's own alpha is untouched - this is only for on-screen
    display, so a transparent result doesn't look like a black hole).
    """
    if img.dtype == np.uint16:
        img8 = (img / 256).astype(np.uint8)
    elif img.dtype != np.uint8:
        img8 = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    else:
        img8 = img

    if img8.ndim == 2:
        return cv2.cvtColor(img8, cv2.COLOR_GRAY2RGB)
    if img8.shape[2] == 4:
        rgb = img8[..., :3].astype(np.float32)
        alpha = img8[..., 3:4].astype(np.float32) / 255.0
        return (rgb * alpha + 255.0 * (1 - alpha)).astype(np.uint8)
    if img8.shape[2] == 2:  # gray + alpha
        gray = img8[..., 0:1].astype(np.float32)
        alpha = img8[..., 1:2].astype(np.float32) / 255.0
        flat = (gray * alpha + 255.0 * (1 - alpha)).astype(np.uint8)
        return cv2.cvtColor(flat, cv2.COLOR_GRAY2RGB)
    return img8


def render_preview(src_path: str, dst_path: str, max_w: int = 480, max_h: int = 360) -> None:
    img = io_utils.load_image(src_path)
    rgb = to_display_rgb8(img)
    h, w = rgb.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    if scale < 1.0:
        rgb = cv2.resize(rgb, (max(1, int(w * scale)), max(1, int(h * scale))),
                          interpolation=cv2.INTER_AREA)
    cv2.imwrite(dst_path, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print("usage: python3 -m flatstitch.preview <src> <dst> [max_w] [max_h]", file=sys.stderr)
        return 1
    src, dst = argv[0], argv[1]
    max_w = int(argv[2]) if len(argv) > 2 else 480
    max_h = int(argv[3]) if len(argv) > 3 else 360
    render_preview(src, dst, max_w, max_h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
