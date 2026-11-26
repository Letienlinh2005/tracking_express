import re
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from tracker.adapters.base import CarrierAdapter, TrackingError
from tracker.adapters.spx import is_terminal, normalized
from tracker.http import TrackingHTTP
from tracker.models import Snapshot, TrackingEvent, parse_time


class JNTAdapter(CarrierAdapter):
    """J&T Vietnam's verified public tracking form (HTML, not an invented JSON API)."""

    code, name = "jnt", "J&T Express"

    def __init__(self, http: TrackingHTTP, endpoint: str, tz: ZoneInfo) -> None:
        http.validate_url(endpoint)
        self.http, self.endpoint, self.tz = http, endpoint, tz

    @staticmethod
    def validate_phone(phone_last4: str) -> None:
        if not re.fullmatch(r"[0-9]{4}", phone_last4):
            raise TrackingError(
                "J&T yêu cầu 4 số cuối điện thoại người gửi hoặc người nhận. "
                "Thêm --phone 1234 vào lệnh (thay 1234 bằng 4 số của bạn)."
            )

    async def track(self, tracking_number: str, *, phone_last4: str = "") -> Snapshot:
        self.validate_phone(phone_last4)
        html = await self.http.text(
            "GET",
            self.endpoint,
            params={"type": "track", "billcode": tracking_number, "cellphone": phone_last4},
            headers={"Referer": "https://jtexpress.vn/vi/tracking?type=track"},
        )
        return self.parse(html, tracking_number)

    def parse(self, html: str, tracking_number: str) -> Snapshot:
        soup = BeautifulSoup(html, "html.parser")
        error = soup.find("input", id="error")
        if error and str(error.get("value") or "").strip():
            raise TrackingError("J&T không xác thực được 4 số cuối điện thoại cho mã này.")
        checkbox = soup.find("input", id="chck-" + tracking_number)
        container = checkbox.find_parent(class_="result_vandon") if checkbox else None
        if container is None:
            if soup.select_one(".result-vandon-item"):
                raise TrackingError("J&T trả hành trình của mã khác; chưa cập nhật mốc lưu.")
            raise TrackingError("J&T chưa trả hành trình. Kiểm tra mã và 4 số cuối điện thoại.")
        rows = container.select(".result-vandon-item")
        if not rows:
            raise TrackingError("J&T chưa có bản ghi hành trình cho mã này.")
        events = []
        for row in reversed(rows):  # Official page is newest first.
            time_icon = row.find("ion-icon", attrs={"name": "time-outline"})
            date_icon = row.find("ion-icon", attrs={"name": "calendar-clear-outline"})
            time_span = time_icon.find_next_sibling("span") if time_icon else None
            date_span = date_icon.find_next_sibling("span") if date_icon else None
            fields = row.find_all("div", recursive=False)
            if time_span is None or date_span is None or len(fields) != 2:
                raise TrackingError("Cấu trúc hành trình J&T đã thay đổi; chưa cập nhật mốc lưu.")
            detail = " ".join(fields[1].get_text(" ", strip=True).split())
            if not detail:
                raise TrackingError("J&T trả sự kiện thiếu trạng thái.")
            try:
                timestamp = parse_time(
                    date_span.get_text(strip=True) + "T" + time_span.get_text(strip=True),
                    ZoneInfo("Asia/Ho_Chi_Minh"),
                )
            except (ValueError, TypeError, OverflowError):
                raise TrackingError(
                    "J&T trả thời gian không hợp lệ; chưa cập nhật mốc lưu."
                ) from None
            status, code, terminal = classify_status(detail)
            location, next_location = locations(detail)
            events.append(
                TrackingEvent(
                    time=timestamp,
                    status=status,
                    detail=detail,
                    code=code,
                    location=location,
                    next_location=next_location,
                    terminal=terminal,
                    delivered=code == "delivered",
                )
            )
        return Snapshot(tuple(events))


def classify_status(detail: str) -> tuple[str, str, bool]:
    text = normalized(detail)
    if text.startswith(("don hang da ky nhan", "da ky nhan")):
        return "Đã giao thành công", "delivered", True
    if text.startswith(("don hang da huy", "van don da huy", "da huy don hang")):
        return "Đã hủy", "cancelled", True
    if text.startswith(("hoan hang thanh cong", "tra hang thanh cong", "don hang da hoan tra")):
        return "Đã hoàn hàng", "returned", True
    if is_terminal(detail):
        return detail, "completed", True
    if "dang giao hang" in text:
        return "Đang giao hàng", "out_for_delivery", False
    if "dang chuyen hang den" in text:
        return "Đang trung chuyển", "in_transit", False
    if "hang da duoc chuyen den" in text:
        return "Đã đến bưu cục", "arrived", False
    if "da dang ky kien van de" in text:
        return "Kiện có vấn đề", "exception", False
    if "da nhan hang" in text:
        return "Đã nhận hàng", "picked_up", False
    return detail, "", False


def locations(detail: str) -> tuple[str, str]:
    def match(pattern: str) -> str:
        result = re.search(pattern + r"\s*【\s*([^】]+?)\s*】", detail, re.IGNORECASE)
        return result[1].strip() if result else ""

    current = match(r"bưu cục") or match(r"hàng đã được chuyển đến") or match(r"^")
    following = match(r"đang chuyển hàng đến")
    return current, following
