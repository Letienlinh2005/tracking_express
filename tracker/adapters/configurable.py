import json
import os
import re
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tracker.adapters.base import CarrierAdapter, NotConfigured, TrackingError
from tracker.http import TrackingHTTP
from tracker.models import Snapshot, TrackingEvent, parse_time
from tracker.statuses import is_delivered_status


def at_path(value: Any, path: str, default: Any = None) -> Any:
    if not path:
        return value
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def substitute(value: Any, tracking_number: str) -> Any:
    if isinstance(value, dict):
        return {key: substitute(item, tracking_number) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, tracking_number) for item in value]
    if isinstance(value, str):
        value = value.replace("{tracking_number}", tracking_number)

        def env(match: re.Match) -> str:
            result = os.getenv(match[1])
            if not result:
                raise NotConfigured(f"Thiếu biến môi trường {match[1]} cho API của hãng.")
            return result

        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", env, value)
    return value


def load_profiles(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as stream:
        profiles = json.load(stream)
    if not isinstance(profiles, dict):
        raise ValueError("File cấu hình hãng cần là JSON object.")
    return profiles


class ConfigurableJSONAdapter(CarrierAdapter):
    """Complete HTTP/JSON strategy; operator supplies the authorized API contract."""

    def __init__(self, http: TrackingHTTP, profile: dict, tz: ZoneInfo) -> None:
        self.http, self.profile, self.tz = http, profile, tz
        if not isinstance(profile, dict):
            raise ValueError(f"Profile {self.code} cần là object.")
        if profile.get("endpoint"):
            http.validate_url(profile["endpoint"])
        if profile.get("method", "GET").upper() not in {"GET", "POST"}:
            raise ValueError(f"{self.code}: method cần là GET hoặc POST.")

    @property
    def configured(self) -> bool:
        return bool(self.profile.get("endpoint"))

    async def track(self, tracking_number: str) -> Snapshot:
        if not self.configured:
            raise NotConfigured(
                f"{self.name} chưa được cấu hình endpoint trong config/carriers.json."
            )
        profile = self.profile
        payload = await self.http.json(
            profile.get("method", "GET").upper(),
            substitute(profile["endpoint"], tracking_number),
            params=substitute(profile.get("params", {}), tracking_number),
            json=substitute(profile.get("body"), tracking_number),
            headers=substitute(profile.get("headers", {}), tracking_number),
        )
        return self.parse(payload)

    def parse(self, payload: Any) -> Snapshot:
        profile = self.profile
        success = profile.get("success")
        if success and at_path(payload, success["path"]) != success["equals"]:
            raise TrackingError(f"{self.name} báo tra cứu không thành công.")
        records = at_path(payload, profile.get("events_path", "events"))
        if not isinstance(records, list) or not records:
            raise TrackingError(f"{self.name} chưa có sự kiện hoặc mapping JSON chưa đúng.")
        fields = profile.get("fields", {})
        events = []
        sequence = reversed(records) if profile.get("newest_first", False) else records
        for record in sequence:
            if not isinstance(record, dict):
                raise TrackingError(f"{self.name} trả bản ghi không hợp lệ.")

            def field(key: str, record: dict = record) -> Any:
                return at_path(record, fields.get(key, key))

            try:
                timestamp = parse_time(field("time"), self.tz)
            except (TypeError, ValueError, OverflowError):
                raise TrackingError(f"{self.name}: mapping thời gian không hợp lệ.") from None
            detail = str(field("detail") or "").strip()
            if not detail:
                raise TrackingError(f"{self.name}: mapping chi tiết trạng thái không hợp lệ.")
            code = str(field("code") or "")
            # Configure exact terminal codes from your carrier contract, never guess numeric codes.
            delivered_codes = {str(c) for c in profile.get("delivered_codes", [])}
            terminal = code in {str(c) for c in profile.get("terminal_codes", [])} | delivered_codes
            status = str(field("status") or detail)
            events.append(
                TrackingEvent(
                    time=timestamp,
                    code=code,
                    detail=detail,
                    status=status,
                    location=str(field("location") or ""),
                    next_location=str(field("next_location") or ""),
                    terminal=terminal,
                    delivered=terminal
                    and (code in delivered_codes or is_delivered_status(status, detail)),
                )
            )
        return Snapshot(tuple(events))
