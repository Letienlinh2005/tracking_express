import asyncio
import json
import time
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.error import RetryAfter, TimedOut

from tracker.adapters.ghn import GHNAdapter
from tracker.database import Database
from tracker.models import Snapshot, TrackingEvent
from tracker.service import TrackingService


def event(offset=0, status="Đang giao hàng", *, terminal=False, delivered=False):
    return TrackingEvent(
        time=1795657268 + offset,
        status=status,
        detail=status,
        terminal=terminal,
        delivered=delivered,
    )


def insert(db, tz, *, chat=1, final=None):
    row = db.add(chat, "SPXVN123", "Tai nghe", "spx", 50)
    db.ingest(
        row["id"], Snapshot((final or event(1, "Đã giao thành công", terminal=True),)), "SPX", tz
    )
    return row


def test_successful_final_notification_then_delete_only_that_owners_parcel(db, tz, settings):
    delivered = insert(db, tz)
    other = insert(db, tz, chat=2, final=event())
    bot = Mock(send_message=AsyncMock())
    service = TrackingService(settings, db, {})
    asyncio.run(service.deliver(bot, delivered["id"]))
    bot.send_message.assert_awaited_once()
    assert "Đã giao thành công" in bot.send_message.call_args.kwargs["text"]
    assert db.find(1, "SPXVN123") is None
    assert db.list_chat(1) == []
    assert db.history(1, "SPXVN123") == ()
    assert db.get(other["id"]) is not None
    assert len(db.pending()) == 1
    with db.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE shipment_id=?", (delivered["id"],)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM outbox WHERE shipment_id=?", (delivered["id"],)
            ).fetchone()[0]
            == 0
        )


def test_all_new_milestones_must_be_sent_before_removing_delivery(db, tz, settings):
    row = insert(db, tz, final=event())
    db.ingest(
        row["id"],
        Snapshot((event(1, "Đã giao thành công", terminal=True, delivered=True),)),
        "SPX",
        tz,
    )
    bot = Mock(send_message=AsyncMock(side_effect=[None, TimedOut()]))
    asyncio.run(TrackingService(settings, db, {}).deliver(bot))
    assert bot.send_message.call_count == 2
    assert db.get(row["id"]) is not None
    assert len(db.pending()) == 1
    assert "Đã giao thành công" in db.pending()[0]["payload"]
    with db.connect() as connection:
        connection.execute("UPDATE outbox SET next_attempt_at=0")
    restarted_db = Database(db.path)
    bot = Mock(send_message=AsyncMock())
    asyncio.run(TrackingService(settings, restarted_db, {}).deliver(bot))
    bot.send_message.assert_awaited_once()
    assert restarted_db.get(row["id"]) is None


def test_rate_limited_final_notification_keeps_delivery_and_retry_state(
    db, tz, settings, monkeypatch
):
    monkeypatch.setenv("PTB_TIMEDELTA", "true")
    row = insert(db, tz)
    bot = Mock(send_message=AsyncMock(side_effect=RetryAfter(30)))
    service = TrackingService(settings, db, {})
    asyncio.run(service.deliver(bot))
    assert db.get(row["id"]) is not None
    assert db.pending()[0]["next_attempt_at"] > time.time() + 29
    assert db.prune_delivered() == 0


@pytest.mark.parametrize("status", ["Delivered", "Đã giao hàng thành công", "Đã giao thành công"])
def test_existing_deliveries_without_new_flag_are_cleaned_after_restart(db, tz, settings, status):
    row = insert(db, tz, final=event(1, status, terminal=True))
    with db.connect() as connection:
        legacy = json.loads(
            connection.execute(
                "SELECT last_event_json FROM shipments WHERE id=?", (row["id"],)
            ).fetchone()[0]
        )
        legacy.pop("delivered")
        connection.execute(
            "UPDATE shipments SET last_event_json=? WHERE id=?", (json.dumps(legacy), row["id"])
        )
    db.mark_sent(db.pending()[0]["id"])
    bot = Mock(send_message=AsyncMock())
    asyncio.run(TrackingService(settings, Database(db.path), {}).deliver(bot))
    assert db.get(row["id"]) is None
    bot.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    "status,terminal",
    [
        ("Đang giao hàng", False),
        ("Đã hủy", True),
        ("Đã hoàn hàng", True),
        ("Chưa giao hàng thành công", True),
        ("Delivery failed", True),
        ("Returning to sender", True),
        ("Returned to sender", True),
    ],
)
def test_unfinished_cancelled_or_returned_parcels_are_kept(db, tz, settings, status, terminal):
    row = insert(db, tz, final=event(1, status, terminal=terminal))
    asyncio.run(TrackingService(settings, db, {}).deliver(Mock(send_message=AsyncMock())))
    assert db.get(row["id"]) is not None
    assert len(db.list_chat(1)) == 1


def test_unknown_terminal_payload_is_kept(db, tz):
    row = insert(db, tz)
    db.mark_sent(db.pending()[0]["id"])
    with db.connect() as connection:
        connection.execute(
            "UPDATE shipments SET last_event_json='invalid JSON' WHERE id=?", (row["id"],)
        )
    assert db.prune_delivered() == 0
    assert db.get(row["id"]) is not None


def test_configurable_carrier_distinguishes_delivered_and_cancelled_codes(tz):
    adapter = GHNAdapter(
        Mock(),
        {
            "terminal_codes": [40],
            "delivered_codes": [99],
        },
        tz,
    )
    delivered = adapter.parse(
        {"events": [{"time": 1795657268, "code": 99, "detail": "Trạng thái cuối"}]}
    )
    cancelled = adapter.parse({"events": [{"time": 1795657268, "code": 40, "detail": "Đã hủy"}]})
    assert delivered.latest.delivered_successfully
    assert cancelled.latest.terminal
    assert not cancelled.latest.delivered_successfully
