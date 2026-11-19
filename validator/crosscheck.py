#!/usr/bin/env python3
"""Pha 2 — Đối chiếu detection (YOLO) với panoptic (mask) để sinh ỨNG VIÊN lỗi gán nhãn.

  python validator/crosscheck.py runs/r36007/delivery --out runs/r36007/candidates.json
  python validator/crosscheck.py runs/r36007/delivery --out c.json --rules runs/r36007/rules.json

Chỉ đọc thư mục delivery được truyền vào, chỉ thư viện chuẩn (+ scripts/pngutil.py), không mạng.
Ứng viên KHÔNG phải lỗi: phải mở ảnh xem rồi ghi confirmed/rejected vào review_log.csv.

Validator luật (validate.py) gọi được trực tiếp:
    from crosscheck import find_candidates
    doc = find_candidates(Path(delivery), skip_images=<các ảnh đã có lỗi luật>)

Ý tưởng (xem GUIDE Pha 2):
  1. Đổi box YOLO sang pixel; đọc mask, gom pixel của từng segment thành các "run" theo hàng.
  2. Ghép box ↔ segment thing (một-một, tham lam theo IoU bbox, ưu tiên cùng lớp).
  3. Tín hiệu bằng PIXEL chứ không chỉ IoU:
     - cặp ghép khác lớp                                   -> class_swap | category_swap (xem ảnh để biết bên nào sai)
     - segment cùng lớp có nhiều pixel rơi ra ngoài box,
       hoặc box rộng hơn segment ở CẢ BỐN cạnh            -> bbox_drift
     - segment thing đủ lớn không có box nào               -> missing_box
     - một segment có pixel ở phần RIÊNG của hai box cùng lớp -> instance_merge
       (box COCO bao sát vật, nên pixel của một vật không thể nằm trong phần riêng của box vật khác)
  4. Bỏ qua ảnh đã có lỗi luật (mỗi ảnh mang nhiều nhất một lỗi), vì lỗi luật như thing_overlap
     hay duplicate_box tự nó làm lệch hai nguồn và sinh ứng viên giả.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pngutil  # noqa: E402

# Ngưỡng mặc định. Chỉnh bằng tham số dòng lệnh sau khi đo trên lô sạch (runs/clean),
# và ghi lý do chọn vào REPORT.md. KHÔNG chép tham số trong inject_release.py.
DEFAULTS = {
    "match_iou": 0.25,    # IoU bbox tối thiểu để coi box và segment là cùng một vật
    "cov": 0.93,          # tỉ lệ pixel segment nằm trong box; thấp hơn -> nghi bbox_drift
    "gap": 0.04,          # box rộng hơn segment ở cả 4 cạnh quá tỉ lệ này -> nghi bbox_drift (box bị phóng to)
    "poke": 0.02,         # segment thò ra ngoài box quá tỉ lệ này của cạnh box ...
    "poke_px": 2.0,       # ... và quá số pixel này -> nghi bbox_drift
    "merge_px": 200,      # instance_merge: số pixel tối thiểu nằm ở phần riêng của box thứ hai
    "merge_frac": 0.10,   # ... và phải lấp ít nhất tỉ lệ này diện tích phần riêng đó
    "min_seg_px": 500,    # segment nhỏ hơn thì không xét missing_box / instance_merge
}
SWAP = "class_swap|category_swap"


# ----------------------------------------------------------------------------- đọc lô
def load_delivery(d: Path) -> dict:
    classes = {}
    for line in (d / "classes.txt").read_text(encoding="utf-8").splitlines():
        if line.strip():
            idx, name = line.split(" ", 1)
            classes[int(idx)] = name.strip()
    pan = json.loads((d / "panoptic.json").read_text(encoding="utf-8"))
    cats = {c["id"]: c for c in pan["categories"]}
    size = {Path(im["file_name"]).stem: (im["width"], im["height"]) for im in pan["images"]}
    anns = {Path(a["file_name"]).stem: a for a in pan["annotations"]}
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))["items"]
    return {"classes": classes, "cats": cats, "size": size, "anns": anns, "manifest": manifest}


def read_boxes(path: Path, W: int, H: int, classes: dict) -> tuple[list[dict], bool]:
    """Box pixel [x0, y0, x1, y1]. Trả thêm cờ 'có dòng hỏng / lớp lạ / tràn mép' (lỗi luật)."""
    boxes, bad = [], False
    if not path.is_file():
        return boxes, True
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if not parts:
            continue
        try:
            c = int(float(parts[0]))
            x, y, w, h = map(float, parts[1:5])
        except (ValueError, IndexError):
            bad = True
            continue
        if c not in classes:
            bad = True
            continue
        if x - w / 2 < -0.01 or y - h / 2 < -0.01 or x + w / 2 > 1.01 or y + h / 2 > 1.01:
            bad = True
        boxes.append({"cls": classes[c], "rect": [(x - w / 2) * W, (y - h / 2) * H, (x + w / 2) * W, (y + h / 2) * H],
                      "wh": (round(w * W), round(h * H))})
    return boxes, bad


def has_duplicate(boxes: list[dict]) -> bool:
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if a["cls"] == b["cls"] and a["wh"] == b["wh"] and \
                    abs(a["rect"][0] - b["rect"][0]) <= 3 and abs(a["rect"][1] - b["rect"][1]) <= 3:
                return True
    return False


def scan_mask(path: Path) -> tuple[int, int, dict]:
    """sid -> {"n": số pixel, "bbox": [x0, y0, x1, y1) , "runs": [(y, xa, xb), ...]}"""
    W, H, px = pngutil.read_rgb(path)
    segs: dict[int, dict] = {}
    for y in range(H):
        base = y * W * 3
        ids = [px[i] | (px[i + 1] << 8) | (px[i + 2] << 16) for i in range(base, base + W * 3, 3)]
        start = 0
        for x in range(1, W + 1):
            if x == W or ids[x] != ids[start]:
                sid = ids[start]
                if sid:
                    s = segs.get(sid)
                    if s is None:
                        s = segs[sid] = {"n": 0, "bbox": [start, y, x, y + 1], "runs": []}
                    s["n"] += x - start
                    s["runs"].append((y, start, x))
                    bb = s["bbox"]
                    bb[0], bb[2], bb[3] = min(bb[0], start), max(bb[2], x), y + 1
                start = x
    return W, H, segs


# ----------------------------------------------------------------------------- hình học
def inside(seg: dict, rect, margin: float = 1.0) -> int:
    """Số pixel của segment có tâm nằm trong rect (nới margin pixel cho sai số làm tròn)."""
    x0, y0, x1, y1 = rect[0] - margin, rect[1] - margin, rect[2] + margin, rect[3] + margin
    total = 0
    for y, xa, xb in seg["runs"]:
        if y0 <= y + 0.5 < y1:
            lo = max(xa, int(-(-(x0 - 0.5) // 1)))   # ceil(x0 - 0.5)
            hi = min(xb, int(-(-(x1 - 0.5) // 1)))
            if hi > lo:
                total += hi - lo
    return total


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def area(r) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def intersect(a, b):
    r = [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]
    return r if r[2] > r[0] and r[3] > r[1] else None


def r1(rect) -> list:
    return [round(v, 1) for v in rect]


# ----------------------------------------------------------------------------- phân tích một ảnh
def analyze(name: str, boxes: list[dict], segs: list[dict], p: dict, pair_log: list | None = None) -> list[dict]:
    out = []

    def add(kind, score, why, bidx=(), seg=None, **extra):
        out.append({"image": name, "type": kind, "score": round(score, 3), "why": why,
                    "boxes": [{"cls": boxes[i]["cls"], "rect": r1(boxes[i]["rect"])} for i in bidx],
                    "segment": None if seg is None else {"id": seg["id"], "cls": seg["cls"], "bbox": seg["bbox"], "px": seg["n"]},
                    **extra})

    # 1. ghép một-một, tham lam
    pairs = []
    for bi, b in enumerate(boxes):
        for si, s in enumerate(segs):
            v = iou(b["rect"], s["bbox"])
            if v >= p["match_iou"]:
                pairs.append((v + (0.15 if b["cls"] == s["cls"] else 0.0), v, bi, si))
    pairs.sort(reverse=True)
    match_b, match_s = {}, {}
    for _, v, bi, si in pairs:
        if bi not in match_b and si not in match_s:
            match_b[bi], match_s[si] = si, v

    # 2. instance_merge: segment tràn ra khỏi box cùng lớp bao nó nhiều nhất, và phần tràn
    #    nằm gọn trong MỘT box cùng lớp khác. Vật thật thì nằm trong box của chính nó, nên dù
    #    các box cùng lớp chồng nhau, pixel của nó không rơi vào phần riêng của box vật khác.
    merged = set()
    for si, s in enumerate(segs):
        if s["n"] < p["min_seg_px"]:
            continue
        same = [bi for bi, b in enumerate(boxes) if b["cls"] == s["cls"]]
        if len(same) < 2:
            continue
        own = max(same, key=lambda bi: inside(s, boxes[bi]["rect"]))
        in_own = inside(s, boxes[own]["rect"])
        if s["n"] - in_own < p["merge_px"]:
            continue
        best = None
        for b2 in same:
            ra, rb = boxes[own]["rect"], boxes[b2]["rect"]
            if b2 == own or iou(ra, rb) >= 0.7:   # box gần trùng: chuyện của duplicate_box, không phải gộp
                continue
            both = intersect(ra, rb)
            extra = inside(s, rb) - (inside(s, both) if both else 0)
            excl_area = area(rb) - (area(both) if both else 0)     # diện tích phần riêng của box thứ hai
            if extra >= p["merge_px"] and extra >= p["merge_frac"] * excl_area and (in_own + extra) >= 0.8 * s["n"]:
                if best is None or extra > best[0]:
                    best = (extra, b2)
        if best:
            merged.add(si)
            sc = min(in_own, best[0]) / s["n"]
            add("instance_merge", sc, f"segment {s['cls']} trải trên 2 box {s['cls']}: {in_own / s['n']:.0%} "
                f"pixel trong box thứ nhất, {best[0] / s['n']:.0%} ở phần riêng của box thứ hai", (own, best[1]), s)

    # 3. các cặp đã ghép
    for bi, si in match_b.items():
        b, s = boxes[bi], segs[si]
        if si in merged:
            continue
        v = match_s[si]
        if b["cls"] != s["cls"]:
            add(SWAP, v, f"box và segment trùng vị trí (IoU {v:.2f}) nhưng detection ghi '{b['cls']}', "
                f"panoptic ghi '{s['cls']}'", (bi,), s, det_cls=b["cls"], pan_cls=s["cls"])
            continue
        cov = inside(s, b["rect"]) / s["n"]
        bx0, by0, bx1, by1 = b["rect"]
        sx0, sy0, sx1, sy1 = s["bbox"]
        bw, bh = max(bx1 - bx0, 1), max(by1 - by0, 1)
        gap = min((sx0 - bx0) / bw, (bx1 - sx1) / bw, (sy0 - by0) / bh, (by1 - sy1) / bh)
        # segment thò ra ngoài box: vật bị che chỉ MẤT pixel, không bao giờ thò ra ngoài box của chính nó
        out_px = max(bx0 - sx0, sx1 - bx1, by0 - sy0, sy1 - by1)
        out_rel = max((bx0 - sx0) / bw, (sx1 - bx1) / bw, (by0 - sy0) / bh, (sy1 - by1) / bh)
        poke = out_px > p["poke_px"] and out_rel > p["poke"]
        if pair_log is not None:
            pair_log.append({"image": name, "cls": s["cls"], "seg_px": s["n"], "iou": round(v, 3), "cov": round(cov, 4),
                             "gap": round(gap, 4), "poke_px": round(out_px, 1), "poke": round(out_rel, 4)})
        if cov < p["cov"] or gap > p["gap"] or poke:
            why = (f"{1 - cov:.0%} pixel segment nằm ngoài box" if cov < p["cov"]
                   else f"segment thò ra ngoài box {out_px:.0f} px ({out_rel:.0%} cạnh box)" if poke
                   else f"box rộng hơn segment ở cả 4 cạnh (≥ {gap:.0%})")
            add("bbox_drift", max(p["cov"] - cov, gap - p["gap"], out_rel - p["poke"], 0) + 0.01, why, (bi,), s,
                cov=round(cov, 3), gap=round(gap, 3), poke=round(out_rel, 3), iou=round(v, 3))

    # 4. segment không có box
    free_boxes = [bi for bi in range(len(boxes)) if bi not in match_b]
    for si, s in enumerate(segs):
        if si in match_s or si in merged or s["n"] < p["min_seg_px"]:
            continue
        near = sorted(((inside(s, boxes[bi]["rect"]) / s["n"], bi) for bi in free_boxes
                       if boxes[bi]["cls"] == s["cls"]), reverse=True)
        near = [x for x in near if x[0] >= 0.2]
        if near:
            cov, bi = near[0]
            if cov < p["cov"]:   # box cùng lớp lệch xa tới mức không ghép được
                add("bbox_drift", p["cov"] - cov + 0.01, f"box {s['cls']} lệch xa khỏi segment ({1 - cov:.0%} pixel ngoài box)",
                    (bi,), s, cov=round(cov, 3))
            # cov cao: segment nằm gọn trong box (vật bị che nhiều nên IoU thấp) -> lệch tự nhiên, bỏ
        else:
            add("missing_box", s["n"] / s["img_px"], f"segment {s['cls']} {s['n']} pixel không có box detection nào", (), s)
    return out


# ----------------------------------------------------------------------------- cả lô
def find_candidates(d: Path, skip_images: set | None = None, pair_log: list | None = None, **params) -> dict:
    p = {**DEFAULTS, **{k: v for k, v in params.items() if v is not None}}
    lot = load_delivery(d)
    skip_images = set(skip_images or ())
    cands, skipped = [], {}
    for name in sorted(lot["manifest"]):
        it = lot["manifest"][name]
        if name in skip_images:
            skipped[name] = "đã có lỗi luật"
            continue
        ann, wh = lot["anns"].get(name), lot["size"].get(name)
        mask = d / it["mask"]
        if ann is None or wh is None or not mask.is_file():
            skipped[name] = "thiếu annotation/mask"
            continue
        boxes, bad = read_boxes(d / it["label"], wh[0], wh[1], lot["classes"])
        W, H, stats = scan_mask(mask)
        segs, reason = [], None
        if bad:
            reason = "nhãn detection có lớp lạ/tràn mép"
        elif has_duplicate(boxes):
            reason = "có box lặp"
        for info in ann["segments_info"]:
            cat = lot["cats"].get(info["category_id"])
            st = stats.get(info["id"])
            if cat is None:
                reason = reason or "category_id lạ"
                continue
            if st is None or abs(st["n"] - info.get("area", st["n"])) > max(5, 0.01 * st["n"]):
                reason = reason or "area khai khác số pixel thật"
            if st is None or not cat.get("isthing") or info.get("iscrowd"):
                continue
            segs.append({**st, "id": info["id"], "cls": cat["name"], "img_px": W * H})
        if reason:     # ảnh đã mang lỗi luật -> không mang lỗi gán nhãn; tránh ứng viên giả
            skipped[name] = reason
            continue
        cands += analyze(name, boxes, segs, p, pair_log)
    cands.sort(key=lambda c: (-c["score"], c["image"]))
    by_type = {}
    for c in cands:
        by_type[c["type"]] = by_type.get(c["type"], 0) + 1
    return {"params": p, "images": len(lot["manifest"]), "skipped": skipped,
            "summary": {"candidates": len(cands), "images_with_candidates": len({c["image"] for c in cands}),
                        "by_type": by_type},
            "candidates": cands}


def rule_images(path: Path) -> set:
    errs = json.loads(path.read_text(encoding="utf-8"))["errors"]
    return {e["target"] for e in errs if e["type"] not in
            {"class_swap", "bbox_drift", "missing_box", "category_swap", "instance_merge", "scene_leak"}}


def main() -> int:
    ap = argparse.ArgumentParser(description="Đối chiếu detection ↔ panoptic, ghi candidates.json")
    ap.add_argument("delivery")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pairs-csv", help="ghi số đo của MỌI cặp box-segment cùng lớp (để chọn ngưỡng trên lô sạch)")
    ap.add_argument("--rules", help="errors.json/rules.json của validator luật: bỏ qua các ảnh đã có lỗi luật")
    for k, v in DEFAULTS.items():
        ap.add_argument("--" + k.replace("_", "-"), type=type(v), help=f"mặc định {v}")
    a = ap.parse_args()
    skip = rule_images(Path(a.rules)) if a.rules else set()
    log = [] if a.pairs_csv else None
    doc = find_candidates(Path(a.delivery), skip, log, **{k: getattr(a, k) for k in DEFAULTS})
    if log is not None:
        with open(a.pairs_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(log[0]) if log else ["image"])
            w.writeheader()
            w.writerows(log)
        for key, worst in (("cov", min), ("gap", max), ("poke", max)):
            vals = sorted(r[key] for r in log)
            if vals:
                print(f"  lô này: {key} tệ nhất = {worst(vals)}, 5 giá trị tệ nhất = "
                      f"{vals[:5] if worst is min else vals[-5:]}")
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    s = doc["summary"]
    print(f"{s['candidates']} ứng viên trên {s['images_with_candidates']} ảnh / {doc['images']} ảnh "
          f"(bỏ qua {len(doc['skipped'])} ảnh có lỗi luật) -> {a.out}")
    for k, v in sorted(s["by_type"].items()):
        print(f"  {k:28s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
