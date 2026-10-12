"""Feature detection and matching between image tiles.

Only used to find correspondences between overlapping scans; the actual
geometric model fitted from these correspondences is a rigid transform
(see registration.py), not a general homography.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

from .i18n import _

logger = logging.getLogger("flatstitch")


def create_detector(method: str = "sift", max_features: int = 8000):
    """Create a feature detector/descriptor.

    SIFT is preferred for accuracy and is patent-free in modern OpenCV
    (available directly as cv2.SIFT_create, no opencv-contrib needed).
    Falls back to ORB if SIFT is unavailable in the installed OpenCV build.
    """
    method = method.lower()
    if method == "sift":
        if hasattr(cv2, "SIFT_create"):
            return cv2.SIFT_create(nfeatures=max_features)
        logger.warning(_("features.warning.no_sift"))
        method = "orb"
    if method == "orb":
        return cv2.ORB_create(nfeatures=max_features)
    raise ValueError(_("features.error.unknown_detector", method=method))


def detect_and_describe(gray_image: np.ndarray, detector):
    """Run detection + description. Returns (keypoints, descriptors)."""
    keypoints, descriptors = detector.detectAndCompute(gray_image, None)
    return keypoints, descriptors


def match_descriptors(desc_a, desc_b, method: str = "sift", ratio: float = 0.75):
    """Match descriptors with kNN + Lowe's ratio test. Returns a list of
    cv2.DMatch (query index refers to desc_a, train index to desc_b)."""
    if desc_a is None or desc_b is None or len(desc_a) < 2 or len(desc_b) < 2:
        return []

    norm = cv2.NORM_HAMMING if method.lower() == "orb" else cv2.NORM_L2
    matcher = cv2.BFMatcher(norm)
    raw_matches = matcher.knnMatch(desc_a, desc_b, k=2)

    good = []
    for pair in raw_matches:
        if len(pair) != 2:
            continue
        m, n = pair
        if m.distance < ratio * n.distance:
            good.append(m)
    return good
