#!/usr/bin/env python3
"""Chuẩn bị review bằng mắt: vẽ box detection + segment panoptic của từng ứng viên, chia hàng đợi.

  pip install pillow            # chỉ công cụ xem ảnh dùng Pillow; validator thì không (RULES.md)
  python tools/make_review.py runs/r36007/delivery runs/r36007/candidates.json \
      --out runs/r36007/review --people "Tá,Lâm,Dương,Thức,Thái"

Tạo trong --out:
  NNN_<ảnh>_<loại>.jpg   trái: cả ảnh; phải: vùng quanh vật, phóng to
                         ĐỎ  = box detection của ứng viên (nhãn "DET <lớp>")
                         XANH = segment panoptic của ứng viên (tô mờ + viền, nhãn "PAN <lớp>")
                         VÀNG mảnh = các box detection khác trong ảnh
  review_queue.csv       image,type,verdict,note,reviewer,file,why  -> mỗi người điền dòng của mình
  index.html             xem cả hàng đợi trong trình duyệt

Công cụ KHÔNG điền verdict/note. Người xem ảnh tự ghi:
  - verdict: confirmed (đúng là lỗi) hoặc rejected (lệch tự nhiên: vật bị che, crowd, ...)
  - type: với ứng viên "class_swap|category_swap", nhìn ảnh rồi sửa thành MỘT loại:
        class_swap    = detection ghi sai lớp (panoptic đúng)
        category_swap = panoptic ghi sai lớp (detection đúng)
  - note (≥ 20 ký tự, không chép giống nhau): nói rõ THẤY GÌ, ví dụ "xe tải mui kín, detection ghi car".
Sau đó gộp các dòng (bỏ cột file/why/reviewer nếu muốn) thành runs/r<seed>/review_log.csv.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
except ImportError:
    raise SystemExit("Cần Pillow cho công cụ xem ảnh: pip install pillow")

RED, GREEN, YELLOW = (235, 40, 40), (40, 220, 90), (250, 210, 40)


def font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:   # Pillow cũ
        return ImageFont.load_default()


def seg_mask(mask_rgb: Image.Image, sid: int) -> Image.Image:
    r, g, b = mask_rgb.split()
    m = r.point(lambda v: 255 if v == (sid & 255) else 0)
    m = ImageChops.multiply(m, g.point(lambda v: 255 if v == ((sid >> 8) & 255) else 0))
    return ImageChops.multiply(m, b.point(lambda v: 255 if v == ((sid >> 16) & 255) else 0))


def label(draw, xy, text, color, f):
    x, y = xy
    l, t, r, b = draw.textbbox((x, y), text, font=f)
    draw.rectangle([l - 2, t - 1, r + 2, b + 1], fill=(0, 0, 0))
    draw.text((x, y), text, fill=color, font=f)


def render(img: Image.Image, mask_rgb: Image.Image | None, cand: dict, others: list, scale: float, f) -> Image.Image:
    """Vẽ lên bản phóng to `scale` lần của img."""
    W, H = img.size
    big = img.resize((max(1, round(W * scale)), max(1, round(H * scale))), Image.LANCZOS).convert("RGB")
    seg = cand.get("segment")
    if seg and mask_rgb is not None:
        m = seg_mask(mask_rgb, seg["id"]).resize(big.size, Image.NEAREST)
        tint = Image.new("RGB", big.size, GREEN)
        big = Image.composite(Image.blend(big, tint, 0.35), big, m)
        edge = ImageChops.subtract(m, m.filter(ImageFilter.MinFilter(5)))
        big.paste(GREEN, mask=edge)
    d = ImageDraw.Draw(big)
    for rect in others:
        d.rectangle([v * scale for v in rect], outline=YELLOW, width=1)
    for b in cand.get("boxes", []):
        x0, y0, x1, y1 = [v * scale for v in b["rect"]]
        d.rectangle([x0, y0, x1, y1], outline=RED, width=3)
        label(d, (x0 + 3, max(0, y0 - 16) if y0 > 16 else y0 + 3), f"DET {b['cls']}", RED, f)
    if seg:
        x0, y0, x1, y1 = [v * scale for v in seg["bbox"]]
        label(d, (x0 + 3, min(big.size[1] - 16, y1 + 2)), f"PAN {seg['cls']}", GREEN, f)
    return big


def main() -> int:
    ap = argparse.ArgumentParser(description="Vẽ ứng viên và chia hàng đợi review")
    ap.add_argument("delivery")
    ap.add_argument("candidates")
    ap.add_argument("--out", required=True)
    ap.add_argument("--people", default="", help="tên người xem, cách nhau bằng dấu phẩy; chia vòng tròn")
    ap.add_argument("--zoom-size", type=int, default=560, help="cạnh dài của khung phóng to (pixel)")
    a = ap.parse_args()

    d, out = Path(a.delivery), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))["items"]
    doc = json.loads(Path(a.candidates).read_text(encoding="utf-8"))
    cands = doc["candidates"]
    people = [p.strip() for p in a.people.split(",") if p.strip()] or [""]
    classes = {}
    for line in (d / "classes.txt").read_text(encoding="utf-8").splitlines():
        if line.strip():
            i, n = line.split(" ", 1)
            classes[int(i)] = n.strip()
    f = font(14)

    # Gom các ứng viên cùng ảnh cho một người xem (ảnh đã mở thì xem luôn)
    images = []
    for c in cands:
        if c["image"] not in images:
            images.append(c["image"])
    owner = {im: people[i % len(people)] for i, im in enumerate(images)}

    rows = []
    for n, c in enumerate(sorted(cands, key=lambda c: (people.index(owner[c["image"]]), images.index(c["image"]))), 1):
        it = manifest[c["image"]]
        jpg = d / it["image"]
        if not jpg.is_file():
            continue
        img = Image.open(jpg).convert("RGB")
        W, H = img.size
        mask_rgb = Image.open(d / it["mask"]).convert("RGB") if (d / it["mask"]).is_file() else None
        others = []
        lab = d / it["label"]
        for line in lab.read_text(encoding="utf-8").splitlines() if lab.is_file() else []:
            p = line.split()
            if len(p) >= 5:
                x, y, w, h = map(float, p[1:5])
                r = [(x - w / 2) * W, (y - h / 2) * H, (x + w / 2) * W, (y + h / 2) * H]
                if all(max(abs(r[k] - b["rect"][k]) for k in range(4)) > 1 for b in c["boxes"]):
                    others.append(r)

        full = render(img, mask_rgb, c, others, 480 / H if H < 480 else 1.0, f)
        if full.size[1] > 480:
            full = full.resize((round(full.size[0] * 480 / full.size[1]), 480), Image.LANCZOS)
        # vùng quan tâm = hợp các box + bbox segment, nới 30%
        rects = [b["rect"] for b in c["boxes"]] + ([c["segment"]["bbox"]] if c.get("segment") else [])
        x0, y0 = min(r[0] for r in rects), min(r[1] for r in rects)
        x1, y1 = max(r[2] for r in rects), max(r[3] for r in rects)
        pad = 0.3 * max(x1 - x0, y1 - y0, 40)
        x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
        s = a.zoom_size / max(x1 - x0, y1 - y0)
        zoom = render(img, mask_rgb, c, others, s, f).crop([round(v * s) for v in (x0, y0, x1, y1)])

        head = 26
        canvas = Image.new("RGB", (full.size[0] + zoom.size[0] + 12, max(full.size[1], zoom.size[1]) + head), (25, 25, 25))
        canvas.paste(full, (0, head))
        canvas.paste(zoom, (full.size[0] + 12, head))
        dd = ImageDraw.Draw(canvas)
        title = f"#{n} {c['image']}  {c['type']}  score={c['score']}"
        if c.get("det_cls"):
            title += f"  DET={c['det_cls']} / PAN={c['pan_cls']}"
        dd.text((6, 5), title, fill=(255, 255, 255), font=f)
        name = f"{n:03d}_{c['image']}_{c['type'].split('|')[0]}.jpg"
        canvas.save(out / name, quality=92)
        rows.append({"image": c["image"], "type": c["type"], "verdict": "", "note": "",
                     "reviewer": owner[c["image"]], "file": name, "why": c["why"]})

    with (out / "review_queue.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["image", "type", "verdict", "note", "reviewer", "file", "why"])
        w.writeheader()
        w.writerows(rows)

    parts = ["<!doctype html><meta charset='utf-8'><title>Review queue</title>",
             "<style>body{font-family:sans-serif;background:#111;color:#eee;margin:20px}"
             "img{max-width:100%;border:1px solid #444}.c{margin:0 0 28px}h2{margin-top:40px}"
             "code{background:#333;padding:1px 4px}</style>",
             f"<h1>Hàng đợi review: {len(rows)} ứng viên</h1>",
             "<p>ĐỎ = box detection, XANH = segment panoptic, VÀNG = box detection khác. "
             "Ghi kết luận vào <code>review_queue.csv</code>: confirmed/rejected + thấy gì trong ảnh.</p>"]
    for person in people:
        mine = [r for r in rows if r["reviewer"] == person]
        if not mine:
            continue
        parts.append(f"<h2>{html.escape(person or 'Hàng đợi')} — {len(mine)} ứng viên</h2>")
        for r in mine:
            parts.append(f"<div class='c'><b>{html.escape(r['file'])}</b> · {html.escape(r['type'])}<br>"
                         f"{html.escape(r['why'])}<br><img src='{html.escape(r['file'])}' loading='lazy'></div>")
    (out / "index.html").write_text("\n".join(parts), encoding="utf-8")
    print(f"{len(rows)} ứng viên -> {out}/ (review_queue.csv, index.html)")
    for person in people:
        print(f"  {person or '-'}: {sum(r['reviewer'] == person for r in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
