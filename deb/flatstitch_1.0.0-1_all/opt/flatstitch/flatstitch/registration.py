"""Geometric registration between scanned tiles.

Core design decision: because inputs are flatbed scans (not photographs),
there is no perspective distortion and no scale change between tiles - a
tile placed on a scanner glass is reproduced 1:1. The only real-world
degrees of freedom between two overlapping scans are a small rotation
(the sheet was not placed perfectly straight) and a translation. Fitting
a full homography (as generic panorama tools do) has more freedom than
the physical problem actually has, and on flat, low-parallax content it
tends to introduce spurious keystoning/skew whenever the feature matches
are noisy. Constraining the model to a rigid transform (rotation +
translation, scale fixed at 1) uses the same matches more robustly and
cannot warp the content.

This module implements:
  - a closed-form least-squares rigid transform fit (Kabsch/orthogonal
    Procrustes) for 2D point sets,
  - a RANSAC wrapper around it for robustness to mismatches,
  - construction of a pose graph across many tiles and an initial
    global layout via a maximum-confidence spanning tree,
  - an optional global bundle adjustment pass that reduces drift by
    using ALL confirmed pairwise matches (not just the spanning tree),
    which lets loops in the overlap graph correct accumulated error.
"""
from __future__ import annotations

import concurrent.futures as cf
import itertools
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares

from . import features as feat_mod
from .i18n import _

logger = logging.getLogger("flatstitch")


# ---------------------------------------------------------------------------
# Rigid transform: dst ≈ src @ R.T + t
# ---------------------------------------------------------------------------

def kabsch_rigid_transform(src_pts: np.ndarray, dst_pts: np.ndarray):
    """Least-squares rigid (rotation + translation, NO scale) transform
    mapping src_pts -> dst_pts. src_pts, dst_pts: (N,2) arrays, N >= 2.

    Returns (R, t): R is a 2x2 rotation matrix, t is shape (2,), such that
    dst ≈ src @ R.T + t
    """
    src_mean = src_pts.mean(axis=0)
    dst_mean = dst_pts.mean(axis=0)
    src_c = src_pts - src_mean
    dst_c = dst_pts - dst_mean

    m = src_c.T @ dst_c  # 2x2
    u, _s, vt = np.linalg.svd(m)
    v = vt.T
    d = np.sign(np.linalg.det(v @ u.T)) or 1.0
    corr = np.diag([1.0, d])
    r = v @ corr @ u.T
    t = dst_mean - r @ src_mean
    return r, t


def invert_rt(r: np.ndarray, t: np.ndarray):
    r_inv = r.T
    t_inv = -r_inv @ t
    return r_inv, t_inv


def compose_rt(r1, t1, r2, t2):
    """Return (R, t) equivalent to applying (r1, t1) first, then (r2, t2):
    result(p) = r2 @ (r1 @ p + t1) + t2
    """
    r = r2 @ r1
    t = r2 @ t1 + t2
    return r, t


def apply_rt(r, t, pts: np.ndarray) -> np.ndarray:
    return pts @ r.T + t


def _ransac_iters_needed(inlier_ratio: float, sample_size: int, confidence: float) -> int:
    """Standard adaptive-RANSAC iteration bound: how many random samples
    are needed so that, with the given confidence, at least one sample
    was entirely inliers - given the best inlier ratio found so far.
    """
    if inlier_ratio <= 0.0:
        return np.iinfo(np.int32).max
    w_pow_s = inlier_ratio ** sample_size
    if w_pow_s >= 1.0:
        return 1
    denom = np.log1p(-w_pow_s)
    if denom == 0:
        return np.iinfo(np.int32).max
    return int(np.ceil(np.log1p(-confidence) / denom))


def estimate_rigid_ransac(
    src_pts: np.ndarray,
    dst_pts: np.ndarray,
    reproj_thresh: float = 3.0,
    max_iters: int = 3000,
    min_inliers: int = 8,
    confidence: float = 0.999,
    rng_seed: int = 12345,
):
    """RANSAC estimation of a 2D rigid transform (rotation + translation,
    scale fixed to 1). Returns a dict with R, t, inlier_mask, num_inliers,
    or None if estimation failed / too few inliers.

    Stops early once enough samples have been drawn that, at the current
    best inlier ratio, another all-inlier minimal sample is vanishingly
    unlikely to change the answer (the standard adaptive-RANSAC bound) -
    max_iters is a ceiling for pathological cases, not the typical count.
    """
    n = len(src_pts)
    if n < 2:
        return None

    rng = np.random.default_rng(rng_seed)
    best_mask = None
    best_count = -1
    dynamic_cap = max_iters

    it = 0
    while it < min(max_iters, dynamic_cap):
        it += 1
        idx = rng.choice(n, size=2, replace=False)
        if np.allclose(src_pts[idx[0]], src_pts[idx[1]]):
            continue
        r, t = kabsch_rigid_transform(src_pts[idx], dst_pts[idx])
        pred = apply_rt(r, t, src_pts)
        errs = np.linalg.norm(pred - dst_pts, axis=1)
        mask = errs < reproj_thresh
        count = int(mask.sum())
        if count > best_count:
            best_count = count
            best_mask = mask
            dynamic_cap = min(max_iters, _ransac_iters_needed(count / n, 2, confidence))

    if best_mask is None or best_count < min_inliers:
        return None

    # Refine using all current inliers, then re-check inliers once more.
    r, t = kabsch_rigid_transform(src_pts[best_mask], dst_pts[best_mask])
    pred = apply_rt(r, t, src_pts)
    errs = np.linalg.norm(pred - dst_pts, axis=1)
    mask = errs < reproj_thresh
    if mask.sum() >= 2:
        r, t = kabsch_rigid_transform(src_pts[mask], dst_pts[mask])

    return {
        "R": r,
        "t": t,
        "inlier_mask": mask,
        "num_inliers": int(mask.sum()),
    }


# ---------------------------------------------------------------------------
# Pairwise registration between two tiles' extracted features
# ---------------------------------------------------------------------------

def register_pair(
    feat_i: dict,
    feat_j: dict,
    method: str = "sift",
    ratio: float = 0.75,
    reproj_thresh: float = 3.0,
    coord_scale: float = 1.0,
):
    """Register tile i against tile j. Returns a dict with R, t (mapping
    i's local pixel coords -> j's local pixel coords), num_inliers, and
    the inlier point coordinates (src_inliers, dst_inliers), all rescaled
    back to full-resolution coordinates by coord_scale (use coord_scale =
    the downscale factor used before feature detection, or 1.0 if none).
    Returns None if there are not enough matches/inliers.

    feat_i/feat_j hold plain numpy arrays only ("points", "descriptors") -
    never cv2.KeyPoint objects - so this function (and the dicts passed to
    it) can cross a process boundary cleanly when run in parallel.
    """
    matches = feat_mod.match_descriptors(
        feat_i["descriptors"], feat_j["descriptors"], method=method, ratio=ratio
    )
    if len(matches) < 8:
        return None

    src_pts = np.float32([feat_i["points"][m.queryIdx] for m in matches])
    dst_pts = np.float32([feat_j["points"][m.trainIdx] for m in matches])

    result = estimate_rigid_ransac(src_pts, dst_pts, reproj_thresh=reproj_thresh)
    if result is None:
        return None

    mask = result["inlier_mask"]
    result["src_inliers"] = src_pts[mask] * coord_scale
    result["dst_inliers"] = dst_pts[mask] * coord_scale
    if coord_scale != 1.0:
        # Rotation is scale-invariant; translation scales with the coords.
        result["t"] = result["t"] * coord_scale
    return result


def _pair_task(args):
    """Module-level (picklable) unit of work for ProcessPoolExecutor."""
    i, j, feat_i, feat_j, method, ratio, reproj_thresh, coord_scale = args
    result = register_pair(feat_i, feat_j, method=method, ratio=ratio,
                            reproj_thresh=reproj_thresh, coord_scale=coord_scale)
    return i, j, result


def register_all_pairs(
    feats: list[dict],
    method: str = "sift",
    ratio: float = 0.75,
    reproj_thresh: float = 3.0,
    min_inliers: int = 12,
    coord_scale: float = 1.0,
    n_jobs: int = 1,
) -> list["Edge"]:
    """Register every pair of tiles (O(n^2) pairs). With n_jobs > 1, pairs
    are distributed across a process pool - this is the term that grows
    fastest as tile count increases, so it is the one worth parallelizing
    first. n_jobs=1 (default) runs sequentially in-process, which avoids
    process-start overhead entirely for small tile counts.
    """
    n = len(feats)
    pairs = list(itertools.combinations(range(n), 2))
    edges: list[Edge] = []

    def handle(i: int, j: int, result):
        if result is not None and result["num_inliers"] >= min_inliers:
            edges.append(Edge(
                i=i, j=j, r=result["R"], t=result["t"],
                num_inliers=result["num_inliers"],
                src_pts=result["src_inliers"], dst_pts=result["dst_inliers"],
            ))
            logger.info(_("registration.log.inliers", i=i, j=j, n=result['num_inliers']))

    if n_jobs <= 1 or len(pairs) <= 1:
        for i, j in pairs:
            _i_unused, _j_unused, result = _pair_task((i, j, feats[i], feats[j], method, ratio,
                                        reproj_thresh, coord_scale))
            handle(i, j, result)
        return edges

    logger.info(_("registration.log.parallel_start", n=n_jobs))
    tasks = [(i, j, feats[i], feats[j], method, ratio, reproj_thresh, coord_scale)
             for i, j in pairs]
    try:
        with cf.ProcessPoolExecutor(max_workers=n_jobs) as ex:
            futures = {ex.submit(_pair_task, task): (task[0], task[1]) for task in tasks}
            for fut in cf.as_completed(futures):
                i, j, result = fut.result()
                handle(i, j, result)
    except (OSError, PermissionError) as exc:
        # Some restricted/sandboxed environments cannot spawn subprocesses
        # at all; fall back to sequential rather than failing the whole run.
        logger.warning(_("registration.warning.parallel_unavailable", error=exc))
        edges.clear()
        for i, j in pairs:
            _i_unused, _j_unused, result = _pair_task((i, j, feats[i], feats[j], method, ratio,
                                        reproj_thresh, coord_scale))
            handle(i, j, result)
    # Multiprocessing completion order depends on OS scheduling, not pair
    # identity; sort back into a canonical order so the rest of the
    # pipeline (spanning tree ties, bundle-adjustment subsampling) behaves
    # identically regardless of n_jobs or run-to-run scheduling variance.
    edges.sort(key=lambda e: (e.i, e.j))
    return edges


# ---------------------------------------------------------------------------
# Pose graph: spanning tree for an initial layout, then optional bundle
# adjustment refinement using every confirmed pairwise edge.
# ---------------------------------------------------------------------------

@dataclass
class Edge:
    i: int
    j: int
    r: np.ndarray
    t: np.ndarray
    num_inliers: int
    src_pts: np.ndarray  # i's local coords
    dst_pts: np.ndarray  # j's local coords


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[ra] = rb
        return True

    def components(self, n):
        groups = defaultdict(list)
        for i in range(n):
            groups[self.find(i)].append(i)
        return list(groups.values())


def connected_components(n_images: int, edges: list[Edge]):
    uf = _UnionFind(n_images)
    for e in edges:
        uf.union(e.i, e.j)
    return uf.components(n_images)


def build_max_spanning_tree(n_images: int, edges: list[Edge]) -> list[Edge]:
    """Kruskal max-spanning-tree by inlier count (prefer strongest links)."""
    uf = _UnionFind(n_images)
    tree = []
    for e in sorted(edges, key=lambda e: -e.num_inliers):
        if uf.union(e.i, e.j):
            tree.append(e)
    return tree


def compute_initial_poses(n_images: int, tree_edges: list[Edge], ref_index: int = 0):
    """BFS the spanning tree to get an initial global pose (local -> canvas
    coords) for every image reachable from ref_index. Images not reachable
    (a different connected component) are absent from the returned dict.

    Edge convention: e.r, e.t map tile e.i's local coords -> tile e.j's
    local coords. Composition direction depends on which endpoint is
    already known during the traversal - see module docstring derivation.
    """
    adjacency = defaultdict(list)
    for e in tree_edges:
        # cur = e.i known -> new = e.j: must invert the stored transform
        adjacency[e.i].append((e.j, e.r, e.t, True))
        # cur = e.j known -> new = e.i: use the stored transform as-is
        adjacency[e.j].append((e.i, e.r, e.t, False))

    poses = {ref_index: (np.eye(2), np.zeros(2))}
    visited = {ref_index}
    queue = deque([ref_index])
    while queue:
        cur = queue.popleft()
        r_cur, t_cur = poses[cur]
        for nbr, r_edge, t_edge, need_invert in adjacency[cur]:
            if nbr in visited:
                continue
            r_use, t_use = invert_rt(r_edge, t_edge) if need_invert else (r_edge, t_edge)
            r_nbr, t_nbr = compose_rt(r_use, t_use, r_cur, t_cur)
            poses[nbr] = (r_nbr, t_nbr)
            visited.add(nbr)
            queue.append(nbr)
    return poses


def bundle_adjust(
    n_images: int,
    all_edges: list[Edge],
    initial_poses: dict,
    ref_index: int = 0,
    max_points_per_edge: int = 200,
):
    """Refine global poses by minimizing reprojection error across every
    confirmed pairwise edge at once (not just the spanning tree). Extra,
    non-tree edges create loops in the overlap graph; including them here
    is what lets the optimizer spread out accumulated drift instead of
    letting it build up along a single chain of tiles.
    """
    reachable = set(initial_poses.keys())
    order = [k for k in range(n_images) if k != ref_index and k in reachable]
    index_of = {k: idx for idx, k in enumerate(order)}

    def subsample(edge_i, edge_j, src, dst):
        if len(src) <= max_points_per_edge:
            return src, dst
        # Seed depends only on the edge's own (i, j) identity, not on
        # what order edges are processed in - so the subsample chosen for
        # a given edge is the same regardless of n_jobs or scheduling.
        rng = np.random.default_rng(edge_i * 100_003 + edge_j)
        idx = rng.choice(len(src), size=max_points_per_edge, replace=False)
        return src[idx], dst[idx]

    used_edges = []
    for e in all_edges:
        if e.i in reachable and e.j in reachable:
            s, d = subsample(e.i, e.j, e.src_pts, e.dst_pts)
            used_edges.append((e.i, e.j, s, d))

    if not used_edges or not order:
        return initial_poses

    def pose_from_params(params, k):
        if k == ref_index:
            return np.eye(2), np.zeros(2)
        idx = index_of[k]
        theta, tx, ty = params[3 * idx], params[3 * idx + 1], params[3 * idx + 2]
        c, s = np.cos(theta), np.sin(theta)
        return np.array([[c, -s], [s, c]]), np.array([tx, ty])

    def residuals(params):
        chunks = []
        for i, j, src, dst in used_edges:
            r_i, t_i = pose_from_params(params, i)
            r_j, t_j = pose_from_params(params, j)
            global_from_i = apply_rt(r_i, t_i, src)
            global_from_j = apply_rt(r_j, t_j, dst)
            chunks.append((global_from_i - global_from_j).ravel())
        return np.concatenate(chunks)

    x0 = np.zeros(3 * len(order))
    for k in order:
        r, t = initial_poses[k]
        theta = np.arctan2(r[1, 0], r[0, 0])
        idx = index_of[k]
        x0[3 * idx: 3 * idx + 3] = [theta, t[0], t[1]]

    logger.info(_("registration.log.bundle_adjust_start", n_images=len(order), n_edges=len(used_edges)))
    result = least_squares(residuals, x0, loss="huber", f_scale=2.0, method="trf")

    refined = {ref_index: (np.eye(2), np.zeros(2))}
    for k in order:
        refined[k] = pose_from_params(result.x, k)
    return refined
