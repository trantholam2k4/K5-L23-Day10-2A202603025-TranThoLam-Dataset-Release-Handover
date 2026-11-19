import argparse
import json
import struct
import sys
from pathlib import Path

# Đảm bảo import được các module từ thư mục gốc repo khi chạy: python validator/validate.py ...
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import module của Tá (Pha 1) và Dương (Pha 2)
try:
    from validator.rules_meta_panoptic import check_meta_and_panoptic
except ImportError:
    def check_meta_and_panoptic(delivery_dir: Path) -> list[dict]:
        return []

try:
    from validator.crosscheck import find_candidates
except ImportError:
    def find_candidates(delivery_dir: Path) -> list[dict]:
        return []


def get_jpeg_size(jpg_path: Path) -> tuple[int, int] | None:
    """Đọc kích thước (width, height) từ header file JPEG thuần thư viện chuẩn."""
    try:
        with jpg_path.open("rb") as f:
            if f.read(2) != b"\xff\xd8":
                return None
            while True:
                marker_prefix = f.read(1)
                if not marker_prefix:
                    return None
                if marker_prefix != b"\xff":
                    continue
                marker = f.read(1)
                while marker == b"\xff":
                    marker = f.read(1)
                if not marker or marker in (b"\xd8", b"\xd9"):
                    continue
                # SOF0, SOF1, SOF2 markers chứa kích thước ảnh
                if marker[0] in (0xC0, 0xC1, 0xC2):
                    f.read(3)  # length (2 bytes) + precision (1 byte)
                    h, w = struct.unpack(">HH", f.read(4))
                    return int(w), int(h)
                length_bytes = f.read(2)
                if len(length_bytes) < 2:
                    return None
                length = struct.unpack(">H", length_bytes)[0]
                f.seek(length - 2, 1)
    except OSError:
        return None


def load_valid_classes(delivery_dir: Path) -> set[int]:
    """Đọc classes.txt để lấy tập class_id hợp lệ (0..79)."""
    classes_file = delivery_dir / "classes.txt"
    valid_ids = set()
    if not classes_file.exists():
        return set(range(80))
    for line in classes_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if parts[0].isdigit():
            valid_ids.add(int(parts[0]))
    return valid_ids if valid_ids else set(range(80))


def load_image_sizes(delivery_dir: Path) -> dict[str, tuple[int, int]]:
    """Lấy (width, height) từ panoptic.json, bổ sung từ header ảnh thực tế nếu thiếu."""
    sizes: dict[str, tuple[int, int]] = {}
    panoptic_file = delivery_dir / "panoptic.json"
    if panoptic_file.exists():
        data = json.loads(panoptic_file.read_text(encoding="utf-8"))
        for img in data.get("images", []):
            stem = Path(img["file_name"]).stem
            sizes[stem] = (int(img["width"]), int(img["height"]))

    images_dir = delivery_dir / "images"
    if images_dir.exists():
        for img_path in images_dir.glob("*.jpg"):
            if img_path.stem not in sizes:
                dim = get_jpeg_size(img_path)
                if dim:
                    sizes[img_path.stem] = dim
    return sizes


def check_detection_rules(delivery_dir: Path) -> list[dict]:
    """
    Bắt 3 lỗi luật nhóm Detection:
    - invalid_class: class_id ngoài 0-79
    - bbox_out_of_bounds: box vượt ra ngoài khung ảnh [0, 1]
    - duplicate_box: box lặp lại cùng lớp, cùng kích thước, chỉ lệch 1-2 pixel
    """
    valid_classes = load_valid_classes(delivery_dir)
    image_sizes = load_image_sizes(delivery_dir)
    labels_dir = delivery_dir / "labels"

    errors: list[dict] = []
    if not labels_dir.exists():
        return errors

    for label_path in sorted(labels_dir.glob("*.txt")):
        stem = label_path.stem
        img_w, img_h = image_sizes.get(stem, (640, 480))

        boxes: list[tuple[int, float, float, float, float]] = []
        has_invalid_class = False
        has_oob = False

        for raw_line in label_path.read_text(encoding="utf-8").splitlines():
            parts = raw_line.strip().split()
            if len(parts) < 5:
                continue

            try:
                cls_id = int(float(parts[0]))
                xc, yc, w, h = map(float, parts[1:5])
            except ValueError:
                has_invalid_class = True
                continue

            # 1. invalid_class
            if cls_id not in valid_classes:
                has_invalid_class = True

            # 2. bbox_out_of_bounds
            x1, y1 = xc - w / 2.0, yc - h / 2.0
            x2, y2 = xc + w / 2.0, yc + h / 2.0
            eps = 1e-6
            if w <= 0 or h <= 0 or x1 < -eps or y1 < -eps or x2 > 1.0 + eps or y2 > 1.0 + eps:
                has_oob = True

            # Đổi sang pixel để đo độ lệch duplicate_box
            boxes.append((cls_id, xc * img_w, yc * img_h, w * img_w, h * img_h))

        # 3. duplicate_box: cùng lớp, cùng kích thước, lệch tâm 1-2 pixel
        has_dup = False
        n = len(boxes)
        for i in range(n):
            if has_dup:
                break
            c1, x1, y1, w1, h1 = boxes[i]
            for j in range(i + 1, n):
                c2, x2, y2, w2, h2 = boxes[j]
                if c1 != c2:
                    continue
                if abs(w1 - w2) <= 1.5 and abs(h1 - h2) <= 1.5:
                    if abs(x1 - x2) <= 2.5 and abs(y1 - y2) <= 2.5:
                        has_dup = True
                        break

        if has_invalid_class:
            errors.append({"type": "invalid_class", "target": stem})
        if has_oob:
            errors.append({"type": "bbox_out_of_bounds", "target": stem})
        if has_dup:
            errors.append({"type": "duplicate_box", "target": stem})

    return errors


def deduplicate_errors(errors: list[dict]) -> list[dict]:
    """Khử trùng lặp theo cặp (type, target) và giữ nguyên thứ tự."""
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for err in errors:
        key = (err["type"], err["target"])
        if key not in seen:
            seen.add(key)
            unique.append({"type": err["type"], "target": err["target"]})
    return unique


def main() -> None:
    parser = argparse.ArgumentParser(description="Dataset Delivery Validator")
    parser.add_argument("delivery_dir", type=Path, help="Đường dẫn thư mục delivery")
    parser.add_argument("--out", type=Path, required=True, help="Đường dẫn file errors.json")
    parser.add_argument("--candidates", type=Path, default=None, help="Đường dẫn file candidates.json")
    args = parser.parse_args()

    delivery_dir: Path = args.delivery_dir

    # Tổng hợp lỗi luật từ Detection (Lâm) + Meta/Panoptic (Tá)
    all_errors = []
    all_errors.extend(check_detection_rules(delivery_dir))
    all_errors.extend(check_meta_and_panoptic(delivery_dir))
    all_errors = deduplicate_errors(all_errors)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"errors": all_errors}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Xuất ứng viên đối chiếu (Dương) nếu truyền cờ --candidates
    if args.candidates is not None:
        candidates = find_candidates(delivery_dir)
        args.candidates.parent.mkdir(parents=True, exist_ok=True)
        args.candidates.write_text(
            json.dumps({"candidates": candidates}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()