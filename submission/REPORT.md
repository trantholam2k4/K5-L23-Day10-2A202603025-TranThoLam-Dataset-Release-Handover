# Báo cáo nhóm <<XX>>

## 1. Số liệu luyện tập
- Seed 36007, trước review (chỉ lỗi luật): F1 lỗi luật ≈ 1.0, F1 tổng ≈ 0.7 (score_findings, làm tròn).
- Lô sạch (`--no-faults`, 50 ảnh pool calib): validator luật 0 lỗi; đối chiếu ra 1 ứng viên nhiễu
  (bbox_drift: box rộng hơn segment ở cả 4 cạnh, gap 0.046 > ngưỡng 0.04).
- Hàng đợi trên seed 36007: 13 ứng viên / 13 ảnh (4 swap, 3 bbox_drift, 5 missing_box, 1 instance_merge),
  bỏ qua 9 ảnh đã có lỗi luật (mỗi ảnh mang nhiều nhất một lỗi).
- Ngưỡng và cách chọn (không dùng `_private`): đo số đo của mọi cặp box–segment trên lô sạch (`--pairs-csv`):
  cov luôn = 1.0 (segment không bao giờ ra ngoài box của chính nó), poke tự nhiên ≤ 2 px, gap lớn nhất 0.046.
  Giữ cov 0.93, gap 0.04, poke 2 px & 2%: hàng đợi 13 ảnh xem hết được, ưu tiên không bỏ sót;
  ứng viên nhiễu được loại khi xem ảnh (rejected).

## 2. Vì sao báo hết ứng viên làm F1 giảm?
Ứng viên chỉ là chỗ hai nguồn nhãn lệch nhau, không phải lỗi chắc chắn. COCO vốn lệch tự nhiên
(vật bị che, crowd): lô sạch 50 ảnh không có lỗi nào vẫn ra 1 ứng viên. Báo hết thì mỗi ứng viên
nhiễu thành 1 FP, precision giảm, kéo F1 giảm, dù recall không đổi. Trên seed 36007,
<<X>>/13 ứng viên bị rejected sau khi xem ảnh; nếu báo cả 13 thì <<X>> ca đó thành FP.

## 3. Quyết định và lineage
<<QC v1 a/b, kết luận, vì sao R-021 phải xem lại hoặc train lại.>>

## 4. Hash chứng minh gì, không chứng minh gì?
<<>>

## 5. Truy vết
<<Tiêu chí tầng a/b, vì sao lấy guideline theo job.>>

## 6. Bug hoặc điểm chưa rõ trong lab (nếu có)
<<>>
