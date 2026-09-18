"""Canvas layout, warping, and blending of registered tiles.

Because the geometric model is rigid (no scale/perspective), every tile
is placed with cv2.warpAffine using a 2x3 matrix - never warpPerspective -
so straight lines and text in the scans are never bent or skewed, only
rotated/translated.

Blending uses distance-transform-weighted feathering with the weights
raised to a power to sharpen the transition. This approximates a clean
seam (good for text/line-art scans, which get visibly blurred by a wide
feather or by multi-band blending) while still smoothing away any small
sub-pixel misalignment right at the seam line.

Memory note: warping every tile out to the FULL canvas size before
blending (one full-canvas-sized buffer per tile) scales memory with
tile_count x canvas_size - a 15000x15000 canvas is ~645MB per RGB8 tile,
so 50 tiles would need 30GB+ just to hold the warped copies. Instead,
StreamingCompositor below warps each tile only into its own small
bounding box on the canvas and immediately accumulates it into two
canvas-sized buffers (accum, weight_sum) that exist ONCE, not once per
tile - memory then scales with canvas_size + one_tile_size, regardless
of how many tiles there are.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger("flatstitch")

# LANCZOS4 is sharper for photographic content but its wider sinc kernel
# rings (overshoot/undershoot) at hard edges - visible as faint halos next
# to scanned text and thin lines. CUBIC keeps most of the sharpness with
# much less ringing, which is why it's the default for this tool.
_INTERPOLATION_FLAGS = {
    "cubic": cv2.INTER_CUBIC,
    "linear": cv2.INTER_LINEAR,
    "lanczos4": cv2.INTER_LANCZOS4,
    "nearest": cv2.INTER_NEAREST,
}


def compute_canvas(image_shapes: list[tuple[int, int]], poses: dict):
    """Given each image's (h, w) and its (R, t) global pose, compute the
    canvas size and a per-image 2x3 affine matrix (original pixel coords
    -> canvas pixel coords, with a shift so every coordinate is >= 0).
    """
    all_corners = []
    for idx, (h, w) in enumerate(image_shapes):
        if idx not in poses:
            continue
        r, t = poses[idx]
        corners = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64)
        all_corners.append(corners @ r.T + t)
    all_corners = np.vstack(all_corners)

    min_xy = all_corners.min(axis=0)
    max_xy = all_corners.max(axis=0)
    offset = -min_xy
    canvas_w = int(np.ceil(max_xy[0] - min_xy[0])) + 1
    canvas_h = int(np.ceil(max_xy[1] - min_xy[1])) + 1

    affine_mats = {}
    for idx, (h, w) in enumerate(image_shapes):
        if idx not in poses:
            continue
        r, t = poses[idx]
        t_shift = t + offset
        affine_mats[idx] = np.hstack([r, t_shift.reshape(2, 1)])
    return (canvas_w, canvas_h), offset, affine_mats


def tile_bbox_in_canvas(shape_hw, affine_mat: np.ndarray, canvas_size: tuple[int, int]):
    """Bounding box (x0, y0, x1, y1) - x1/y1 exclusive - that a tile of
    shape shape_hw occupies on the canvas once mapped by affine_mat,
    clipped to the canvas bounds.
    """
    h, w = shape_hw
    canvas_w, canvas_h = canvas_size
    corners = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64)
    warped = corners @ affine_mat[:, :2].T + affine_mat[:, 2]
    x0 = max(0, int(np.floor(warped[:, 0].min())))
    y0 = max(0, int(np.floor(warped[:, 1].min())))
    x1 = min(canvas_w, int(np.ceil(warped[:, 0].max())))
    y1 = min(canvas_h, int(np.ceil(warped[:, 1].max())))
    return x0, y0, x1, y1


def warp_tile_local(img: np.ndarray, affine_mat: np.ndarray, bbox, interpolation: str = "cubic"):
    """Warp img into a small buffer covering only its own bbox on the
    canvas - NOT the whole canvas - the memory-efficient counterpart of
    warping straight onto a full-canvas-sized buffer. Returns
    (warped_local, mask_local).
    """
    x0, y0, x1, y1 = bbox
    local_w, local_h = x1 - x0, y1 - y0
    local_mat = affine_mat.copy()
    local_mat[0, 2] -= x0
    local_mat[1, 2] -= y0

    flag = _INTERPOLATION_FLAGS.get(interpolation, cv2.INTER_CUBIC)
    warped = cv2.warpAffine(
        img, local_mat, (local_w, local_h), flags=flag,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    src_mask = np.full(img.shape[:2], 255, dtype=np.uint8)
    warped_mask = cv2.warpAffine(
        src_mask, local_mat, (local_w, local_h), flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    return warped, warped_mask


def _feather_weight(mask: np.ndarray, sharpen_power: float) -> np.ndarray:
    dist = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 5)
    peak = dist.max()
    if peak <= 0:
        return dist
    return (dist / peak) ** sharpen_power


class StreamingCompositor:
    """Accumulates warped tiles onto a shared canvas one at a time,
    holding only ONE tile's warped data (sized to its own bounding box,
    not the full canvas) in memory at a time, alongside the two
    persistent canvas-sized accumulators. Call add_tile() once per tile
    (loading/discarding each tile's pixel data is the caller's job, so
    this class never needs more than one tile resident at once), then
    finish() for the blended result.
    """

    def __init__(self, canvas_size: tuple[int, int], n_channels: int,
                 sharpen_power: float = 6.0, fill_value: float = 0.0,
                 interpolation: str = "cubic"):
        canvas_w, canvas_h = canvas_size
        self.canvas_size = canvas_size
        self.sharpen_power = sharpen_power
        self.fill_value = fill_value
        self.interpolation = interpolation
        # float32, not float64: half the memory, still far more precision
        # than 8/16-bit source pixels need.
        self.accum = np.zeros((canvas_h, canvas_w, n_channels), dtype=np.float32)
        self.weight_sum = np.zeros((canvas_h, canvas_w), dtype=np.float32)

    def add_tile(self, img: np.ndarray, affine_mat: np.ndarray) -> None:
        bbox = tile_bbox_in_canvas(img.shape[:2], affine_mat, self.canvas_size)
        x0, y0, x1, y1 = bbox
        if x1 <= x0 or y1 <= y0:
            return
        warped, mask = warp_tile_local(img, affine_mat, bbox, self.interpolation)
        w_map = _feather_weight(mask, self.sharpen_power)

        warped_f = warped.astype(np.float32)
        if warped_f.ndim == 2:
            warped_f = warped_f[..., None]

        self.accum[y0:y1, x0:x1] += warped_f * w_map[..., None]
        self.weight_sum[y0:y1, x0:x1] += w_map

    def finish(self):
        """Returns (result, covered): result is a float32 (H,W,C) or
        (H,W) array; covered is a boolean (H,W) mask of pixels that had
        at least one contributing tile.
        """
        n_channels = self.accum.shape[2]
        covered = self.weight_sum > 0
        weight_safe = np.where(covered, self.weight_sum, 1.0)
        result = self.accum / weight_safe[..., None]
        result[~covered] = self.fill_value
        if n_channels == 1:
            result = result[..., 0]
        return result, covered


def bounding_box_of_mask(mask: np.ndarray):
    """Returns (x0, y0, x1, y1) (x1/y1 exclusive) bounding box of True
    values in mask, or None if the mask is entirely False."""
    ys, xs = np.where(mask)
    if len(ys) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def crop_to_content(img: np.ndarray, covered: np.ndarray):
    """Crop img and covered to the bounding box of covered content,
    trimming empty margins left by any slight rotation. Pixels still
    uncovered *inside* that box (visible in the returned covered mask)
    indicate a real gap - most likely insufficient overlap somewhere.
    """
    bbox = bounding_box_of_mask(covered)
    if bbox is None:
        return img, covered
    x0, y0, x1, y1 = bbox
    return img[y0:y1, x0:x1], covered[y0:y1, x0:x1]


def attach_alpha(img: np.ndarray, covered: np.ndarray, dtype) -> np.ndarray:
    """Append an alpha channel built from the coverage mask: fully opaque
    where at least one scan contributed, fully transparent elsewhere.

    Grayscale input becomes 2-channel (gray+alpha), color becomes
    4-channel (RGBA). The alpha is binary rather than feathered because
    the tile masks themselves are binary (warped with INTER_NEAREST), so
    there is no partial coverage to represent - and a hard edge is what
    you want when the transparent region is just the ragged outline of
    the stitched sheet.
    """
    opaque = np.iinfo(dtype).max
    alpha = np.where(covered, opaque, 0).astype(dtype)
    # dstack handles both a 2-D grayscale plane and a 3-D color stack.
    return np.dstack([img, alpha])
