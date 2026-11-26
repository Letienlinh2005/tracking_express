import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from tracker.adapters.base import TrackingError
from tracker.adapters.jnt import JNTAdapter, classify_status, locations
from tracker.adapters.registry import build_registry, detect_carrier
from tracker.bot import BotHandlers
from tracker.database import Database
from tracker.models import Snapshot, TrackingEvent
from tracker.service import TrackingService

NUMBER = "JNTTEST987654"


def record(time="08:00:00", date="2026-09-25", detail="Hàng đã được chuyển đến【Kho A】"):
    return f"""<div class="result-vandon-item flex flex-col">
      <div><div><ion-icon name="time-outline"></ion-icon><span>{time}</span></div>
      <div><ion-icon name="calendar-clear-outline"></ion-icon><span>{date}</span></div></div>
      <div>{detail}</div></div>"""


def page(*records, number=NUMBER):
    return (
        f'<div class="result_vandon"><input type="checkbox" id="chck-{number}"><div class="tab-content">'
        + "".join(records)
        + "</div></div>"
    )


def adapter(tz, html=""):
    http = Mock(text=AsyncMock(return_value=html))
    return JNTAdapter(http, "https://jtexpress.vn/vi/tracking", tz)


def test_jnt_verified_form_request_and_chronological_parsing(tz):
    html = page(
        record(
            "12:00:00", detail="Đơn hàng đã ký nhận. Người ký nhận là:【<font>Người thử</font>】"
        ),
        record(
            "09:00:00",
            detail="Nhân viên【<font>Nhân viên A</font>】của bưu cục 【<font>Kho A</font>】đang giao hàng.【+84000000000】",
        ),
        record(
            "08:00:00",
            detail="Bưu cục【<font>Kho B</font>】đang chuyển hàng đến【<font>Kho A</font>】",
        ),
    )
    carrier = adapter(tz, html)
    snapshot = asyncio.run(carrier.track(NUMBER, phone_last4="0123"))
    assert len(snapshot.events) == 3
    assert snapshot.events[0].location == "Kho B"
    assert snapshot.events[0].next_location == "Kho A"
    assert snapshot.events[1].location == "Kho A"
    assert snapshot.events[1].status == "Đang giao hàng"
    assert snapshot.latest.terminal
    assert snapshot.latest.status == "Đã giao thành công"
    assert snapshot.latest.location == ""
    assert "<font" not in snapshot.latest.detail
    assert [event.time for event in snapshot.events] == sorted(
        event.time for event in snapshot.events
    )
    call = carrier.http.text.call_args
    assert call.args == ("GET", "https://jtexpress.vn/vi/tracking")
    assert call.kwargs["params"] == {"type": "track", "billcode": NUMBER, "cellphone": "0123"}


@pytest.mark.parametrize("phone", ["", "123", "12345", "+123", "abcd", "１２３４"])
def test_jnt_requires_exact_four_ascii_digits_before_request(tz, phone):
    carrier = adapter(tz)
    with pytest.raises(TrackingError):
        asyncio.run(carrier.track(NUMBER, phone_last4=phone))
    carrier.http.text.assert_not_awaited()


@pytest.mark.parametrize(
    "html",
    [
        '<input id="error" value="verification_failed">',
        '<div class="result-tracking"></div>',
        page(record(), number="OTHER123"),
        page(),
        page(record(date="invalid-date")),
        page(record(detail="")),
        page('<div class="result-vandon-item"><div>No timestamp</div></div>'),
    ],
)
def test_jnt_bad_or_unverified_page_does_not_create_snapshot(tz, html):
    with pytest.raises(TrackingError):
        adapter(tz).parse(html, NUMBER)


@pytest.mark.parametrize(
    "detail,terminal",
    [
        ("Đơn hàng đã ký nhận. Người ký nhận là:【Người thử】", True),
        ("Đơn hàng đã hủy", True),
        ("Hoàn hàng thành công", True),
        ("Đang hoàn hàng", False),
        ("Chưa ký nhận", False),
        ("Giao hàng không thành công", False),
        ("Đã đăng ký Kiện Vấn Đề【Kiện khó】", False),
    ],
)
def test_jnt_terminal_statuses_are_conservative(detail, terminal):
    assert classify_status(detail)[2] is terminal


def test_jnt_locations_never_use_courier_or_receiver_name():
    assert locations("Đơn hàng đã ký nhận. Người ký nhận là:【Người nhận】") == ("", "")
    assert locations(
        "Nhân viên【Nhân viên A】của bưu cục【Kho A】đang giao hàng.【+84000000000】"
    ) == ("Kho A", "")
    assert locations("【Kho C】Đã đăng ký Kiện Vấn Đề【Kiện khó】") == ("Kho C", "")


def test_jnt_source_times_remain_vietnamese_when_display_timezone_changes(tz):
    from zoneinfo import ZoneInfo

    html = page(record())
    original = adapter(tz).parse(html, NUMBER)
    utc_display = adapter(ZoneInfo("UTC")).parse(html, NUMBER)
    assert original.latest.time == utc_display.latest.time


def test_jnt_arguments_support_phone_flag_and_prefixed_shortcut():
    parsed = BotHandlers.parse_lookup_args(
        [NUMBER, "--carrier", "jnt", "--phone", "0123", "Áo", "mới"], allow_nickname=True
    )
    assert parsed == (NUMBER, "Áo mới", "jnt", "0123")
    assert BotHandlers.parse_lookup_args([NUMBER + "-0123"], allow_nickname=False) == (
        NUMBER,
        "",
        None,
        "0123",
    )
    for args in (
        [NUMBER, "--phone"],
        [NUMBER, "--phone", "123"],
        [NUMBER, "--phone", "0123", "--phone", "0123"],
        [NUMBER + "-0123", "--phone", "9999"],
    ):
        with pytest.raises((TrackingError, ValueError)):
            BotHandlers.parse_lookup_args(args, allow_nickname=True)
    assert detect_carrier(NUMBER) == "jnt"
    assert detect_carrier("841234567890") is None


def test_jnt_is_registered_with_configured_endpoint(settings):
    registry = build_registry(settings, Mock())
    assert registry["jnt"].endpoint == "https://jtexpress.vn/vi/tracking"
    assert registry["jnt"].configured


def test_existing_sqlite_database_is_migrated_without_losing_history(db, tz):
    row = db.add(1, "SPXVN123", "Original", "spx", 50)
    event = TrackingEvent(time=1795657268, status="Old", detail="Old")
    db.ingest(row["id"], Snapshot((event,)), "SPX", tz)
    before_history = db.history(1, "SPXVN123")
    with db.connect() as connection:
        connection.execute("ALTER TABLE shipments DROP COLUMN phone_last4")
    migrated = Database(db.path)
    assert migrated.get(row["id"])["nickname"] == "Original"
    assert migrated.get(row["id"])["phone_last4"] == ""
    assert migrated.history(1, "SPXVN123") == before_history
    assert len(migrated.pending()) == 1


def test_jnt_worker_does_not_share_results_between_different_verification_digits(db, settings):
    first = db.add(1, NUMBER, "First", "jnt", 50, "1111")
    second = db.add(2, NUMBER, "Second", "jnt", 50, "2222")
    event = TrackingEvent(time=1795657268, status="Moving", detail="Moving")

    async def track(number, *, phone_last4):
        assert number == NUMBER
        if phone_last4 == "2222":
            raise TrackingError("Verification failed")
        return Snapshot((event,))

    carrier = Mock(track=AsyncMock(side_effect=track))
    carrier.name = "J&T Express"
    service = TrackingService(settings, db, {"jnt": carrier})
    asyncio.run(service.cycle())
    assert carrier.track.call_count == 2
    assert db.get(first["id"])["initialized"] == 1
    assert db.get(second["id"])["initialized"] == 0
    assert len(db.pending()) == 1
    assert "1111" not in db.pending()[0]["payload"]


def test_jnt_stored_digits_are_reused_for_manual_requests_and_are_per_owner(db, settings):
    event = TrackingEvent(time=1795657268, status="Moving", detail="Moving")
    carrier = Mock(configured=True, track=AsyncMock(return_value=Snapshot((event,))))
    carrier.name = "J&T Express"
    service = TrackingService(settings, db, {"jnt": carrier})

    async def scenario():
        row, error = await service.add(1, NUMBER, "Áo mới", "jnt", "0123")
        assert error is None
        assert row["phone_last4"] == "0123"
        await service.check(1, NUMBER, "jnt")
        await service.history(1, NUMBER, "jnt")
        assert all(call.kwargs == {"phone_last4": "0123"} for call in carrier.track.call_args_list)
        assert db.find(2, NUMBER) is None
        with pytest.raises(TrackingError):
            await service.add(3, NUMBER, "No phone", "jnt")
        assert db.find(3, NUMBER) is None

    asyncio.run(scenario())
