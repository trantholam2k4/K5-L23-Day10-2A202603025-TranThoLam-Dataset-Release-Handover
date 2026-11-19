# Dùng AI

| # | Ai dùng | Công cụ | Prompt (tóm tắt) | AI sai ở đâu | Nhóm kiểm lại thế nào |
|---|---|---|---|---|---|
| 1 | Dương | Claude | Viết validator/crosscheck.py: ghép box YOLO với segment panoptic bằng pixel mask, sinh ứng viên 4 loại lỗi gán nhãn | Bản đầu báo nhầm instance_merge khi nhiều vật cùng lớp đứng chồng nhau; sót bbox_drift khi box vừa lệch vừa đổi cỡ | Chạy trên lô sạch (phải ~0 ứng viên) và dữ liệu thử; sửa tiêu chí: segment thò ra ngoài box, phần riêng của box thứ hai |
