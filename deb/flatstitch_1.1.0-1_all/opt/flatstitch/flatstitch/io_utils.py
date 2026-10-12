"""Image I/O: loading tiles (preserving bit depth/channels) and writing
the final composite as TIFF.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
import tifffile

from .i18n import _

logger = logging.getLogger("flatstitch")

# Classic (non-Big) TIFF has a hard 4 GiB file-size ceiling; leave margin.
_BIGTIFF_THRESHOLD_BYTES = int(3.8 * 1024**3)


def load_image(path) -> np.ndarray:
    """Load an image preserving its bit depth (8/16-bit) and channel count.
    Returns an array shaped (H, W) for grayscale or (H, W, 3)/(H, W, 4) for
    color, in RGB(A) channel order.
    """
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise IOError(_("io.error.cannot_read", path=path))
    if img.ndim == 3 and img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    elif img.ndim == 3 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA)
    return img


def to_gray_for_features(img: np.ndarray) -> np.ndarray:
    """Convert to an 8-bit single-channel image suitable for feature
    detection, regardless of the original bit depth/channel count.
    """
    if img.ndim == 2:
        gray = img
    else:
        gray = cv2.cvtColor(img[..., :3], cv2.COLOR_RGB2GRAY)
    if gray.dtype == np.uint16:
        gray = (gray / 256).astype(np.uint8)
    elif gray.dtype != np.uint8:
        gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    return gray


def save_tiff(path, img: np.ndarray, compression: str = "lzw", has_alpha: bool = False) -> None:
    """Save the composite as TIFF, preserving dtype and channel layout.
    Automatically switches to BigTIFF for very large outputs.

    has_alpha marks the last channel as an alpha channel: 4-channel input
    is written as RGBA and 2-channel input as gray+alpha. The alpha is
    tagged UNASSALPHA (unassociated, i.e. straight/non-premultiplied),
    which matches how the compositor produces it - RGB values are the
    real pixel colors, not multiplied by coverage - so viewers that
    ignore alpha entirely still show sensible colors rather than a
    darkened image.
    """
    path = Path(path)
    if img.dtype not in (np.uint8, np.uint16):
        img = np.clip(img, 0, 255).astype(np.uint8)

    if compression == "none":
        compression = None

    n_ch = img.shape[2] if img.ndim == 3 else 1
    if has_alpha and n_ch in (2, 4):
        photometric = "rgb" if n_ch == 4 else "minisblack"
        extrasamples = "unassalpha"
    else:
        photometric = "rgb" if n_ch >= 3 else "minisblack"
        extrasamples = None

    bigtiff = img.nbytes > _BIGTIFF_THRESHOLD_BYTES
    if bigtiff:
        logger.info(_("io.log.bigtiff"))

    kwargs = dict(compression=compression, bigtiff=bigtiff, photometric=photometric)
    if extrasamples is not None:
        kwargs["extrasamples"] = extrasamples

    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), img, **kwargs)
