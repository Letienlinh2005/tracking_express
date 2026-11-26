import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.request import BaseRequest

from tracker.bot import BotHandlers, build_application
from tracker.models import Snapshot, TrackingEvent


class FakeTelegramRequest(BaseRequest):
    @property
    def read_timeout(self):
        return 20

    def __init__(self):
        super().__init__()
        self.calls = []

    async def initialize(self):
        return None

    async def shutdown(self):
        return None

    async def do_request(self, url, method, request_data=None, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        parameters = request_data.parameters if request_data else {}
        self.calls.append((endpoint, parameters))
        if endpoint == "getMe":
            result = {
                "id": 123456789,
                "is_bot": True,
                "first_name": "Tracker",
                "username": "test_tracker_bot",
            }
        elif endpoint in {"sendMessage", "editMessageText"}:
            result = {
                "message_id": len(self.calls),
                "date": 1795657268,
                "chat": {"id": int(parameters["chat_id"]), "type": "private"},
                "text": parameters.get("text", ""),
            }
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()

    def sent_texts(self):
        return [
            parameters["text"] for endpoint, parameters in self.calls if endpoint == "sendMessage"
        ]


def command_update(bot, text, uid=1, group=False):
    return Update.de_json(
        {
            "update_id": 10,
            "message": {
                "message_id": 1,
                "date": 1795657268,
                "from": {"id": uid, "is_bot": False, "first_name": "User"},
                "chat": {"id": uid, "type": "group" if group else "private"},
                "text": text,
                "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}],
            },
        },
        bot,
    )


def test_full_command_lifecycle_with_real_ptb_dispatcher(settings):
    async def scenario():
        request = FakeTelegramRequest()
        application = build_application(settings, request)
        service = application.bot_data["service"]
        adapter = Mock(
            configured=True,
            track=AsyncMock(
                return_value=Snapshot(
                    (
                        TrackingEvent(
                            time=1795657268, status="Đang giao hàng", detail="Đang giao hàng"
                        ),
                    )
                )
            ),
        )
        adapter.name = "SPX"
        service.adapters["spx"] = adapter
        await application.initialize()
        try:
            await application.post_init(application)
            assert isinstance(application.job_queue.scheduler, AsyncIOScheduler)
            jobs = application.job_queue.jobs()
            assert {job.name for job in jobs} == {"scan-shipments", "deliver-outbox"}
            assert (
                next(
                    j for j in jobs if j.name == "scan-shipments"
                ).job.trigger.interval.total_seconds()
                == 60
            )
            for text in ["/start", "/add SPXVN123 Tai nghe", "/list", "/check SPXVN123"]:
                await application.process_update(command_update(application.bot, text))
            texts = request.sent_texts()
            assert any("📦 SPXVN123 Tai nghe" in t for t in texts)
            assert any("Danh sách vận đơn" in t and "Đang giao hàng" in t for t in texts)
            assert adapter.track.call_count == 2
            await application.process_update(
                command_update(application.bot, "/del SPXVN123", uid=2)
            )
            assert service.db.find(1, "SPXVN123") is not None
            await application.process_update(command_update(application.bot, "/del SPXVN123"))
            assert service.db.find(1, "SPXVN123") is None
            await application.process_update(command_update(application.bot, "/add 123456789"))
            assert "Chọn hãng" in request.sent_texts()[-1]
            selection = application.user_data[1]["carrier_selection"]
            callback = Update.de_json(
                {
                    "update_id": 20,
                    "callback_query": {
                        "id": "query-1",
                        "chat_instance": "1",
                        "from": {"id": 1, "is_bot": False, "first_name": "User"},
                        "data": f"carrier:{selection['key']}:spx",
                        "message": {
                            "message_id": 22,
                            "date": 1795657268,
                            "chat": {"id": 1, "type": "private"},
                            "text": "Select carrier",
                        },
                    },
                },
                application.bot,
            )
            await application.process_update(callback)
            assert service.db.find(1, "123456789")["carrier"] == "spx"
            # Jobs can start and finish cleanly with the same PTB lifecycle as production.
            await application.start()
            await application.stop()
        finally:
            await application.shutdown()
            await application.post_shutdown(application)

    asyncio.run(scenario())


def test_allowlist_and_private_chat_restrictions(settings):
    async def scenario():
        request = FakeTelegramRequest()
        application = build_application(replace(settings, allowed_users=frozenset({99})), request)
        await application.initialize()
        try:
            await application.process_update(command_update(application.bot, "/add SPXVN123"))
            assert "chưa được cấp quyền" in request.sent_texts()[-1]
            await application.process_update(
                command_update(application.bot, "/add SPXVN123", uid=99, group=True)
            )
            assert "nhắn riêng" in request.sent_texts()[-1]
            assert application.bot_data["service"].db.list_chat(1) == []
        finally:
            await application.shutdown()
            await application.post_shutdown(application)

    asyncio.run(scenario())


def test_argument_validation():
    import pytest

    number, nickname, carrier = BotHandlers.parse_args(
        ["spxvn123", "--carrier", "spx", "Tai", "nghe"], allow_nickname=True
    )
    assert (number, nickname, carrier) == ("SPXVN123", "Tai nghe", "spx")
    for args in [
        [],
        ["a"],
        ["ABC123", "--carrier"],
        ["ABC123", "--carrier", "bad"],
        ["ABC123", "extra"],
    ]:
        with pytest.raises(ValueError):
            BotHandlers.parse_args(args, allow_nickname=False)


def test_history_command_and_check_full_use_full_timeline(settings):
    async def scenario():
        request = FakeTelegramRequest()
        application = build_application(settings, request)
        service = application.bot_data["service"]
        adapter = Mock(
            configured=True,
            track=AsyncMock(
                return_value=Snapshot(
                    (
                        TrackingEvent(
                            time=1795657000,
                            status="Đã đến kho",
                            detail="Đã đến kho",
                            location="Kho A",
                        ),
                        TrackingEvent(
                            time=1795657268, status="Đang giao hàng", detail="Đang giao hàng"
                        ),
                    )
                )
            ),
        )
        adapter.name = "SPX"
        service.adapters["spx"] = adapter
        await application.initialize()
        try:
            await application.post_init(application)
            registered = next(
                parameters for endpoint, parameters in request.calls if endpoint == "setMyCommands"
            )
            assert any(command["command"] == "history" for command in registered["commands"])
            for command in (
                "/history SPXVN123",
                "/check SPXVN123 --full",
                "/history ABC123 --carrier spx",
            ):
                await application.process_update(command_update(application.bot, command))
                text = request.sent_texts()[-1]
                assert "Toàn bộ hành trình" in text
                assert "2 mốc" in text
                assert text.index("Status: Đã đến kho") < text.index("Status: Đang giao hàng")
                assert "📍 Location: Kho A" in text
            assert adapter.track.call_count == 3
            assert service.db.list_chat(1) == []
            assert service.db.pending() == []
            await application.process_update(command_update(application.bot, "/history 123456789"))
            assert "Chọn hãng" in request.sent_texts()[-1]
            assert application.user_data[1]["carrier_selection"]["action"] == "history"
        finally:
            await application.shutdown()
            await application.post_shutdown(application)

    asyncio.run(scenario())


def test_rename_command_updates_only_owners_name_and_all_views(settings):
    async def scenario():
        request = FakeTelegramRequest()
        application = build_application(settings, request)
        service = application.bot_data["service"]
        event = TrackingEvent(time=1795657268, status="Đang giao hàng", detail="Đang giao hàng")
        owner = service.db.add(1, "SPXVN123", "Tên cũ", "spx", 50)
        other = service.db.add(2, "SPXVN123", "Đơn người khác", "spx", 50)
        service.db.ingest(owner["id"], Snapshot((event,)), "SPX", settings.timezone)
        service.db.mark_sent(service.db.pending()[0]["id"])
        adapter = Mock(configured=True, track=AsyncMock(return_value=Snapshot((event,))))
        adapter.name = "SPX"
        service.adapters["spx"] = adapter
        await application.initialize()
        try:
            await application.post_init(application)
            registered = next(
                parameters for endpoint, parameters in request.calls if endpoint == "setMyCommands"
            )
            assert any(command["command"] == "rename" for command in registered["commands"])
            before_history = service.db.history(1, "SPXVN123")
            await application.process_update(
                command_update(application.bot, "/rename spxvn123 Tai nghe Sony")
            )
            assert "Đã đổi tên đơn SPXVN123 thành: Tai nghe Sony" in request.sent_texts()[-1]
            assert service.db.get(owner["id"])["nickname"] == "Tai nghe Sony"
            assert service.db.get(other["id"])["nickname"] == "Đơn người khác"
            assert service.db.history(1, "SPXVN123") == before_history
            adapter.track.assert_not_awaited()
            for command in ("/list", "/check SPXVN123", "/history SPXVN123"):
                await application.process_update(command_update(application.bot, command))
                assert "📦 SPXVN123 Tai nghe Sony" in request.sent_texts()[-1]
            for command in ("/rename", "/rename SPXVN123", "/rename SPXVN123 " + "A" * 101):
                await application.process_update(command_update(application.bot, command))
                assert "Đã đổi tên" not in request.sent_texts()[-1]
                assert service.db.get(owner["id"])["nickname"] == "Tai nghe Sony"
            await application.process_update(
                command_update(application.bot, "/rename SPXVN123 Other", uid=3)
            )
            assert "Không tìm thấy mã trong danh sách của bạn" in request.sent_texts()[-1]
        finally:
            await application.shutdown()
            await application.post_shutdown(application)

    asyncio.run(scenario())


def test_jnt_commands_reuse_verified_phone_and_keep_it_out_of_messages(settings):
    async def scenario():
        request = FakeTelegramRequest()
        application = build_application(settings, request)
        service = application.bot_data["service"]
        event = TrackingEvent(time=1795657268, status="Đang giao hàng", detail="Đang giao hàng")
        carrier = Mock(configured=True, track=AsyncMock(return_value=Snapshot((event,))))
        carrier.name = "J&T Express"
        service.adapters["jnt"] = carrier
        await application.initialize()
        try:
            await application.process_update(
                command_update(application.bot, "/add JNTTEST123456 Áo mới")
            )
            assert "4 số cuối" in request.sent_texts()[-1]
            assert service.db.list_chat(1) == []
            await application.process_update(
                command_update(application.bot, "/add JNTTEST123456-0123 Áo mới")
            )
            row = service.db.find(1, "JNTTEST123456")
            assert row["phone_last4"] == "0123"
            assert "📦 JNTTEST123456 Áo mới" in request.sent_texts()[-1]
            for command in ("/check JNTTEST123456", "/history JNTTEST123456", "/list"):
                await application.process_update(command_update(application.bot, command))
                assert "0123" not in request.sent_texts()[-1]
                assert "Áo mới" in request.sent_texts()[-1]
            assert all(
                call.kwargs == {"phone_last4": "0123"} for call in carrier.track.call_args_list
            )
            await application.process_update(
                command_update(application.bot, "/check 841234567890 --carrier jnt --phone 9999")
            )
            assert carrier.track.call_args.kwargs == {"phone_last4": "9999"}
            assert service.db.find(1, "841234567890") is None
            await application.process_update(
                command_update(application.bot, "/check SPXVN123 --phone 0123")
            )
            assert "chỉ dùng cho J&T" in request.sent_texts()[-1]
        finally:
            await application.shutdown()
            await application.post_shutdown(application)

    asyncio.run(scenario())


def test_adding_delivered_parcel_replies_before_automatically_removing_it(settings):
    async def scenario():
        request = FakeTelegramRequest()
        application = build_application(settings, request)
        service = application.bot_data["service"]
        event = TrackingEvent(
            time=1795657268,
            status="Đã giao thành công",
            detail="Đã giao thành công",
            terminal=True,
            delivered=True,
        )
        carrier = Mock(configured=True, track=AsyncMock(return_value=Snapshot((event,))))
        carrier.name = "SPX"
        service.adapters["spx"] = carrier
        await application.initialize()
        try:
            await application.process_update(
                command_update(application.bot, "/add SPXVN123 Tai nghe")
            )
            assert "Đã giao thành công" in request.sent_texts()[-1]
            assert service.db.find(1, "SPXVN123") is None
            await application.process_update(command_update(application.bot, "/list"))
            assert "Chưa có đơn nào" in request.sent_texts()[-1]
        finally:
            await application.shutdown()
            await application.post_shutdown(application)

    asyncio.run(scenario())
