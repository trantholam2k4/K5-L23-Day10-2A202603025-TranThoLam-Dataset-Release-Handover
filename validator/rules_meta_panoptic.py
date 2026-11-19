"""Metadata and panoptic quality checks for the lab dataset.

Python 3.10+, standard library only, plus the project's scripts/pngutil.py.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import struct
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _load_pngutil(delivery_dir: Path):
    """Load the supplied PNG helper without changing sys.path."""
    helper = delivery_dir / "scripts" / "pngutil.py"
    if not helper.is_file():
        # Also support running this validator from a checkout where the helper
        # sits beside the validator package rather than inside delivery data.
        helper = Path(__file__).resolve().parents[1] / "scripts" / "pngutil.py"
    spec = importlib.util.spec_from_file_location("_lab_pngutil", helper)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load PNG helper: {helper}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe_delivery_path(root: Path, relative: str | Path) -> Path | None:
    """Resolve a dataset-relative path, refusing traversal and _private."""
    rel = Path(relative)
    if rel.is_absolute() or "_private" in rel.parts:
        return None
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if "_private" in candidate.relative_to(root).parts:
        return None
    return candidate


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def check_meta_and_panoptic(delivery_dir: Path) -> list[dict]:
    """Return unique metadata/panoptic QC errors for ``delivery_dir``."""
    root = Path(delivery_dir).resolve()

    manifest = _load_json(root / "manifest.json")
    metadata = _load_json(root / "metadata.json")
    panoptic = _load_json(root / "panoptic.json")

    manifest_items = manifest.get("items", {})
    metadata_items = metadata.get("items", [])
    if not isinstance(manifest_items, dict):
        manifest_items = {}
    if not isinstance(metadata_items, list):
        metadata_items = []

    errors: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(error_type: str, target: str) -> None:
        key = (error_type, str(target))
        if key not in seen:
            seen.add(key)
            errors.append({"type": error_type, "target": str(target)})

    # Load job_id -> guideline mapping from jobs.csv.
    jobs: dict[str, str] = {}
    jobs_path = root / "jobs.csv"
    with jobs_path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            job_id = row.get("job_id")
            guideline = row.get("guideline")
            if job_id and guideline:
                jobs[job_id] = guideline

    # Rule 1 + 2: image presence and SHA-256.
    for image_key, item in manifest_items.items():
        if not isinstance(item, dict):
            continue
        image_rel = item.get("image") or f"images/{image_key}.jpg"
        image_path = _safe_delivery_path(root, image_rel)
        target = Path(str(image_rel)).stem or str(image_key)
        if image_path is None or not image_path.is_file():
            add("missing_image", target)
            continue

        expected_hash = item.get("image_sha256")
        if expected_hash:
            digest = hashlib.sha256()
            with image_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest().lower() != str(expected_hash).strip().lower():
                add("wrong_hash", target)

    # Rule 3: a scene_id must not occur in both train and val.
    scene_splits: dict[str, set[str]] = defaultdict(set)
    for item in metadata_items:
        if not isinstance(item, dict):
            continue
        scene_id = item.get("scene_id")
        split = item.get("split")
        if scene_id is not None and split in ("train", "val"):
            scene_splits[str(scene_id)].add(str(split))
    for scene_id, splits in scene_splits.items():
        if {"train", "val"}.issubset(splits):
            add("scene_leak", scene_id)

    # Rule 4: compare each metadata guideline version with its job guideline.
    for item in metadata_items:
        if not isinstance(item, dict):
            continue
        image = item.get("image")
        job_id = item.get("job_id")
        version = item.get("guideline_version")
        if image is None or job_id is None or version is None:
            continue
        expected = jobs.get(str(job_id))
        if expected is not None and str(version) != expected:
            add("guideline_mismatch", Path(str(image)).stem)

    # Panoptic category lookup and annotation lookup by image basename.
    categories = {
        category.get("id"): category
        for category in panoptic.get("categories", [])
        if isinstance(category, dict) and "id" in category
    }
    annotations_by_stem: dict[str, dict[str, Any]] = {}
    for annotation in panoptic.get("annotations", []):
        if isinstance(annotation, dict) and annotation.get("file_name"):
            annotations_by_stem[Path(str(annotation["file_name"])).stem] = annotation

    # Rule 5 + 6: invalid category IDs and declared area exceeding actual mask pixels.
    pngutil = _load_pngutil(root)
    for annotation in annotations_by_stem.values():
        image_stem = Path(str(annotation.get("file_name", ""))).stem
        segments = annotation.get("segments_info", [])
        if not image_stem or not isinstance(segments, list):
            continue

        invalid_category = False
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            if segment.get("category_id") not in categories:
                invalid_category = True
                break
        if invalid_category:
            add("invalid_category_id", image_stem)

        # Masks are stored under masks/<image_name>.png in this lab.
        mask_path = _safe_delivery_path(root, Path("masks") / f"{image_stem}.png")
        if mask_path is None or not mask_path.is_file():
            continue
        try:
            width, height, pixels = pngutil.read_rgb(mask_path)
        except (OSError, ValueError, struct.error):
            continue

        pixel_counts: Counter[int] = Counter()
        for index in range(0, len(pixels), 3):
            segment_id = pngutil.segment_id(pixels, index)
            if segment_id != 0:
                pixel_counts[segment_id] += 1

        has_overlap = False
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            segment_id = segment.get("id")
            declared_area = segment.get("area")
            if segment_id is None or declared_area is None:
                continue
            try:
                if int(declared_area) > pixel_counts.get(int(segment_id), 0):
                    has_overlap = True
                    break
            except (TypeError, ValueError):
                continue
        if has_overlap:
            add("thing_overlap", image_stem)

    return errors
