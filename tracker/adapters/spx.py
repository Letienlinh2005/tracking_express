import unicodedata
from typing import Any
from zoneinfo import ZoneInfo

from tracker.adapters.base import CarrierAdapter, TrackingError
from tracker.http import TrackingHTTP
from tracker.models import Snapshot, TrackingEvent, parse_time


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold().replace("đ", "d"))
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).split()).strip(". !")


TERMINAL_NAMES = {
    "delivered",
    "delivered successfully",
    "delivery successful",
    "order delivered",
    "cancelled",
    "canceled",
    "order cancelled",
    "order canceled",
    "returned",
    "returned to sender",
    "return completed",
    "return delivered",
    "da giao hang",
    "giao hang thanh cong",
    "da giao hang thanh cong",
    "da huy",
    "don hang da huy",
    "don hang da bi huy",
    "da hoan hang",
    "hoan hang thanh cong",
    "tra hang thanh cong",
    "da tra hang cho nguoi gui",
}

SHORT_STATUSES = {
    "manifested": "Người bán đang chuẩn bị hàng",
    "enter domestic first mile hub": "Đã đến bưu cục lấy hàng",
    "loaded to truck in first mile hub": "Đã lên xe trung chuyển",
    "left domestic first mile hub": "Đã rời bưu cục lấy hàng",
    "enter domestic sorting center": "Đã đến kho phân loại",
    "packed in first mile hub": "Đã phân loại hàng",
    "packed in domestic sorting centre": "Đã phân loại hàng",
    "loaded to truck in sorting centre": "Đã lên xe trung chuyển",
    "left domestic sorting center": "Đang trung chuyển",
    "domestic line haul end": "Đang đến trạm giao hàng",
    "enter last mile hub": "Đã đến trạm giao hàng",
    "out for delivery": "Đang giao hàng",
    "delivered": "Đã giao thành công",
    "cancelled": "Đã hủy",
    "canceled": "Đã hủy",
    "returned to sender": "Đã hoàn hàng",
    "return completed": "Đã hoàn hàng",
}


def is_terminal(*values: str) -> bool:
    # Exact matches avoid stopping on "delivery failed", "returning" or "cancel requested".
    return any(normalized(value) in TERMINAL_NAMES for value in values)


def location_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("location_name") or "").strip()
    return str(value or "").strip() if isinstance(value, str) else ""


class SPXAdapter(CarrierAdapter):
    code, name = "spx", "SPX"

    def __init__(self, http: TrackingHTTP, endpoint: str, tz: ZoneInfo) -> None:
        http.validate_url(endpoint)
        self.http, self.endpoint, self.tz = http, endpoint, tz

    async def track(self, tracking_number: str) -> Snapshot:
        payload = await self.http.json(
            "GET",
            self.endpoint,
            params={"spx_tn": tracking_number, "language_code": "vi"},
            headers={"Referer": "https://spx.vn/"},
        )
        return self.parse(payload, tracking_number)

    def parse(self, payload: Any, tracking_number: str = "") -> Snapshot:
        if not isinstance(payload, dict) or str(payload.get("retcode")) != "0":
            raise TrackingError("SPX chưa trả hành trình hợp lệ cho mã này.")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise TrackingError("Cấu trúc dữ liệu SPX đã thay đổi.")
        info = data.get("order_info") or {}
        if not isinstance(info, dict):
            raise TrackingError("Cấu trúc thông tin đơn SPX không hợp lệ.")
        actual_number = str(info.get("spx_tn") or "")
        if tracking_number and actual_number and actual_number.upper() != tracking_number.upper():
            raise TrackingError("SPX trả mã vận đơn khác mã yêu cầu.")
        tracking = data.get("sls_tracking_info") or {}
        if not isinstance(tracking, dict):
            raise TrackingError("Cấu trúc hành trình SPX không hợp lệ.")
        records = tracking.get("records")
        if not isinstance(records, list) or not records:
            raise TrackingError("SPX chưa có lịch sử cho mã này; bot sẽ thử lại.")
        events = []
        # The endpoint returns newest first. Reverse before stable sorting for equal times.
        for record in reversed(records):
            if not isinstance(record, dict):
                raise TrackingError("SPX trả bản ghi hành trình không hợp lệ.")
            detail = str(record.get("description") or record.get("tracking_name") or "").strip()
            if not detail:
                raise TrackingError("SPX trả sự kiện thiếu trạng thái; chưa cập nhật mốc lưu.")
            try:
                timestamp = parse_time(record.get("actual_time"), self.tz)
            except (ValueError, TypeError, OverflowError):
                raise TrackingError(
                    "SPX trả sự kiện thiếu thời gian hợp lệ; chưa cập nhật mốc lưu."
                ) from None
            name = str(record.get("tracking_name") or "")
            terminal = is_terminal(name, detail)
            status = SHORT_STATUSES.get(normalized(name), detail)
            events.append(
                TrackingEvent(
                    time=timestamp,
                    status=status,
                    detail=detail,
                    code=str(record.get("tracking_code") or ""),
                    location=location_name(record.get("current_location")),
                    next_location=location_name(record.get("next_location")),
                    terminal=terminal,
                )
            )
        return Snapshot(tuple(events))
