#!/usr/bin/env python3
"""Render candidate reviews for the Day 10 dataset handover lab.

This is a human-review helper, not part of the validator.  It may use Pillow
to draw detection boxes and panoptic segment outlines on the delivery images.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


DEFAULT_SCALE = 2
MIN_DIMENSION = 400
MASK_ALPHA = 70
BOX_WIDTH = 3


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_classes(path: Path) -> dict[int, str]:
    classes: dict[int, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                raise ValueError(f"invalid classes.txt line {line_number}: {line!r}")
            classes[int(parts[0])] = parts[1]
    return classes


def load_candidates(path: Path) -> list[dict[str, str]]:
    payload = load_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("candidates"), dict):
        payload = payload["candidates"]
    if isinstance(payload, dict):
        rows = payload.get("candidates", payload.get("errors", []))
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError("candidates JSON must be a list or contain a candidates list")

    candidates: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"candidate {index} is not an object")
        target = str(row.get("target") or row.get("image") or "").strip()
        candidate_type = str(row.get("type", "")).strip()
        if not target or not candidate_type:
            raise ValueError(f"candidate {index} needs target/image and type")
        candidates.append({
            "target": Path(target).stem,
            "type": candidate_type,
            "detail": str(row.get("detail") or row.get("why") or ""),
        })
    return candidates


def load_panoptic(delivery: Path) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, Any]]]:
    payload = load_json(delivery / "panoptic.json")
    annotations = {
        Path(item["file_name"]).stem: item
        for item in payload.get("annotations", [])
    }
    categories = {int(item["id"]): item for item in payload.get("categories", [])}
    return annotations, categories


def load_yolo_boxes(
    label_path: Path,
    width: int,
    height: int,
    classes: dict[int, str],
) -> list[tuple[int, int, int, int, str]]:
    boxes: list[tuple[int, int, int, int, str]] = []
    if not label_path.is_file():
        return boxes
    with label_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            values = line.split()
            if len(values) != 5:
                continue
            try:
                class_id = int(values[0])
                xc, yc, box_width, box_height = (float(value) for value in values[1:])
            except ValueError:
                continue
            left = round((xc - box_width / 2) * width)
            top = round((yc - box_height / 2) * height)
            right = round((xc + box_width / 2) * width)
            bottom = round((yc + box_height / 2) * height)
            boxes.append((left, top, right, bottom, classes.get(class_id, f"class_{class_id}")))
    return boxes


def color_for(value: int) -> tuple[int, int, int]:
    # A deterministic palette gives the same segment the same colour on reruns.
    return (
        (value * 71 + 83) % 220 + 20,
        (value * 127 + 41) % 220 + 20,
        (value * 193 + 17) % 220 + 20,
    )


def draw_panoptic(
    image: Image.Image,
    mask_path: Path,
    annotation: dict[str, Any] | None,
    categories: dict[int, dict[str, Any]],
) -> None:
    if annotation is None or not mask_path.is_file():
        return
    with Image.open(mask_path) as mask_source:
        mask = mask_source.convert("RGB")
    if mask.size != image.size:
        mask = mask.resize(image.size, Image.Resampling.NEAREST)
    mask_pixels = list(mask.getdata())
    segment_by_id = {int(item["id"]): item for item in annotation.get("segments_info", [])}
    visible_segments = {
        segment_id: item
        for segment_id, item in segment_by_id.items()
        if not item.get("iscrowd") and categories.get(int(item["category_id"]), {}).get("isthing")
    }
    if not visible_segments:
        return

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_pixels = overlay.load()
    width, height = image.size
    ids = [red + green * 256 + blue * 65536 for red, green, blue in mask_pixels]
    for index, segment_id in enumerate(ids):
        if segment_id in visible_segments:
            red, green, blue = color_for(segment_id)
            overlay_pixels[index % width, index // width] = (red, green, blue, MASK_ALPHA)
    image.alpha_composite(overlay)

    draw = ImageDraw.Draw(image)
    for segment_id, segment in visible_segments.items():
        category = categories.get(int(segment["category_id"]), {})
        name = str(category.get("name", segment["category_id"]))
        box = segment.get("bbox", [0, 0, 0, 0])
        left, top, box_width, box_height = (int(round(float(value))) for value in box)
        right, bottom = left + box_width, top + box_height
        colour = color_for(segment_id)
        # Draw a mask boundary by comparing each segment pixel with neighbours.
        for y in range(max(0, top), min(height, bottom)):
            for x in range(max(0, left), min(width, right)):
                index = y * width + x
                if ids[index] != segment_id:
                    continue
                neighbours = []
                if x > 0:
                    neighbours.append(ids[index - 1])
                if x + 1 < width:
                    neighbours.append(ids[index + 1])
                if y > 0:
                    neighbours.append(ids[index - width])
                if y + 1 < height:
                    neighbours.append(ids[index + width])
                if any(neighbour != segment_id for neighbour in neighbours):
                    draw.point((x, y), fill=colour)
        draw.text((left + 2, top + 2), f"mask: {name}", fill=colour)


def draw_boxes(image: Image.Image, boxes: list[tuple[int, int, int, int, str]]) -> None:
    draw = ImageDraw.Draw(image)
    for left, top, right, bottom, name in boxes:
        draw.rectangle((left, top, right, bottom), outline=(255, 45, 35), width=BOX_WIDTH)
        draw.text((left + 2, max(0, top - 14)), f"det: {name}", fill=(255, 45, 35))


def write_review_log(path: Path, candidates: list[dict[str, str]]) -> None:
    seen: set[tuple[str, str]] = set()
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("image", "type", "verdict", "note"))
        for candidate in candidates:
            key = candidate["target"], candidate["type"]
            if key in seen:
                continue
            seen.add(key)
            writer.writerow((candidate["target"], candidate["type"], "", ""))


def render_candidate(
    delivery: Path,
    output: Path,
    target: str,
    classes: dict[int, str],
    annotations: dict[str, dict[str, Any]],
    categories: dict[int, dict[str, Any]],
) -> bool:
    image_path = delivery / "images" / f"{target}.jpg"
    if not image_path.is_file():
        print(f"warning: image not found, skipping render: {image_path}")
        return False
    with Image.open(image_path) as source:
        image = source.convert("RGBA")
    width, height = image.size
    boxes = load_yolo_boxes(delivery / "labels" / f"{target}.txt", width, height, classes)
    draw_panoptic(
        image,
        delivery / "masks" / f"{target}.png",
        annotations.get(target),
        categories,
    )
    draw_boxes(image, boxes)
    if min(width, height) < MIN_DIMENSION:
        image = image.resize((width * DEFAULT_SCALE, height * DEFAULT_SCALE), Image.Resampling.BILINEAR)
    output_path = output / f"{target}_review.jpg"
    image.convert("RGB").save(output_path, quality=95)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render Day 10 candidate review images")
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    delivery = args.delivery.resolve()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)

    candidates = load_candidates(args.candidates)
    classes = load_classes(delivery / "classes.txt")
    annotations, categories = load_panoptic(delivery)
    targets = sorted({candidate["target"] for candidate in candidates})

    rendered = 0
    for target in targets:
        if render_candidate(delivery, output, target, classes, annotations, categories):
            rendered += 1
    write_review_log(output / "review_log.csv", candidates)
    print(f"rendered {rendered}/{len(targets)} candidate images")
    print(f"review log: {output / 'review_log.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
