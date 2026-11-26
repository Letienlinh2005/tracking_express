import asyncio
import json
import time
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.error import RetryAfter, TimedOut

from tracker.adapters.base import NotConfigured, RateLimited, TrackingError
from tracker.adapters.registry import detect_carrier
from tracker.adapters.spx import SPXAdapter, is_terminal
from tracker.database import Database, DuplicateShipment, OrderLimit
from tracker.formatting import format_update
from tracker.http import TrackingHTTP, retry_seconds
from tracker.models import Snapshot, TrackingEvent, parse_time
from tracker.service import TrackingService

BASE_TIME = 1795650000


def event(offset=0, detail="Đã đến kho", **kwargs):
    return TrackingEvent(time=BASE_TIME + offset, status=detail, detail=detail, **kwargs)


def add(db, chat=1, number="SPXVN123"):
    return db.add(chat, number, "Tai nghe", "spx", 50)


def save(db, row, tz, *events):
    db.ingest(row["id"], Snapshot(events), "SPX", tz)


def test_exact_message_and_optional_locations(tz):
    e = TrackingEvent(time=1795657268, status="Đang giao hàng", detail="Đang giao hàng")
    assert format_update("SPX", "SPXVN123", "Tai nghe", e, tz) == (
        "🔔 SPX Update: Đang giao hàng\n\n📦 SPXVN123 Tai nghe\n"
        "📌 Status: Đang giao hàng\n🕒 Date: 08:41:08 26/11/2026"
    )
    message = format_update(
        "SPX", "SPXVN123", "", event(location="Kho A", next_location="Kho B"), tz
    )
    assert "📍 Location: Kho A\n➡️ Next Location: Kho B" in message
    assert "📦 SPXVN123\n" in message
    injected = format_update("SPX", "ABC123", "<b>\n⚠️", event(detail="a\nb"), tz)
    assert "Status: a b\n" in injected


def test_first_scan_only_latest_and_restart_deduplication(db, tz):
    row = add(db)
    save(db, row, tz, event(), event(10), event(20))
    assert len(db.pending()) == 1
    assert db.get(row["id"])["last_event_time"] == BASE_TIME + 20
    db.mark_sent(db.pending()[0]["id"])
    restarted = Database(db.path)
    save(restarted, row, tz, event(), event(10), event(20))
    assert restarted.pending() == []
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3


def test_multiple_new_events_chronological_and_equal_timestamp(db, tz):
    row = add(db)
    save(db, row, tz, event())
    db.mark_sent(db.pending()[0]["id"])
    save(db, row, tz, event(20, "B"), event(10, "A"), event(20, "C"), event())
    pending = db.pending()
    assert [p["event_time"] for p in pending] == [BASE_TIME + 10, BASE_TIME + 20, BASE_TIME + 20]
    assert "Status: A" in pending[0]["payload"]
    for p in pending:
        db.mark_sent(p["id"])
    save(db, row, tz, event(20, "D"), event(20, "B"), event(20, "C"))
    assert len(db.pending()) == 1
    assert "Status: D" in db.pending()[0]["payload"]


def test_stale_response_does_not_regress_cursor(db, tz):
    row = add(db)
    save(db, row, tz, event(100, "Mới"))
    save(db, row, tz, event(50, "Cũ"))
    assert db.get(row["id"])["last_status"] == "Mới"
    assert len(db.pending()) == 1


def test_successful_scan_remains_due_for_next_scheduler_tick(db, tz):
    row = add(db)
    save(db, row, tz, event())
    assert [r["id"] for r in db.due()] == [row["id"]]


def test_equal_time_stale_snapshot_does_not_change_latest(db, tz):
    row = add(db)
    save(db, row, tz, event(0, "A"), event(0, "B"))
    save(db, row, tz, event(0, "A"))
    assert db.get(row["id"])["last_status"] == "B"


def test_deleted_shipment_id_cannot_be_reused_by_an_inflight_scan(db, tz):
    row = add(db)
    db.delete(1, "SPXVN123")
    newer = add(db, number="SPXVN456")
    assert newer["id"] != row["id"]
    save(db, row, tz, event())
    assert not db.get(newer["id"])["initialized"]


def test_terminal_order_stops_polling_and_keeps_final_outbox(db, tz):
    row = add(db)
    save(db, row, tz, event())
    save(db, row, tz, event(50, "Đã giao thành công", terminal=True))
    assert db.get(row["id"])["completed"] == 1
    assert db.due() == []
    assert len(db.pending()) == 2


def test_manual_stale_check_cannot_reopen_completed_parcel(db, tz):
    row = add(db)
    save(db, row, tz, event(50, "Delivered", terminal=True))
    save(db, row, tz, event(50, "Delivering"))
    assert db.get(row["id"])["completed"] == 1
    assert db.due() == []


@pytest.mark.parametrize(
    "name",
    ["Delivered", "Cancelled", "Returned to Sender", "Đã hoàn hàng", "Đã giao hàng thành công"],
)
def test_terminal_positive(name):
    assert is_terminal(name)


@pytest.mark.parametrize(
    "name",
    [
        "Out For Delivery",
        "Delivery failed",
        "Returning to sender",
        "Đang hoàn hàng",
        "Chưa giao hàng thành công",
        "Cancel requested",
    ],
)
def test_terminal_negative(name):
    assert not is_terminal(name)


def test_owner_isolation_and_cascade_delete(db, tz):
    first, second = add(db), add(db, chat=2)
    save(db, first, tz, event())
    save(db, second, tz, event())
    assert db.delete(99, "SPXVN123") is False
    assert db.delete(1, "SPXVN123") is True
    assert db.find(1, "SPXVN123") is None
    assert db.find(2, "SPXVN123") is not None
    assert len(db.pending()) == 1
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_limits_and_failure_backoff(db):
    row = add(db)
    with pytest.raises(DuplicateShipment):
        add(db)
    with pytest.raises(OrderLimit):
        db.add(1, "NEW123", "", "spx", 1)
    db.record_failure(row["id"], "temporary", 120)
    assert db.due() == []
    assert db.get(row["id"])["next_check_at"] >= time.time() + 119


def spx_payload():
    return {
        "retcode": 0,
        "data": {
            "order_info": {"spx_tn": "SPXVN123"},
            "sls_tracking_info": {
                "records": [
                    {
                        "actual_time": BASE_TIME + 10,
                        "tracking_code": "F600",
                        "tracking_name": "Out For Delivery",
                        "description": "Đang giao hàng",
                        "current_location": {"location_name": ""},
                    },
                    {
                        "actual_time": BASE_TIME,
                        "tracking_code": "F599",
                        "tracking_name": "Enter Last Mile Hub",
                        "description": "Đã đến kho",
                        "current_location": {"location_name": "Kho A"},
                        "next_location": {"location_name": "Kho B"},
                        "display_flag": 0,
                    },
                ]
            },
        },
    }


def test_spx_actual_contract_and_no_invented_location(tz):
    http = Mock()
    adapter = SPXAdapter(http, "https://spx.vn/api", tz)
    snapshot = adapter.parse(spx_payload(), "SPXVN123")
    assert snapshot.latest.status == "Đang giao hàng"
    assert snapshot.latest.location == ""
    assert snapshot.events[0].location == "Kho A"
    assert snapshot.events[0].next_location == "Kho B"
    assert not snapshot.latest.terminal
    payload = spx_payload()
    del payload["data"]["sls_tracking_info"]["records"][0]["actual_time"]
    with pytest.raises(TrackingError):
        adapter.parse(payload)
    with pytest.raises(TrackingError):
        adapter.parse(spx_payload(), "WRONG123")
    with pytest.raises(TrackingError):
        adapter.parse({"retcode": 0, "data": {}})


def test_json_adapter_maps_contract_and_env(tz, monkeypatch):
    from tracker.adapters.ghn import GHNAdapter

    http = Mock(
        json=AsyncMock(
            return_value={
                "ok": True,
                "data": {
                    "history": [
                        {
                            "created": "2026-11-26T08:00:00+07:00",
                            "status_id": 99,
                            "text": "Đã giao",
                            "hub": {"name": "Kho A"},
                        }
                    ]
                },
            }
        )
    )
    profile = {
        "endpoint": "https://example.com/tracking",
        "method": "POST",
        "body": {"number": "{tracking_number}"},
        "headers": {"Token": "${TEST_CARRIER_TOKEN}"},
        "success": {"path": "ok", "equals": True},
        "events_path": "data.history",
        "fields": {
            "time": "created",
            "code": "status_id",
            "detail": "text",
            "location": "hub.name",
        },
        "terminal_codes": [99],
    }
    monkeypatch.setenv("TEST_CARRIER_TOKEN", "dummy")
    adapter = GHNAdapter(http, profile, tz)
    snapshot = asyncio.run(adapter.track("GY123"))
    assert snapshot.latest.terminal
    assert snapshot.latest.location == "Kho A"
    assert http.json.call_args.kwargs["json"] == {"number": "GY123"}
    assert http.json.call_args.kwargs["headers"] == {"Token": "dummy"}
    monkeypatch.delenv("TEST_CARRIER_TOKEN")
    with pytest.raises(NotConfigured):
        asyncio.run(adapter.track("GY123"))
    with pytest.raises(NotConfigured):
        asyncio.run(GHNAdapter(http, {}, tz).track("GY123"))


def test_timestamps_and_detection(tz):
    assert parse_time(1795657268000, tz) == 1795657268
    assert parse_time("2026-11-26T08:41:08", tz) == 1795657268
    assert parse_time("2026-11-26T01:41:08Z", tz) == 1795657268
    with pytest.raises(ValueError):
        parse_time(None, tz)
    assert detect_carrier("spxvn123") == "spx"
    assert detect_carrier("GY123") == "ghn"
    assert detect_carrier("G8123") == "ghn"
    assert detect_carrier("123456789") is None


def test_outbox_failure_preserves_order_and_restart(db, tz, settings):
    row = add(db)
    save(db, row, tz, event())
    save(db, row, tz, event(10, "B"), event(20, "C"))
    bot = Mock(send_message=AsyncMock(side_effect=TimedOut()))
    service = TrackingService(settings, db, {})
    asyncio.run(service.deliver(bot))
    assert bot.send_message.call_count == 1
    assert len(db.pending()) == 3
    with db.connect() as connection:
        connection.execute("UPDATE outbox SET next_attempt_at=0")
    restarted = TrackingService(settings, Database(db.path), {})
    bot = Mock(send_message=AsyncMock())
    asyncio.run(restarted.deliver(bot))
    assert bot.send_message.call_count == 3
    assert "Status: B" in bot.send_message.call_args_list[1].kwargs["text"]
    assert db.pending() == []


def test_telegram_retry_after(db, tz, settings):
    row = add(db)
    save(db, row, tz, event())
    bot = Mock(send_message=AsyncMock(side_effect=RetryAfter(30)))
    service = TrackingService(settings, db, {})
    asyncio.run(service.deliver(bot))
    assert len(db.pending()) == 1
    assert service.telegram_cooldown_until > time.monotonic() + 29


def test_worker_deduplicates_shared_parcel(db, tz, settings):
    add(db, chat=1)
    add(db, chat=2)
    adapter = Mock(name="SPX", track=AsyncMock(return_value=Snapshot((event(),))))
    adapter.name = "SPX"
    service = TrackingService(settings, db, {"spx": adapter})
    asyncio.run(service.cycle())
    assert adapter.track.call_count == 1
    assert len(db.pending()) == 2


def test_transient_add_failure_is_retained_uninitialized(db, settings):
    adapter = Mock(configured=True, track=AsyncMock(side_effect=TrackingError("Temporary")))
    service = TrackingService(settings, db, {"spx": adapter})
    row, error = asyncio.run(service.add(1, "SPXVN123", "Name", "spx"))
    assert error == "Temporary"
    assert row["initialized"] == 0
    assert row["last_event_time"] is None
    assert row["failures"] == 1


def test_http_honors_cooldown_and_keeps_browser_headers():
    transport = TrackingHTTP(1, 3, 20)
    response = Mock(status_code=429, headers={"Retry-After": "120"})
    transport.session.request = Mock(return_value=response)

    async def scenario():
        with pytest.raises(RateLimited) as error:
            await transport.json("GET", "https://spx.vn/api")
        assert error.value.seconds == 120
        with pytest.raises(RateLimited):
            await transport.json("GET", "https://spx.vn/api")

    asyncio.run(scenario())
    assert transport.session.request.call_count == 1
    assert "Mozilla/5.0" in transport.session.headers["User-Agent"]
    assert transport.session.request.call_args.kwargs["allow_redirects"] is False
    response.close.assert_called_once()
    transport.close()
    assert retry_seconds("invalid") == 60


def test_curl_backend_json_response_and_headers():
    transport = TrackingHTTP(1, 3, 20, backend="curl_cffi")
    response = Mock(status_code=200, headers={}, json=Mock(return_value={"ok": True}))
    transport.session.request = Mock(return_value=response)
    result = asyncio.run(transport.json("GET", "https://example.com/tracking"))
    assert result == {"ok": True}
    assert transport.session.impersonate == "chrome"
    assert transport.session.headers["Accept-Language"].startswith("vi-VN")
    response.close.assert_called_once()
    transport.close()


@pytest.mark.parametrize("url", ["http://example.com", "https://user:secret@example.com", "/api"])
def test_endpoint_validation(url):
    with pytest.raises(ValueError):
        TrackingHTTP.validate_url(url)


def test_example_config_is_valid():
    from pathlib import Path

    profiles = json.loads(Path("config/carriers.example.json").read_text())
    assert set(profiles) == {"ghn", "ghtk", "vtp"}
    assert all(p["endpoint"] is None for p in profiles.values())


def test_full_history_pages_are_chronological_and_within_telegram_limit(tz):
    from tracker.formatting import format_history

    events = tuple(event(i, f"Mốc {i}", location="Kho A" if i == 1 else "") for i in range(100))
    pages = format_history("SPX", "SPXVN123", "Tai nghe", Snapshot(events[::-1]), tz)
    assert len(pages) > 1
    assert all(len(page.encode("utf-16-le")) // 2 <= 4096 for page in pages)
    combined = "\n".join(pages)
    for i in range(100):
        assert f"Status: Mốc {i}\n" in combined + "\n"
    assert combined.index("Status: Mốc 0\n") < combined.index("Status: Mốc 99")
    assert combined.count("📍 Location:") == 1
    assert "➡️ Next Location:" not in combined
    assert "📦 SPXVN123 Tai nghe" in pages[0]


def test_full_history_handles_long_unicode_fields(tz):
    from tracker.formatting import format_history

    events = tuple(
        event(i, "📦" * 1500, location="📦" * 300, next_location="📦" * 300) for i in range(5)
    )
    pages = format_history("📦" * 40, "A" * 64, "📦" * 100, Snapshot(events), tz, "📦" * 300)
    assert all(len(page.encode("utf-16-le")) // 2 <= 4096 for page in pages)
    assert "Phần 5/5" in pages[-1]


def test_history_merges_cached_and_live_without_changing_notifications(db, tz, settings):
    row = add(db)
    save(db, row, tz, event(0, "A"), event(10, "B"))
    before_row, before_pending = db.get(row["id"]), db.pending()
    adapter = Mock(track=AsyncMock(return_value=Snapshot((event(10, "B"), event(20, "C")))))
    service = TrackingService(settings, db, {"spx": adapter})
    result, warning = asyncio.run(service.history(1, "SPXVN123", "spx"))
    assert [e.detail for e in result.events] == ["A", "B", "C"]
    assert warning is None
    assert db.get(row["id"]) == before_row
    assert db.pending() == before_pending
    assert len(db.history(1, "SPXVN123")) == 2
    assert db.history(2, "SPXVN123") == ()


def test_history_falls_back_to_own_cache_when_provider_unavailable(db, tz, settings):
    row = add(db)
    save(db, row, tz, event())
    adapter = Mock(track=AsyncMock(side_effect=TrackingError("Unavailable")))
    service = TrackingService(settings, db, {"spx": adapter})
    result, warning = asyncio.run(service.history(1, "SPXVN123", "spx"))
    assert len(result.events) == 1
    assert "đã lưu" in warning
    with pytest.raises(TrackingError):
        asyncio.run(service.history(2, "SPXVN123", "spx"))


def test_rename_preserves_history_completed_state_and_delivery_retries(db, tz, settings):
    row = add(db)
    save(db, row, tz, event())
    sent_id = db.pending()[0]["id"]
    db.mark_sent(sent_id)
    save(db, row, tz, event(10, "Delivered", terminal=True))
    message = db.pending()[0]
    db.retry_message(message["id"], 3, 120)
    before_pending = db.pending()[0]
    before_row = db.get(row["id"])
    before_history = db.history(1, "SPXVN123")
    adapter = Mock()
    adapter.name = "SPX"
    service = TrackingService(settings, db, {"spx": adapter})
    assert asyncio.run(service.rename(1, "SPXVN123", "  Tai nghe Sony  "))
    renamed = db.get(row["id"])
    assert renamed["nickname"] == "Tai nghe Sony"
    assert {k: v for k, v in renamed.items() if k not in {"nickname", "updated_at"}} == {
        k: v for k, v in before_row.items() if k not in {"nickname", "updated_at"}
    }
    assert db.history(1, "SPXVN123") == before_history
    assert db.due() == []
    after_pending = db.pending()[0]
    assert "📦 SPXVN123 Tai nghe Sony" in after_pending["payload"]
    assert {k: v for k, v in after_pending.items() if k != "payload"} == {
        k: v for k, v in before_pending.items() if k != "payload"
    }
    with db.connect() as connection:
        sent = connection.execute(
            "SELECT payload,delivered_at FROM outbox WHERE id=?", (sent_id,)
        ).fetchone()
        assert "Tai nghe Sony" not in sent["payload"]
        assert sent["delivered_at"] is not None


def test_rename_cannot_change_another_users_parcel(db, settings):
    row = add(db)
    service = TrackingService(settings, db, {})
    assert asyncio.run(service.rename(2, "SPXVN123", "Other")) is False
    assert db.get(row["id"])["nickname"] == "Tai nghe"


@pytest.mark.parametrize("nickname", ["", "   ", "A" * 101])
def test_rename_rejects_invalid_names_without_changing_data(db, tz, nickname):
    row = add(db)
    before = db.get(row["id"])
    with pytest.raises(ValueError):
        db.rename(1, "SPXVN123", nickname, "SPX", tz)
    assert db.get(row["id"]) == before
