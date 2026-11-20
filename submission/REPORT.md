# Báo cáo nhóm 36

## 1. Số liệu luyện tập
- Seed 36007, trước review (chỉ lỗi luật): F1 lỗi luật ≈ 1.0, F1 tổng ≈ 0.7 (`score_findings.py`, làm tròn).
- Sau review thủ công 13/13 ảnh ứng viên (gộp 14 lỗi luật + 13 lỗi gán nhãn xác nhận = **27 lỗi**): **F1 tổng ≈ 0.9** (lỗi luật ≈ 1.0, lỗi gán nhãn ≈ 0.9). Truy vết đạt **F1 ≈ 1.0** trên cả `tier_a` (3 ảnh), `tier_b` (4 ảnh) và `notify` (4 người).
- Lô sạch (`--no-faults`, 50 ảnh pool calib): validator luật 0 lỗi; đối chiếu ra 1 ứng viên nhiễu (`bbox_drift`: box rộng hơn segment ở cả 4 cạnh, gap 0.046 > ngưỡng 0.04).
- Hàng đợi trên seed 36007: 13 ứng viên / 13 ảnh (4 swap, 3 `bbox_drift`, 5 `missing_box`, 1 `instance_merge`), bỏ qua 9 ảnh đã có lỗi luật (mỗi ảnh mang nhiều nhất một lỗi).
- Ngưỡng và cách chọn (không dùng `_private`): đo số đo của mọi cặp box–segment trên lô sạch (`--pairs-csv`): cov luôn = 1.0 (segment không bao giờ ra ngoài box của chính nó), poke tự nhiên ≤ 2 px, gap lớn nhất 0.046. Giữ cov 0.93, gap 0.04, poke 2 px & 2%: hàng đợi 13 ảnh xem hết được, ưu tiên không bỏ sót; ứng viên nhiễu được loại khi xem ảnh (`rejected`).

## 2. Vì sao báo hết ứng viên làm F1 giảm?
Ứng viên chỉ là chỗ hai nguồn nhãn lệch nhau, không phải lỗi chắc chắn. COCO vốn lệch tự nhiên (vật bị che, crowd): lô sạch 50 ảnh không có lỗi nào vẫn ra 1 ứng viên. Báo hết thì mỗi ứng viên nhiễu thành 1 FP, precision giảm, kéo F1 giảm, dù recall không đổi. Trên seed 36007, **0/13** ứng viên bị `rejected` sau khi xem ảnh (cả 13/13 đều là lỗi thật, `confirmed`); nếu không xem ảnh mà báo tràn lan trên các lô sạch hoặc ca biên thì các ca nhiễu sẽ trở thành FP làm giảm F1.

## 3. Quyết định và lineage
- **QC v1 (`lab250_758cc5ff59fa`):** Phát hiện **27/250** mục lỗi (**10.8%** > ngưỡng cho phép **2%**). Kết luận **HOLD** bản v1 trong `lineage.jsonl`, giữ nguyên toàn bộ thư mục `delivery/` gốc.
- **Bản vá v2 (`lab250_758cc5ff59fa_v2`):**
  - Tách **16 ảnh** lỗi nhãn detection hoặc thiếu file (`missing_image`: 1, `invalid_class`: 2, `bbox_out_of_bounds`: 1, `duplicate_box`: 2, `class_swap`: 2, `bbox_drift`: 3, `missing_box`: 5) sang `rework_queue.csv`, giao đúng `annotator`/`reviewer` theo `jobs.csv`.
  - Giữ lại **234 ảnh** (**1.622 nhãn** detection) trong v2; sửa `scene_leak` (`scene_378`: 1 cảnh), đồng bộ guideline theo `jobs.csv` (1 ảnh), cập nhật sha256 sau khi mở kiểm tra ảnh `wrong_hash` (2 ảnh); giữ **7 ảnh** chỉ lỗi panoptic (`invalid_category_id`: 2, `thing_overlap`: 2, `category_swap`: 2, `instance_merge`: 1) ở lại v2 và ghi chú báo team panoptic trong `README.txt`.
- **Vì sao R-021 phải xem lại hoặc train lại:** Model R-021 được gắn với bản v1 chứa 10.8% dữ liệu lỗi (thiếu box, lệch box, sai nhãn lớp, rò rỉ cảnh `scene_378` giữa train/val), dẫn đến sai lệch đặc trưng học và đánh giá; cần kiểm tra lại hoặc train lại trên bản sạch `lab250_758cc5ff59fa_v2`.

## 4. Hash chứng minh gì, không chứng minh gì?
- **Chứng minh được:** Tính toàn vẹn ở mức byte (file ảnh, file nhãn hoặc gói `.zip` không bị hỏng bit, thiếu byte hay chỉnh sửa trái phép so với thời điểm tính mã sha256 trong `manifest.csv` / `.sha256`).
- **Không chứng minh được:** Tính đúng đắn về mặt ngữ nghĩa của nhãn (một file nhãn gán sai tên lớp hoặc lệch bounding box vẫn có mã sha256 khớp hoàn toàn với file đã đóng gói).

## 5. Truy vết
- **Tiêu chí tầng `tier_a` và `tier_b`:** Đối chiếu `incoming/ticket.md` (team AI báo sót wine glass bị che/cắt mép tại `scene_324`, `scene_690`) và `guideline/changelog.md` (quy tắc mới bắt đầu từ 1.3, các job đầu dùng 1.2):
  - **`tier_a` (3 ảnh):** Ảnh thuộc `scene_324` hoặc `scene_690`, thuộc job dùng guideline cũ 1.2 theo `jobs.csv`, và có box wine glass.
  - **`tier_b` (4 ảnh):** Các ảnh khác trong lô cũng thuộc job guideline cũ 1.2 và có box wine glass nhưng chưa được team AI báo cáo.
  - **`notify` (4 người):** Các mã `annotator` và `reviewer` phụ trách các job chứa ảnh thuộc `tier_a` và `tier_b` trong `jobs.csv`.
- **Vì sao lấy guideline theo `jobs.csv`:** Trường `guideline_version` trong `metadata.json` của từng ảnh có thể bị sai do lỗi `guideline_mismatch`, nên phải căn cứ theo `jobs.csv` để xác định đúng phiên bản quy tắc áp dụng cho đợt gán nhãn đó.

## 6. Bug hoặc điểm chưa rõ trong lab (nếu có)
- Công cụ đối chiếu gán nhãn xuất ứng viên gộp dạng `class_swap|category_swap`, cần mở ảnh kiểm tra nguồn nào (detection hay panoptic) ghi đúng vật thể thực tế để phân loại chính xác, quyết định ảnh phải đưa vào `rework_queue.csv` hay giữ lại ở v2.
