from zoneinfo import ZoneInfo

from tracker.models import Snapshot, TrackingEvent, display_time


def clean(value: str, limit: int) -> str:
    # One API field must not inject a second line into the fixed Telegram template.
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_update(
    carrier: str, tracking_number: str, nickname: str, event: TrackingEvent, tz: ZoneInfo
) -> str:
    parcel = clean(tracking_number, 64)
    if nickname:
        parcel += " " + clean(nickname, 100)
    lines = [
        f"🔔 {clean(carrier, 40)} Update: {clean(event.status, 180)}",
        "",
        f"📦 {parcel}",
        f"📌 Status: {clean(event.detail, 1200)}",
        f"🕒 Date: {display_time(event.time, tz)}",
    ]
    if event.location.strip():
        lines.append(f"📍 Location: {clean(event.location, 250)}")
    if event.next_location.strip():
        lines.append(f"➡️ Next Location: {clean(event.next_location, 250)}")
    # Plain text, no parse_mode: names containing <>&_* are safe without escaping.
    return "\n".join(lines)


def format_history(
    carrier: str,
    tracking_number: str,
    nickname: str,
    snapshot: Snapshot,
    tz: ZoneInfo,
    warning: str | None = None,
) -> list[str]:
    """Build chronological pages under Telegram's UTF-16 message size limit."""
    parcel = clean(tracking_number, 64)
    if nickname:
        parcel += " " + clean(nickname, 100)
    header = (
        f"📜 {clean(carrier, 40)} — Toàn bộ hành trình\n"
        f"📦 {parcel}\n🔢 {len(snapshot.events)} mốc · Cũ → mới"
    )
    if warning:
        header += "\n⚠️ " + clean(warning, 220)

    def units(text: str) -> int:
        return len(text.encode("utf-16-le")) // 2

    # Reserve room for repeated headers and page numbers, even with supplementary emoji.
    budget = 3900 - units(header) - 64
    pages = []
    current = ""
    for index, event in enumerate(snapshot.events, start=1):
        lines = [
            f"{index}. 🕒 {display_time(event.time, tz)}",
            f"📌 Status: {clean(event.detail, 1000)}",
        ]
        if event.location.strip():
            lines.append(f"📍 Location: {clean(event.location, 200)}")
        if event.next_location.strip():
            lines.append(f"➡️ Next Location: {clean(event.next_location, 200)}")
        block = "\n".join(lines)
        candidate = current + "\n\n" + block if current else block
        if current and units(candidate) > budget:
            pages.append(current)
            current = block
        else:
            current = candidate
    if current:
        pages.append(current)
    return [
        f"{header}\n📄 Phần {index}/{len(pages)}\n\n{page}"
        for index, page in enumerate(pages, start=1)
    ]
