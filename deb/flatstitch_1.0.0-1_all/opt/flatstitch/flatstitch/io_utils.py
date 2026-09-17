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


def save_tiff(path, img: np.ndarray, compression: str = "lzw") -> None:
    """Save the composite as TIFF, preserving dtype and channel layout.
    Automatically switches to BigTIFF for very large outputs.
    """
    path = Path(path)
    if img.dtype not in (np.uint8, np.uint16):
        img = np.clip(img, 0, 255).astype(np.uint8)

    if compression == "none":
        compression = None

    photometric = "rgb" if (img.ndim == 3 and img.shape[2] >= 3) else "minisblack"
    bigtiff = img.nbytes > _BIGTIFF_THRESHOLD_BYTES
    if bigtiff:
        logger.info(_("io.log.bigtiff"))

    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        str(path),
        img,
        compression=compression,
        bigtiff=bigtiff,
        photometric=photometric,
    )
