import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from tracker.statuses import is_delivered_status


def parse_time(value: Any, local_timezone: ZoneInfo) -> int:
    """Accept Unix seconds/milliseconds or ISO-8601; never invent an event timestamp."""
    if isinstance(value, bool) or value is None:
        raise ValueError("Thiếu thời gian sự kiện.")
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and value.replace(".", "", 1).isdigit()
    ):
        number = float(value)
        if number > 100_000_000_000:
            number /= 1000
        # Bound to a representable, credible parcel date (2000–2100).
        if not 946684800 <= number <= 4102444800:
            raise ValueError("Thời gian sự kiện nằm ngoài khoảng hỗ trợ.")
        return int(number)
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_timezone)
    return parse_time(parsed.timestamp(), local_timezone)


@dataclass(frozen=True)
class TrackingEvent:
    time: int
    status: str
    detail: str
    code: str = ""
    location: str = ""
    next_location: str = ""
    terminal: bool = False
    delivered: bool = False

    @property
    def delivered_successfully(self) -> bool:
        # Exact text/code fallback keeps events saved by older versions compatible.
        return self.terminal and (
            self.delivered
            or self.code.casefold() == "delivered"
            or is_delivered_status(self.status, self.detail)
        )

    @property
    def key(self) -> str:
        # Use content as well as time: two distinct scans can share a second.
        identity = [self.time, self.code, self.detail, self.location, self.next_location]
        return hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, value: str) -> "TrackingEvent":
        return cls(**json.loads(value))


@dataclass(frozen=True)
class Snapshot:
    events: tuple[TrackingEvent, ...]

    def __post_init__(self) -> None:
        if not self.events:
            raise ValueError("Snapshot cần có ít nhất một sự kiện.")
        unique = {event.key: event for event in self.events}
        object.__setattr__(self, "events", tuple(sorted(unique.values(), key=lambda e: e.time)))

    @property
    def latest(self) -> TrackingEvent:
        return self.events[-1]


def display_time(timestamp: int, local_timezone: ZoneInfo) -> str:
    return (
        datetime.fromtimestamp(timestamp, tz=UTC)
        .astimezone(local_timezone)
        .strftime("%H:%M:%S %d/%m/%Y")
    )
