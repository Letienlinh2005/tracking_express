# Changelog

## 0.3.0 — 26/11/2026

- Tự xóa đơn giao thành công sau khi Telegram nhận đủ các thông báo đang chờ, bao gồm thông báo cuối.
- Giữ đơn và outbox khi Telegram lỗi/rate-limit; tiếp tục gửi lại sau khi khởi động lại.
- Dọn cả đơn giao thành công đã lưu từ phiên bản cũ; giữ nguyên các đơn đang giao, hủy và hoàn hàng.
- Phân biệt kết quả giao thành công với trạng thái terminal; thêm `delivered_codes` cho adapter JSON cấu hình được.
- Dữ liệu đơn, lịch sử cục bộ và thông tin xác thực được xóa theo đơn. Có thể tra cứu lại trực tiếp từ API bằng `/check` hoặc `/history`.
- 94 bài kiểm thử tự động đã đạt, bao gồm thứ tự gửi trước khi xóa, retry/rate-limit, dữ liệu bản cũ và tách đơn theo chủ sở hữu.

## 0.2.0 — 26/11/2026

- Thêm adapter J&T Express Việt Nam dùng endpoint tracking HTML chính thức và BeautifulSoup.
- Hỗ trợ `--carrier jnt`, `--phone <4 số cuối>` và dạng rút gọn `JNT...-1234` cho `/add`, `/check`, `/history`.
- Lưu xác thực theo từng đơn/chủ đơn, tự dùng lại khi quét nền; tách nhóm request theo thông tin xác thực.
- Tự nâng cấp SQLite để thêm `phone_last4`, giữ nguyên lịch sử và các mốc thông báo.
- Đọc vị trí bưu cục/điểm đến tiếp theo và nhận diện đã ký nhận để dừng quét.
- Hỗ trợ phản hồi HTML qua HTTP client và kho CA Windows, tiếp tục xác minh TLS.
- 78 bài kiểm thử tự động đã đạt; kiểm chứng trực tiếp một vận đơn J&T do người dùng cung cấp và kiểm tra lại SPX.

## 0.1.0 — 26/11/2026

### Chức năng

- Theo dõi vận đơn qua Telegram với `/start`, `/add`, `/list`, `/check`, `/del`.
- Đặt tên khi thêm đơn và đổi tên bằng `/rename <mã vận đơn> <tên mới>`, giữ nguyên lịch sử và trạng thái.
- Xem toàn bộ hành trình bằng `/history <mã>` hoặc `/check <mã> --full`, chia tin dài và sắp xếp từ cũ đến mới.
- Adapter SPX dùng API web tracking; có bộ HTTP và mapping JSON cấu hình được cho GHN, GHTK, Viettel Post.
- Worker APScheduler quét mỗi 60 giây, delay ngẫu nhiên và backoff khi API lỗi hoặc giới hạn truy cập.
- SQLite lưu lịch sử, chống gửi lặp và giữ hàng đợi thông báo qua các lần khởi động lại.
- Ngừng quét đơn đã giao thành công, đã hủy hoặc hoàn tất hoàn hàng.
- Tách dữ liệu theo chủ đơn trong chat riêng và hỗ trợ danh sách user được cấp quyền.

### Triển khai

- Windows Task Scheduler khởi chạy bot ẩn sau khi đăng nhập, có log xoay vòng và thử lại khi mạng Telegram lỗi lúc khởi động.
- Có Dockerfile, Docker Compose và systemd service để chạy trên VPS Linux.
- Có `.env.example`, cấu hình hãng mẫu và hướng dẫn cài đặt trong [README.md](README.md).

### Kiểm chứng

- 49 bài kiểm thử tự động đã đạt, gồm dispatcher Telegram, adapter, phân quyền, lịch sử, đổi tên, SQLite và gửi lại thông báo.
- Đã kiểm tra trực tiếp adapter SPX và đăng ký các lệnh của bot trên Telegram khi triển khai.

### Phạm vi hỗ trợ

- GHN, GHTK và Viettel Post cần endpoint, thông tin xác thực và mapping theo API được phép sử dụng.
- API web SPX có thể thay đổi; chu kỳ quét thực tế còn phụ thuộc thời gian phản hồi và giới hạn truy cập của hãng.
