# tracking_express

Telegram Express Tracker — bot theo dõi vận đơn tự động và gửi thông báo qua Telegram.

Bot Python theo dõi vận đơn qua Telegram, SQLite và APScheduler. Toàn bộ mã nguồn nằm trong dự án này; không cần dịch vụ database riêng. Python **3.11 trở lên**; khuyến nghị 3.13. Các phụ thuộc chính được khóa phiên bản trong `requirements.txt`, gồm `python-telegram-bot[job-queue] 22.8`, `APScheduler 3.11.3`, `requests 2.34.2`, `curl_cffi 0.17.0`.

SPX đã có adapter gọi API thật. J&T Express Việt Nam có adapter đọc trang tracking chính thức với 4 số cuối điện thoại xác thực theo từng đơn. GHN, GHTK, Viettel Post có class riêng và bộ gọi HTTP/mapping JSON hoàn chỉnh; bạn cần cung cấp endpoint và hợp đồng dữ liệu của API được phép sử dụng. Các profile ví dụ để `endpoint: null` nhằm tránh gọi nhầm endpoint. Bot thông báo rõ khi hãng chưa được cấu hình.

Mã nguồn: [Letienlinh2005/tracking_express](https://github.com/Letienlinh2005/tracking_express). Xem [CHANGELOG.md](CHANGELOG.md) để biết các chức năng của phiên bản hiện tại.

```bash
git clone https://github.com/Letienlinh2005/tracking_express.git
cd tracking_express
```

## Cấu trúc thư mục

```text
Express/
├── .env.example
├── .gitignore
├── .dockerignore
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
├── Dockerfile
├── compose.yaml
├── README.md
├── CHANGELOG.md
├── config/
│   └── carriers.example.json
├── deploy/
│   ├── express-tracker.service
│   ├── register-windows-autostart.ps1
│   └── windows_runner.pyw
├── tracker/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py
│   ├── models.py
│   ├── statuses.py
│   ├── formatting.py
│   ├── database.py
│   ├── http.py
│   ├── service.py
│   ├── bot.py
│   └── adapters/
│       ├── __init__.py
│       ├── base.py
│       ├── registry.py
│       ├── spx.py
│       ├── jnt.py
│       ├── configurable.py
│       ├── ghn.py
│       ├── ghtk.py
│       └── viettelpost.py
└── tests/
    ├── conftest.py
    ├── test_tracking.py
    ├── test_delivery_cleanup.py
    ├── test_jnt.py
    └── test_bot.py
```

Lúc chạy sẽ tạo `data/tracking.sqlite3` và các file SQLite WAL. `.env`, database và `config/carriers.json` chứa cấu hình riêng đã được đưa vào `.gitignore`.

## Lấy Bot Token

1. Mở [@BotFather](https://t.me/BotFather) chính thức trong Telegram.
2. Gửi `/newbot`, chọn tên hiển thị và username kết thúc bằng `bot`.
3. Sao chép token vào `TELEGRAM_BOT_TOKEN` trong `.env`.
4. Mở cuộc trò chuyện riêng với bot vừa tạo và bấm **Start**. Bot chỉ có thể chủ động nhắn cho người dùng đã bắt đầu cuộc trò chuyện với nó.

Token là mật khẩu của bot. Nếu bị lộ, thu hồi bằng BotFather và thay token trong cấu hình. Xem [hướng dẫn chính thức của Telegram](https://core.telegram.org/bots/tutorial#obtain-your-bot-token).

## Chạy nhanh trên Windows

Trong PowerShell, ở thư mục dự án:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item config/carriers.example.json config/carriers.json
notepad .env
.\.venv\Scripts\python.exe -m tracker
```

Nếu máy chỉ có Python 3.11/3.12/3.14, đổi `-3.13` thành phiên bản đó. Việc copy cấu hình chỉ cần làm lần đầu; không ghi đè file bạn đã chỉnh. Có thể bỏ qua `config/carriers.json` nếu chỉ dùng SPX.

## Tự chạy khi đăng nhập Windows

Chạy PowerShell trong thư mục dự án:

```powershell
.\deploy\register-windows-autostart.ps1
```

Script đăng ký task **Express Telegram Tracker** cho tài khoản Windows hiện tại. Bot tự chạy ẩn bằng `pythonw.exe` sau khi đăng nhập khoảng 30 giây và có mạng, không cần mở PowerShell. Task không giới hạn thời gian chạy, không dừng vì chuyển sang pin, bỏ qua lần khởi chạy trùng của chính task và thử khởi động lại sau 1 phút nếu tiến trình lỗi (tối đa 999 lần). Không lưu mật khẩu tài khoản Windows.

Task được tạo ở trạng thái sẵn sàng và sẽ chạy từ lần đăng nhập kế tiếp; script không mở thêm bot khi phiên chạy thủ công còn hoạt động. Sau khi task đã tự chạy, không chạy thêm `python -m tracker` bằng tay với cùng token. Khi tắt máy, ngủ hoặc đăng xuất thì bot ngừng hoạt động; việc chỉ khóa màn hình vẫn giữ bot chạy. Đây là lịch **khi đăng nhập**, không chạy trước màn hình đăng nhập.

Kết nối Telegram có timeout 30 giây và được thử lại khi khởi động. Nếu lỗi mạng vẫn kéo dài, tiến trình chạy ẩn giữ trạng thái sống và thử khởi động lại sau 30 giây, thay vì thoát ngay khi mạng vừa lên chậm. Log `Registered Telegram commands: ... /history ...` và `Application started` xác nhận bản mới đã đăng ký lệnh và bắt đầu xử lý.

Log được lưu ở `data/logs/bot.log`, xoay vòng tối đa 3 file dự phòng, mỗi file khoảng 2 MB; token được che khỏi nội dung log. Xem trạng thái và log:

```powershell
Get-ScheduledTask -TaskName 'Express Telegram Tracker'
Get-ScheduledTaskInfo -TaskName 'Express Telegram Tracker'
Get-Content .\data\logs\bot.log -Tail 30
```

Để tắt tự khởi động:

```powershell
Disable-ScheduledTask -TaskName 'Express Telegram Tracker'
```

Để bật lại:

```powershell
Enable-ScheduledTask -TaskName 'Express Telegram Tracker'
```

Vô hiệu hóa task chỉ ngăn lần chạy tiếp theo. Muốn dừng task đang chạy, dùng `Stop-ScheduledTask -TaskName 'Express Telegram Tracker'`. Nếu di chuyển thư mục dự án, chạy lại script đăng ký ở vị trí mới để cập nhật đường dẫn.

## Chạy nhanh trên Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
cp config/carriers.example.json config/carriers.json
nano .env
.venv/bin/python -m tracker
```

Chỉ cần thay token là dùng được SPX. Dừng chương trình bằng `Ctrl+C`. Không chạy nhiều instance với cùng token/database. Chương trình dùng long polling, không cần tên miền, webhook hay mở cổng vào VPS.

## Các lệnh Telegram

| Lệnh | Hành vi |
|---|---|
| `/start` | Hướng dẫn ngắn và cú pháp |
| `/add SPXVN0123456789 Tai nghe` | Thêm đơn, quét ngay và trả trạng thái mới nhất |
| `/add GY123456 --carrier ghn Quà tặng` | Chọn hãng rõ ràng, có tên gợi nhớ |
| `/add JNTTEST123456 --phone 1234 Áo mới` | Thêm J&T với 4 số cuối điện thoại xác thực |
| `/add 841234567890 --carrier jnt --phone 1234 Áo mới` | Chọn rõ J&T cho mã toàn số |
| `/rename SPXVN0123456789 Tai nghe Sony` | Đổi tên đơn của bạn, giữ nguyên lịch sử và trạng thái |
| `/add 123456789` | Hiện nút chọn hãng nếu không nhận diện được |
| `/list` | Liệt kê đơn còn lưu; đơn giao thành công tự xóa sau khi gửi xong thông báo cuối |
| `/check SPXVN0123456789` | Gọi API ngay; cũng dùng được cho mã chưa thêm |
| `/check 123456789 --carrier vtp` | Tra cứu thủ công với hãng cụ thể |
| `/history SPXVN0123456789` | Xem toàn bộ hành trình từ cũ đến mới, có thời gian và vị trí |
| `/check SPXVN0123456789 --full` | Cách gọi khác để xem toàn bộ hành trình |
| `/del SPXVN0123456789` | Xóa đơn, lịch sử và thông báo chưa gửi của chính bạn |

Bot nhận diện `SPXVN… → SPX`, `GY…/G8… → GHN`, `JNT… → J&T`. Đây là quy tắc gợi ý; dùng `--carrier` để chọn đúng hãng khi tiền tố trùng. Mã toàn số được hỏi bằng inline keyboard hoặc chọn rõ bằng `--carrier jnt`, để tránh gán nhầm các hãng khác. Lựa chọn hết hạn sau 10 phút. Nếu tạo lựa chọn mới hoặc khởi động lại bot, gửi lại lệnh để chọn.

Bot hoạt động trong **chat riêng**, dữ liệu tách theo Telegram chat ID. Mặc định ai nhắn riêng cũng dùng được; để chỉ dùng cá nhân, đặt `ALLOWED_USER_IDS=123456789` hoặc nhiều ID ngăn bằng dấu phẩy. Đây là **Telegram user ID**, không phải số điện thoại hay username. Có thể lấy ID số từ dữ liệu `from.id` của update Telegram bằng Bot API hoặc công cụ Telegram mà bạn tin cậy.

Tên gợi nhớ giới hạn 100 ký tự; mã giới hạn 3–64 ký tự. Thêm trùng mã trong cùng chat sẽ báo đã tồn tại. Dùng `/rename <mã> <tên mới>` để đổi tên mà vẫn giữ nguyên lịch sử, trạng thái, mốc thông báo và lịch quét, kể cả đơn đã hoàn thành. Tên mới có thể gồm nhiều từ, không cần dấu ngoặc, tối đa 100 ký tự. Tên được cập nhật trong `/list`, `/check`, `/history`, các thông báo tự động tiếp theo và tin còn chờ gửi; tin đã gửi trước đó giữ tên cũ. Chỉ chủ đơn trong chat riêng mới đổi được tên. Dùng `/del` rồi `/add` để đổi hãng hoặc theo dõi lại từ đầu. `/check` một mã chưa thêm không tự đăng ký theo dõi.

`/history` và `/check ... --full` gọi API lấy hành trình hiện tại rồi gộp với các mốc đã lưu của chính bạn, loại trùng và hiển thị từ cũ đến mới. Hành trình dài được chia thành nhiều tin đánh số phần, mỗi tin dưới giới hạn Telegram. Có thể xem mã chưa thêm hoặc đơn đã hoàn thành. Lệnh này chỉ đọc dữ liệu, không đổi mốc thông báo và không đăng ký theo dõi. Nếu API lỗi nhưng bot có lịch sử đã lưu, bot hiển thị bản lưu kèm ghi chú; dữ liệu đầy đủ phụ thuộc các mốc API hãng cung cấp và các mốc bot đã giữ lại.

## Luồng xử lý và thông báo

`Telegram handlers → TrackingService → CarrierAdapter → TrackingHTTP → API hãng`

`Snapshot → SQLite transaction (shipments + events + outbox) → Telegram sender`

- `JobQueue` của PTB dùng **APScheduler AsyncIOScheduler** bên trong. Job quét chạy mỗi 60 giây, lần đầu sau khi khởi động khoảng 1 giây. `max_instances=1`, `coalesce=True` ngăn chồng lượt. Job gửi lại outbox chạy mỗi 5 giây. PTB quản lý lifecycle và chờ các job kết thúc khi dừng.
- Worker chỉ chọn đơn `completed=0` và đã hết thời gian backoff. Các chat theo dõi cùng mã/hãng trong một lượt dùng chung một lần gọi API.
- Lần quét thành công đầu tiên lưu toàn bộ lịch sử làm baseline và xếp **một** thông báo mới nhất vào outbox. Nếu lần `/add` đầu gặp lỗi, đơn vẫn được lưu với `initialized=0` và sẽ thử lại; chưa đặt mốc thời gian giả.
- Các lần sau lọc sự kiện có thời gian `>= last_event_time`, đối chiếu fingerprint SHA-256 theo thời gian/mã trạng thái/chi tiết/vị trí để không mất các sự kiện khác nhau cùng giây. Các sự kiện đã biết không được xếp lại. Những bản ghi xuất hiện muộn nhưng có thời gian **cũ hơn mốc đã lưu** chỉ lưu lịch sử, không gửi lại.
- Nhiều sự kiện mới được xếp và gửi từ cũ đến mới. Nếu hai sự kiện trùng thời gian, giữ thứ tự ổn định từ adapter. Một phản hồi cũ không làm giảm mốc lưu.
- Giao dịch SQLite ghi cả lịch sử, trạng thái và outbox một cách nguyên tử. Gửi lỗi sẽ giữ thông báo và thử lại theo backoff, không vượt qua mốc lỗi để gửi mốc sau của cùng đơn. Khởi động lại tiếp tục xử lý outbox chưa gửi, kể cả đơn đã hoàn thành.
- `/check` trả hiện trạng ngay. Với đơn đang lưu, nếu API có các mốc mới, bot cập nhật SQLite rồi gửi các mốc đó qua outbox theo thứ tự; trạng thái mới nhất không bị gửi thêm một lần do chính lệnh đó.
- SPX chỉ đánh dấu kết thúc khi tên/chi tiết sự kiện khớp chính xác các trạng thái hoàn tất đã khai báo trong `spx.py`. `Delivery failed`, `Returning to sender`, yêu cầu hủy và đang hoàn hàng tiếp tục được quét cho đến trạng thái cuối. Với adapter cấu hình, đặt đúng `terminal_codes` theo tài liệu API của hãng.
- Đơn giao thành công tự xóa sau khi toàn bộ thông báo còn chờ đã được Telegram xác nhận nhận thành công. Nếu gửi lỗi/rate-limit, đơn và thông báo vẫn được giữ để thử lại. Đơn hủy hoặc hoàn hàng ngừng quét nhưng vẫn nằm trong `/list` cho đến khi `/del`.
- Tự xóa bao gồm dữ liệu đơn, lịch sử lưu cục bộ, 4 số xác thực và outbox qua foreign-key cascade. Các đơn giao thành công từ phiên bản cũ cũng được dọn sau khi gửi xong thông báo. `/check` và `/history` vẫn gọi API để tra cứu mã đã xóa; với J&T cần cung cấp lại `--phone` hoặc dạng `<mã>-<4 số cuối>`, và không còn bản lưu cục bộ để dùng khi API lỗi.

Mẫu thông báo là plain text, đúng emoji/dòng bạn yêu cầu; nội dung API và tên được đưa về một dòng để tránh phá bố cục. Hai dòng vị trí chỉ xuất hiện khi API trả tên vị trí thật:

```text
🔔 SPX Update: Đơn hàng đã đến kho

📦 SPXVN0123456789 Tai nghe
📌 Status: Đơn hàng đã đến kho BN B Mega SOC
🕒 Date: 19:31:38 25/11/2026
📍 Location: BN B Mega SOC
➡️ Next Location: 29-NBH Hoa Lu Hub
```

Các tên/mã trong ví dụ chỉ minh họa. Thời gian hiển thị mặc định theo `Asia/Ho_Chi_Minh`; database lưu Unix seconds.

**Giới hạn giao nhận thông báo:** outbox tránh gửi lặp trong luồng bình thường và giữ dữ liệu khi gửi lỗi, nhưng Telegram `sendMessage` không cung cấp idempotency key. Nếu máy dừng sau khi Telegram nhận tin nhưng trước khi SQLite đánh dấu đã gửi, hoặc một timeout xảy ra sau khi Telegram đã nhận tin, tin đó có thể được gửi lại. Thiết kế ưu tiên không mất tin, không tuyên bố bảo đảm exactly-once.

## SPX: endpoint và mapping đã triển khai

```http
GET https://spx.vn/shipment/order/open/order/get_order_info?spx_tn=<MA>&language_code=vi
Accept: application/json, text/plain, */*
Referer: https://spx.vn/
```

Adapter kiểm tra `retcode == 0`, đối chiếu `data.order_info.spx_tn` nếu có, rồi đọc `data.sls_tracking_info.records`. Mapping:

| Dữ liệu SPX | Dữ liệu nội bộ |
|---|---|
| `actual_time` | `time` (Unix seconds) |
| `tracking_code` | `code` |
| `description`, fallback `tracking_name` | `detail` |
| `tracking_name`/`description` | kiểm tra kết thúc; rút gọn một số status |
| `current_location.location_name` | `location` |
| `next_location.location_name` | `next_location` |

API trả mới trước; adapter đảo về cũ trước rồi sắp xếp theo thời gian. Giữ các sự kiện nội bộ trả về kể cả `display_flag=0`, phù hợp ảnh có các mốc lên xe/phân loại. Không tự suy đoán tên kho từ câu mô tả, không lấy số điện thoại/tên người nhận từ phản hồi để lưu. Thiếu thời gian/chi tiết ở một bản ghi sẽ báo lỗi cả snapshot để tránh đẩy mốc qua dữ liệu chưa xử lý được.

Đây là endpoint web tracking của [SPX](https://spx.vn/vi), không có cam kết API công khai ổn định. Đã kiểm tra phản hồi trực tiếp khi triển khai ngày 26/11/2026; hãng có thể thay schema hoặc yêu cầu xác minh về sau. Khi đó sửa riêng adapter hoặc `SPX_API_URL`. Không dùng endpoint cũ `/api/v2/fleet_order/tracking/search` vì phép thử trả `data: {}`.

## J&T Express Việt Nam

Adapter gọi đúng form tra cứu của [J&T Việt Nam](https://jtexpress.vn/vi/tracking?type=track):

```http
GET https://jtexpress.vn/vi/tracking?type=track&billcode=<MA>&cellphone=<4_SO_CUOI>
```

Endpoint này trả **HTML**, được phân tích bằng BeautifulSoup; không phải API JSON. Mỗi kiện trong `.result_vandon` được đối chiếu đúng mã, rồi đọc `.result-vandon-item`, thời gian, ngày và chi tiết. Chỉ lấy tên kho/bưu cục từ các mẫu câu đã xác minh, không lấy tên nhân viên/người nhận làm vị trí. Thời gian nguồn là giờ Việt Nam; `TIMEZONE` chỉ thay đổi cách hiển thị. Trạng thái `Đơn hàng đã ký nhận` được đánh dấu giao thành công; đang giao, kiện có vấn đề và đang hoàn hàng tiếp tục theo dõi.

Ví dụ dưới đây dùng mã/số điện thoại minh họa; thay bằng thông tin của bạn:

```text
/add JNTTEST123456 --phone 1234 Áo mới
/add 841234567890 --carrier jnt --phone 1234 Áo mới
/check JNTTEST123456 --phone 1234
/history JNTTEST123456 --phone 1234
```

Với mã có tiền tố `JNT`, có thể nhập gộp `<mã>-<4 số cuối>`:

```text
/add JNTTEST123456-1234 Áo mới
/history JNTTEST123456-1234
```

Bot lưu mã chuẩn không kèm hậu tố xác thực. Sau `/add` thành công, `/check <mã>` và `/history <mã>` tự dùng 4 số đã lưu của chính bạn, không cần nhập lại. `/rename` và `/del` dùng mã chuẩn. Tra cứu mã chưa thêm với `--phone` chỉ kiểm tra thủ công, không đăng ký theo dõi. Đơn thiếu 4 số xác thực sẽ được hướng dẫn bổ sung trước khi lưu.

SQLite tự thêm cột `phone_last4` khi nâng cấp, giữ nguyên toàn bộ đơn và lịch sử cũ. Bốn số này được giữ riêng theo chủ đơn, không đưa vào tin nhắn, callback data hay log. Worker chỉ dùng chung kết quả khi cùng mã, hãng và cùng 4 số xác thực. Không dùng dữ liệu xác thực của người này để quét đơn cho người khác.

Trên Windows, `curl_cffi` dùng thêm kho CA tin cậy của Windows để kiểm tra chuỗi chứng chỉ của J&T; việc xác minh TLS vẫn bật. `JNT_TRACKING_URL` có thể thay endpoint nếu website thay đổi. Nếu HTML thay cấu trúc hoặc xác thực thất bại, adapter báo lỗi và giữ nguyên mốc sự kiện đang lưu.

## Thêm endpoint GHN/GHTK/Viettel Post

Copy `config/carriers.example.json` thành `config/carriers.json`. Mỗi profile có đầy đủ cấu hình endpoint, method, query/body/headers, đường dẫn JSON sự kiện, mapping trường và trạng thái kết thúc.

Ví dụ dưới đây là **hợp đồng JSON minh họa**, không phải URL hay mã trạng thái thực của GHN. Thay các giá trị theo endpoint bạn có quyền dùng:

```json
{
  "ghn": {
    "endpoint": "https://your-authorized-api.example/tracking",
    "method": "POST",
    "params": {},
    "body": {"order_code": "{tracking_number}"},
    "headers": {"Token": "${GHN_TOKEN}"},
    "success": {"path": "code", "equals": 200},
    "events_path": "data.logs",
    "newest_first": true,
    "fields": {
      "time": "created_at",
      "code": "status_code",
      "status": "status_name",
      "detail": "description",
      "location": "warehouse.name",
      "next_location": "next_warehouse.name"
    },
    "terminal_codes": ["delivered", "cancelled", "returned"],
    "delivered_codes": ["delivered"]
  }
}
```

Với ví dụ đó, API cần trả dạng:

```json
{
  "code": 200,
  "data": {
    "logs": [
      {
        "created_at": "2026-11-26T08:41:08+07:00",
        "status_code": "delivered",
        "status_name": "Đã giao thành công",
        "description": "Bưu kiện đã được giao thành công",
        "warehouse": {"name": "Kho A"},
        "next_warehouse": null
      }
    ]
  }
}
```

- `events_path` và các field hỗ trợ key lồng nhau dạng `a.b.c`; chuỗi rỗng chỉ toàn bộ object. Không hỗ trợ JSONPath/index mảng. Thời gian nhận Unix seconds/milliseconds hoặc ISO-8601. ISO thiếu offset được hiểu theo `TIMEZONE`.
- `success` là tùy chọn; dùng để kiểm tra mã thành công của API. `equals` so sánh đúng kiểu JSON, ví dụ số `200` khác chuỗi `"200"`.
- `delivered_codes` chứa đúng mã giao thành công theo hợp đồng API. Các mã này đồng thời là terminal và sẽ tự xóa đơn sau thông báo cuối. Mã hủy/hoàn hàng chỉ nằm trong `terminal_codes`; không đưa chúng vào `delivered_codes`. Với trạng thái tiếng Việt/Anh phổ biến, bot cũng nhận diện chính xác tên giao thành công và hỗ trợ dữ liệu đã lưu từ bản cũ.
- `${GHN_TOKEN}` lấy từ môi trường hoặc `.env`, không ghi token vào JSON được commit. Thiếu biến sẽ báo rõ tên biến, không in token.
- Có thể đặt `{tracking_number}` trong endpoint/body/query/header. Mã đã được giới hạn ký tự nên không chứa ký tự phân tách URL.
- Đường dẫn `events_path` phải trả về **toàn bộ các mốc cần theo dõi**. Nếu API cần phân trang, chữ ký, login hoặc đổi token nhiều bước, hãy mở rộng `track()` trong class hãng đó để gom trang/xác thực trước khi gọi `parse()`.
- Profile phải trỏ tới HTTPS. Redirect HTTP bị từ chối để tránh gửi header token sang host khác.
- Sau khi chỉnh profile hoặc `.env`, khởi động lại bot.

Thêm hãng mới bằng cách tạo class kế thừa `CarrierAdapter` (hoặc `ConfigurableJSONAdapter`), trả `Snapshot` gồm `TrackingEvent`, rồi đăng ký trong `build_registry()`; thêm key vào kiểm tra `--carrier` và quy tắc `detect_carrier()` nếu có tiền tố chắc chắn.

## Rate-limit, độ trễ và cấu hình

Mọi request tracking, kể cả `/add` và `/check`, đi qua cùng một khóa và HTTP session. Mặc định `HTTP_BACKEND=curl_cffi` dùng profile Chrome và các header của trình duyệt; có thể đặt `HTTP_BACKEND=requests` để dùng requests với User-Agent phổ thông. Cả hai backend giữ nguyên delay, timeout và cooldown; không tự đổi backend khi bị chặn. Request blocking chạy bằng `asyncio.to_thread`; Telegram vẫn có thể xử lý job khác. Sau mỗi request, chờ ngẫu nhiên mặc định 1–3 giây trước request kế tiếp. Không xoay IP, không vượt CAPTCHA. Xem [cách cấu hình profile Chrome của curl_cffi](https://curl-cffi.readthedocs.io/en/latest/quick_start.html).

HTTP 403/429 kích hoạt cooldown toàn bộ client theo `Retry-After` (nhận cả số giây và HTTP-date), mặc định 60 giây. Các lỗi mạng, 5xx, JSON sai và dữ liệu rỗng được backoff theo đơn từ 60 giây, tăng dần tối đa 1 giờ; giá trị `Retry-After` dài hơn vẫn được tôn trọng. Telegram `RetryAfter` có cooldown riêng.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | bắt buộc | Token từ BotFather |
| `ALLOWED_USER_IDS` | trống | Danh sách user được dùng chat riêng |
| `DATABASE_PATH` | `data/tracking.sqlite3` | Đường dẫn SQLite |
| `TIMEZONE` | `Asia/Ho_Chi_Minh` | Múi giờ hiển thị và đọc ISO không offset |
| `POLL_INTERVAL_SECONDS` | `60` | Chu kỳ worker, tối thiểu 60 |
| `REQUEST_DELAY_MIN/MAX` | `1` / `3` | Delay giữa request |
| `HTTP_TIMEOUT_SECONDS` | `20` | Read timeout; connect timeout tối đa 10 giây |
| `HTTP_BACKEND` | `curl_cffi` | Client HTTP; có thể đổi thành `requests` |
| `MAX_ORDERS_PER_CHAT` | `50` | Tổng số đơn mỗi chat, tính cả đơn hoàn thành |
| `SPX_API_URL` | endpoint ở trên | Endpoint SPX |
| `JNT_TRACKING_URL` | `https://jtexpress.vn/vi/tracking` | Trang tra cứu J&T Việt Nam |
| `CARRIER_CONFIG_PATH` | `config/carriers.json` | File cấu hình hãng |
| `LOG_LEVEL` | `INFO` | Mức log ứng dụng |

Chu kỳ 60 giây là lịch khởi chạy, không phải bảo đảm mỗi đơn luôn được kiểm tra trong đúng 60 giây. Với request tuần tự và delay trung bình 2 giây, 30 mã đã cần khoảng 60 giây cộng thời gian mạng. Nếu một lượt quá dài, APScheduler bỏ lượt chồng và log cảnh báo; giảm số đơn hoặc tăng chu kỳ để phù hợp giới hạn hãng. Không có header hay delay nào bảo đảm website không chặn IP.

## Chạy 24/7 bằng systemd trên VPS

Ví dụ Ubuntu 24.04 với Python 3.12. Upload toàn bộ dự án vào `/opt/express-tracker`, sau đó:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv
sudo useradd --system --user-group --home-dir /opt/express-tracker --shell /usr/sbin/nologin express-bot
cd /opt/express-tracker
sudo python3 -m venv .venv
sudo .venv/bin/python -m pip install -r requirements.txt
sudo cp .env.example .env
sudo cp config/carriers.example.json config/carriers.json
sudo nano .env
sudo mkdir -p data
sudo chown -R express-bot:express-bot /opt/express-tracker
sudo chmod 600 .env
sudo cp deploy/express-tracker.service /etc/systemd/system/express-tracker.service
sudo systemctl daemon-reload
sudo systemctl enable --now express-tracker
sudo systemctl status express-tracker
sudo journalctl -u express-tracker -f
```

Chỉ chạy lệnh tạo user/copy file cấu hình ở lần cài đầu. Nếu bạn dùng thư mục khác, sửa đồng bộ `WorkingDirectory`, `EnvironmentFile`, `ExecStart`, `ReadWritePaths` trong service. Database phải nằm trong thư mục `data` được cấp quyền ghi hoặc bổ sung đường dẫn ghi tương ứng. Các đường dẫn cấu hình tương đối tính từ thư mục dự án.

Sau khi cập nhật code/config: `sudo systemctl restart express-tracker`. Dừng: `sudo systemctl stop express-tracker`. Service tự khởi động lại khi tiến trình lỗi và bật cùng VPS.

## Hoặc chạy Docker Compose

Cần Docker Engine và Compose plugin trên VPS. Ở thư mục dự án, sau khi điền `.env` và copy profile:

```bash
docker compose up -d --build
docker compose logs -f --tail=100 bot
docker compose ps
```

Container chạy bằng user không phải root; database nằm trong named volume `tracking-data`, cấu hình hãng mount read-only. Không cần publish port. Dừng bằng `docker compose down`; dữ liệu vẫn tồn tại. **Không dùng `down -v` nếu muốn giữ database.** Sau khi đổi `.env`, dùng `docker compose up -d --force-recreate`; sau khi đổi code, dùng `docker compose up -d --build`.

Chọn systemd **hoặc** Docker cho cùng token; hai tiến trình long polling cùng token sẽ xung đột. Đây là bộ file triển khai sẵn; container/service chưa được bật tự động trên máy của bạn.

## Kiểm thử và vận hành

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check tracker tests
python -m ruff format --check tracker tests
```

Tests không cần bot token thật và không gửi Telegram. Có test đi qua dispatcher thật của PTB với transport Telegram giả lập, bao gồm các lệnh, chọn hãng, phân quyền và lifecycle JobQueue; có test adapter SPX, JSON mapping, baseline, nhiều sự kiện cùng giây, thứ tự gửi, khởi động lại, backoff và xóa dữ liệu theo chủ sở hữu.

Log tránh in token, body phản hồi và thông tin người nhận. Kiểm tra `/list` và log khi đơn không cập nhật. Nếu HTTP 403/429 kéo dài, kiểm tra trên website hãng và dùng kênh API được cấp phép; bot sẽ giữ mốc cũ và backoff. Nếu thấy `Conflict` từ Telegram, dừng instance trùng hoặc webhook cũ của cùng token.

Sao lưu SQLite bằng SQLite backup API khi đang chạy, hoặc dừng dịch vụ trước khi copy cả database. Không copy riêng file `.sqlite3` khi WAL đang hoạt động. Lịch sử được giữ đến khi `/del`; có thể xây chính sách lưu trữ thêm nếu chạy bot công cộng lâu dài.

Tài liệu đối chiếu: [python-telegram-bot 22.8](https://docs.python-telegram-bot.org/en/v22.8/), [JobQueue và APScheduler](https://docs.python-telegram-bot.org/en/stable/telegram.ext.jobqueue.html), [APScheduler 3.x](https://apscheduler.readthedocs.io/en/3.x/).
