#!/usr/bin/env python3
"""Truy vết ticket của team AI -> trace.json (chỉ dùng thư viện chuẩn, Python 3.10+).

Cách dùng:
    python tools/trace_query.py --delivery <thư_mục_delivery> --out trace.json

Cấu trúc delivery mong đợi (script tự tìm ở vài vị trí phổ biến):
    <delivery>/jobs.csv
    <delivery>/metadata.json
    <delivery>/classes.txt
    <delivery>/labels/<tên_ảnh>.txt        (định dạng YOLO: dòng nào cũng bắt đầu bằng class_id)
    <delivery>/incoming/ticket.md
    <delivery>/guideline/changelog.md

Quy tắc chính:
  * Phiên bản guideline của ảnh = cột `guideline` của JOB trong jobs.csv
    (KHÔNG dùng `guideline_version` trong metadata.json vì có thể lệch - guideline_mismatch).
  * Ảnh nghi ngờ = thuộc job guideline CŨ + có >= 1 box thuộc lớp bị đổi quy tắc.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path


# --------------------------------------------------------------------------- #
# Tìm file
# --------------------------------------------------------------------------- #
def find_file(root: Path, rel_candidates: list[str], name: str) -> Path:
    for rel in rel_candidates:
        p = root / rel
        if p.is_file():
            return p
    hits = sorted(root.rglob(name))
    hits = [h for h in hits if h.is_file()]
    if hits:
        return hits[0]
    sys.exit(f"Không tìm thấy {name} trong {root}")


# --------------------------------------------------------------------------- #
# ticket.md -> tập scene_id
# --------------------------------------------------------------------------- #
def parse_ticket_scenes(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8-sig")
    return set(re.findall(r"scene_\d+", text))


# --------------------------------------------------------------------------- #
# classes.txt -> {tên lớp: class_id}
# --------------------------------------------------------------------------- #
def parse_classes(path: Path) -> dict[str, int]:
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8-sig").splitlines()]
    lines = [ln for ln in lines if ln]
    # Hỗ trợ cả dạng "<id> <tên>" lẫn mỗi dòng một tên (id = số thứ tự dòng, từ 0).
    if lines and all(re.match(r"^\d+\s+\S", ln) for ln in lines):
        return {re.sub(r"^\d+\s+", "", ln).strip().lower(): int(ln.split()[0]) for ln in lines}
    return {name.lower(): i for i, name in enumerate(lines)}


# --------------------------------------------------------------------------- #
# changelog.md -> (phiên bản cũ, phiên bản mới, tên lớp bị đổi)
# --------------------------------------------------------------------------- #
def vkey(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def parse_changelog(path: Path, classes: dict[str, int]) -> tuple[str, str, set[int]]:
    text = path.read_text(encoding="utf-8-sig")
    # Tách theo heading "## vX.Y"
    parts = re.split(r"^#{1,6}\s*(v\d+(?:\.\d+)*)\s*$", text, flags=re.M)
    sections: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        sections[parts[i]] = parts[i + 1]
    if len(sections) < 2:
        sys.exit("changelog.md cần ít nhất 2 phiên bản (mới và cũ)")
    versions = sorted(sections, key=vkey)
    new_v, old_v = versions[-1], versions[-2]

    changed: set[int] = set()
    for token in re.findall(r"`([^`]+)`", sections[new_v]):
        key = token.strip().lower()
        if key in classes:
            changed.add(classes[key])
    if not changed:
        sys.exit(f"Không xác định được lớp nào bị đổi quy tắc trong mục {new_v} của changelog.md")
    return old_v, new_v, changed


# --------------------------------------------------------------------------- #
# jobs.csv / metadata.json / labels
# --------------------------------------------------------------------------- #
def read_jobs(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = csv.DictReader(f)
        return {
            r["job_id"].strip(): {k.strip(): (v or "").strip() for k, v in r.items()}
            for r in rows
        }


def read_items(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data["items"] if isinstance(data, dict) else data


def label_class_ids(label_path: Path) -> set[int]:
    ids: set[int] = set()
    if not label_path.is_file():
        return ids
    for ln in label_path.read_text(encoding="utf-8-sig").splitlines():
        tok = ln.split()
        if not tok:
            continue
        try:
            ids.add(int(float(tok[0])))
        except ValueError:
            continue
    return ids


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delivery", required=True, type=Path, help="thư mục delivery")
    ap.add_argument("--out", required=True, type=Path, help="đường dẫn trace.json")
    args = ap.parse_args()

    root: Path = args.delivery
    if not root.is_dir():
        sys.exit(f"Không phải thư mục: {root}")

    ticket_p = find_file(root, ["incoming/ticket.md", "ticket.md"], "ticket.md")
    change_p = find_file(root, ["guideline/changelog.md", "changelog.md"], "changelog.md")
    classes_p = find_file(root, ["classes.txt"], "classes.txt")
    jobs_p = find_file(root, ["jobs.csv"], "jobs.csv")
    meta_p = find_file(root, ["metadata.json"], "metadata.json")
    labels_dir = root / "labels"
    if not labels_dir.is_dir():
        hits = [p for p in root.rglob("labels") if p.is_dir()]
        if not hits:
            sys.exit(f"Không tìm thấy thư mục labels trong {root}")
        labels_dir = hits[0]

    ticket_scenes = parse_ticket_scenes(ticket_p)
    classes = parse_classes(classes_p)
    old_v, _new_v, changed_ids = parse_changelog(change_p, classes)

    jobs = read_jobs(jobs_p)
    items = read_items(meta_p)

    tier_a: set[str] = set()
    tier_b: set[str] = set()
    hit_jobs: set[str] = set()

    for it in items:
        job = jobs.get(str(it["job_id"]).strip())
        if job is None:
            continue
        # Phiên bản lấy từ jobs.csv, KHÔNG phải it["guideline_version"].
        if job["guideline"] != old_v:
            continue
        name = str(it["image"]).strip()
        if not (label_class_ids(labels_dir / f"{name}.txt") & changed_ids):
            continue
        (tier_a if str(it["scene_id"]).strip() in ticket_scenes else tier_b).add(name)
        hit_jobs.add(job["job_id"])

    notify = sorted({jobs[j][role] for j in hit_jobs for role in ("annotator", "reviewer") if jobs[j].get(role)})

    result = {"tier_a": sorted(tier_a), "tier_b": sorted(tier_b), "notify": notify}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"tier_a={len(tier_a)} tier_b={len(tier_b)} notify={len(notify)} -> {args.out}")


if __name__ == "__main__":
    main()
