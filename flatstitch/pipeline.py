"""End-to-end pipeline: extract features -> register all pairs -> build a
global layout -> stream-warp & blend -> save TIFF.

Memory strategy: each image is loaded from disk twice - once (as a
possibly-downscaled grayscale array) for feature detection, and again
later, one tile at a time, for compositing - rather than keeping every
full-resolution original resident for the whole run. Only image shape/
dtype (a few bytes each) survive between the two phases. Combined with
StreamingCompositor's local-bbox warping (see compositor.py), peak memory
scales with canvas_size + one_tile_size, not with the number of tiles.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from . import compositor, features, io_utils, registration
from .i18n import _

logger = logging.getLogger("flatstitch")


class StitchError(RuntimeError):
    pass


def default_output_path(input_paths: list[str]) -> Path:
    """Auto-generated output path: same folder as the first input, named
    stitched.tiff (or stitched_2.tiff, stitched_3.tiff, ... if that name
    is already taken) - never silently overwrites an existing file.
    """
    folder = Path(input_paths[0]).resolve().parent
    candidate = folder / "stitched.tiff"
    if not candidate.exists():
        return candidate
    n = 2
    while True:
        candidate = folder / f"stitched_{n}.tiff"
        if not candidate.exists():
            return candidate
        n += 1


def _detect_all_features(paths, detector_name, max_features, downscale):
    """Extract features for every path. Each image is loaded just long
    enough to grab its shape/dtype and a (possibly downscaled) grayscale
    copy for detection, then freed - the full-resolution pixel data is
    not kept around; compositing reloads it later from disk.
    """
    detector = features.create_detector(detector_name, max_features=max_features)
    feats, shapes, dtypes = [], [], []
    for i, p in enumerate(paths):
        img = io_utils.load_image(p)
        shapes.append(img.shape[:2])
        dtypes.append(img.dtype)
        gray = io_utils.to_gray_for_features(img)
        del img
        if downscale != 1.0:
            gray = cv2.resize(gray, None, fx=1.0 / downscale, fy=1.0 / downscale,
                               interpolation=cv2.INTER_AREA)
        kp, desc = features.detect_and_describe(gray, detector)
        points = np.float32([k.pt for k in kp]) if kp else np.zeros((0, 2), dtype=np.float32)
        feats.append({"points": points, "descriptors": desc})
        logger.info(_("pipeline.log.feature_count", i=i, name=Path(p).name, n=len(kp)))
    return feats, shapes, dtypes


def stitch(
    input_paths: list[str],
    output_path: str,
    detector: str = "sift",
    match_ratio: float = 0.75,
    reproj_thresh: float = 3.0,
    min_inliers: int = 12,
    max_features: int = 8000,
    downscale: float = 1.0,
    bundle_adjustment: bool = True,
    sharpen_power: float = 6.0,
    compression: str = "lzw",
    ref_index: int = 0,
    background: str = "transparent",
    interpolation: str = "cubic",
    auto_orient: bool = True,
    n_jobs: int = 1,
):
    """Stitch input_paths into a single TIFF at output_path. If the tiles
    form more than one disconnected group (no shared features between
    groups), a separate output is written per group as
    "<output>_partN.tiff" and a list of the written paths is returned.

    n_jobs > 1 parallelizes pairwise registration (the O(n^2) part) over
    a process pool - the more tiles, the more this helps; n_jobs=1 stays
    fully sequential in this process.

    background="transparent" (the default) writes an alpha channel, so
    areas no scan covers are see-through instead of a painted-in color;
    "white"/"black" paint them instead and write no alpha.

    auto_orient rotates the finished canvas by a multiple of 90 degrees
    so the majority of tiles come out the way they were scanned - it
    does nothing at all when the tiles already agree.
    """
    if len(input_paths) < 2:
        raise StitchError(_("pipeline.error.need_two_images"))

    logger.info(_("pipeline.log.loading_features", n=len(input_paths)))
    feats, shapes, dtypes = _detect_all_features(input_paths, detector, max_features, downscale)

    n = len(input_paths)
    total_pairs = n * (n - 1) // 2
    logger.info(_("pipeline.log.comparing_pairs", n=total_pairs))
    edges = registration.register_all_pairs(
        feats, method=detector, ratio=match_ratio, reproj_thresh=reproj_thresh,
        min_inliers=min_inliers, coord_scale=downscale, n_jobs=max(1, n_jobs),
    )
    del feats  # descriptors/points no longer needed past this point

    if not edges:
        raise StitchError(_("pipeline.error.no_matches"))

    groups = registration.connected_components(n, edges)
    written = []

    if len(groups) > 1:
        sizes = ", ".join(str(len(g)) for g in groups)
        logger.warning(_("pipeline.warning.disconnected_groups", n=len(groups), sizes=sizes))

    out_path = Path(output_path)
    for gi, group in enumerate(groups):
        group_set = set(group)
        group_edges = [e for e in edges if e.i in group_set and e.j in group_set]
        local_ref = group[0]

        tree_edges = registration.build_max_spanning_tree(n, group_edges)
        poses = registration.compute_initial_poses(n, tree_edges, ref_index=local_ref)

        if bundle_adjustment and len(group) > 2:
            poses = registration.bundle_adjust(n, group_edges, poses, ref_index=local_ref)

        if auto_orient:
            poses, applied_deg = registration.snap_output_orientation(poses, group)
            if applied_deg:
                logger.info(_("pipeline.log.auto_oriented", degrees=applied_deg))

        canvas_size, _offset, affine_mats = compositor.compute_canvas(shapes, poses)
        logger.info(_("pipeline.log.canvas_size", i=gi + 1, w=canvas_size[0], h=canvas_size[1], n=len(group)))

        dtype = dtypes[group[0]]
        canvas_w, canvas_h = canvas_size
        transparent = background == "transparent"
        # Under transparency the uncovered pixels still get a sane RGB
        # value (white) underneath alpha=0, so a viewer that ignores the
        # alpha channel shows the same thing 1.0.0 did rather than black.
        fill_value = 0.0 if background == "black" else float(np.iinfo(dtype).max)

        acc = None
        for idx in group:
            img = io_utils.load_image(input_paths[idx])
            if img.ndim == 3 and img.shape[2] == 4:
                img = img[..., :3]
            if acc is None:
                n_channels = 1 if img.ndim == 2 else img.shape[2]
                bytes_per_px = n_channels * 4 + 4 + (n_channels + 1 if transparent else 0)
                est_mb = (canvas_w * canvas_h * bytes_per_px) / (1024 ** 2)
                logger.info(_("pipeline.log.memory_estimate", mb=f"{est_mb:.0f}"))
                acc = compositor.StreamingCompositor(
                    canvas_size, n_channels, sharpen_power=sharpen_power,
                    fill_value=fill_value, interpolation=interpolation,
                )
            acc.add_tile(img, affine_mats[idx])
            del img

        result, covered = acc.finish()
        del acc
        result, covered = compositor.crop_to_content(result, covered)

        gap_pixels = int((~covered).sum())
        gap_fraction = gap_pixels / covered.size
        if gap_fraction > 0.003 and not transparent:
            logger.warning(_("pipeline.warning.gap_pixels", pixels=gap_pixels,
                              percent=f"{100 * gap_fraction:.1f}"))

        result = np.round(result).astype(dtype)
        if transparent:
            result = compositor.attach_alpha(result, covered, dtype)
        del covered

        if len(groups) == 1:
            dest = out_path
        else:
            dest = out_path.with_name(f"{out_path.stem}_part{gi + 1}{out_path.suffix}")
        io_utils.save_tiff(dest, result, compression=compression, has_alpha=transparent)
        del result
        logger.info(_("pipeline.log.saved", path=dest))
        written.append(str(dest))

    return written
