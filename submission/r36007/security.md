# Bảo mật khi làm việc

> Trả lời gắn với lô bạn vừa bàn giao, không chép slide. Mỗi mục vài câu.

## 1. Ca USB: hết giờ, muốn chép lô về nhà làm tiếp
Tuyệt đối không chép dữ liệu của lô lab250_758cc5ff59fa ra USB cá nhân, không gửi qua email cá nhân hay chat ngoài, và không nhờ người ngoài làm hộ vì làm rò rỉ dữ liệu nội bộ, vi phạm quy định bảo mật và mất dấu truy vết trên hệ thống. Khi hết giờ, cần lưu tiến độ trên máy trạm được cấp quyền, đẩy file làm việc lên kho lưu trữ/CVAT nội bộ của dự án rồi tiếp tục vào ca sau hoặc xin cấp quyền truy cập từ xa hợp lệ.

## 2. Năm việc: được hay không
- Mở job được giao trên máy công ty: Được, vì thực hiện đúng phạm vi phân công trong jobs.csv và trên thiết bị an toàn đã kiểm soát.
- Chụp màn hình gửi người quen: Không được, vì làm lộ dữ liệu ảnh và quy tắc gán nhãn nội bộ ra ngoài tổ chức.
- Chép lô ra USB: Không được, vì thiết bị lưu trữ cá nhân dễ thất lạc, nhiễm mã độc và thoát khỏi kiểm soát truy cập.
- Dán nhãn thật lên chatbot: Không được, vì đưa dữ liệu dự án lên dịch vụ bên thứ ba làm rò rỉ thông tin chưa công bố.
- Đưa tài khoản CVAT cho đồng nghiệp: Không được, vì vi phạm nguyên tắc định danh cá nhân và làm sai lệch lịch sử annotator/reviewer khi truy vết.

## 3. Ca khuôn mặt: thấy mặt người rõ trong lô, guideline yêu cầu làm mờ
Khi phát hiện ảnh trong lô (ví dụ ảnh 000000012639 có khuôn mặt cậu bé nhìn rõ hoặc ảnh 000000000872 có mặt người rõ) chưa được làm mờ đúng yêu cầu guideline, phải lập tức đặt trạng thái lô là Hold, dừng bàn giao và báo ngay cho người phụ trách chất lượng/quản lý dữ liệu. Tuyệt đối không tự ý dùng công cụ ngoài để làm mờ hay tự xóa ảnh khỏi lô gốc vì sẽ làm thay đổi mã sha256 trong manifest.json, phá vỡ tính toàn vẹn dữ liệu gốc v1 và làm mất lịch sử truy vết nguồn.
