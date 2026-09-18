#!/usr/bin/env python3
"""Command-line interface for flatstitch."""
from __future__ import annotations

import argparse
import glob
import logging
import os
import sys

from . import i18n, pipeline
from .i18n import _

IMAGE_EXTS = ("*.tif", "*.tiff", "*.png", "*.jpg", "*.jpeg", "*.bmp")


def gather_input_paths(inputs: list[str]) -> list[str]:
    paths = []
    for item in inputs:
        if os.path.isdir(item):
            for ext in IMAGE_EXTS:
                paths.extend(sorted(glob.glob(os.path.join(item, ext))))
        else:
            matches = sorted(glob.glob(item))
            paths.extend(matches if matches else [item])
    seen = set()
    unique = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flatstitch",
        description=_("cli.description"),
    )
    p.add_argument("inputs", nargs="+", help=_("cli.help.inputs"))
    p.add_argument("-o", "--output", default=None, help=_("cli.help.output"))
    p.add_argument("--detector", choices=["sift", "orb"], default="sift",
                    help=_("cli.help.detector"))
    p.add_argument("--match-ratio", type=float, default=0.75,
                    help=_("cli.help.match_ratio"))
    p.add_argument("--reproj-thresh", type=float, default=3.0,
                    help=_("cli.help.reproj_thresh"))
    p.add_argument("--min-inliers", type=int, default=12,
                    help=_("cli.help.min_inliers"))
    p.add_argument("--max-features", type=int, default=8000,
                    help=_("cli.help.max_features"))
    p.add_argument("--downscale", type=float, default=1.0,
                    help=_("cli.help.downscale"))
    p.add_argument("--no-bundle-adjust", dest="bundle_adjustment", action="store_false",
                    help=_("cli.help.no_bundle_adjust"))
    p.add_argument("--sharpen-power", type=float, default=6.0,
                    help=_("cli.help.sharpen_power"))
    p.add_argument("--interpolation", choices=["cubic", "linear", "lanczos4", "nearest"],
                    default="cubic", help=_("cli.help.interpolation"))
    p.add_argument("--compression", choices=["lzw", "zlib", "none"], default="lzw",
                    help=_("cli.help.compression"))
    p.add_argument("--background", choices=["transparent", "white", "black"],
                    default="transparent", help=_("cli.help.background"))
    p.add_argument("--no-auto-orient", dest="auto_orient", action="store_false",
                    help=_("cli.help.no_auto_orient"))
    p.add_argument("--ref-index", type=int, default=0, help=_("cli.help.ref_index"))
    p.add_argument("--jobs", type=int, default=None, help=_("cli.help.jobs"))
    p.add_argument("--lang", choices=sorted(i18n.SUPPORTED_LANGUAGES), default=None,
                    help=_("cli.help.lang"))
    p.add_argument("-v", "--verbose", action="store_true", help=_("cli.help.verbose"))
    return p


def main(argv=None) -> int:
    # --lang has to be resolved before building the parser (so its own
    # --help text is in the right language), but argparse needs the full
    # parser to actually parse. Do a quick, tolerant pre-scan for it.
    argv = sys.argv[1:] if argv is None else argv
    for i, tok in enumerate(argv):
        if tok == "--lang" and i + 1 < len(argv):
            i18n.set_language(argv[i + 1])
            break
        if tok.startswith("--lang="):
            i18n.set_language(tok.split("=", 1)[1])
            break
    else:
        i18n.set_language(None)

    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    logger = logging.getLogger("flatstitch")

    paths = gather_input_paths(args.inputs)
    if len(paths) < 2:
        logger.error(_("cli.error.too_few_images"))
        return 1

    logger.info(_("cli.found_images", n=len(paths)))
    for p in paths:
        logger.info(f"  - {p}")

    output = args.output
    if not output:
        output = str(pipeline.default_output_path(paths))
        logger.info(_("cli.output_default", path=output))

    n_jobs = args.jobs if args.jobs is not None else (os.cpu_count() or 1)

    try:
        pipeline.stitch(
            input_paths=paths,
            output_path=output,
            detector=args.detector,
            match_ratio=args.match_ratio,
            reproj_thresh=args.reproj_thresh,
            min_inliers=args.min_inliers,
            max_features=args.max_features,
            downscale=args.downscale,
            bundle_adjustment=args.bundle_adjustment,
            sharpen_power=args.sharpen_power,
            interpolation=args.interpolation,
            compression=args.compression,
            ref_index=args.ref_index,
            background=args.background,
            auto_orient=args.auto_orient,
            n_jobs=n_jobs,
        )
    except pipeline.StitchError as e:
        logger.error(_("cli.error.prefix", error=e))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
