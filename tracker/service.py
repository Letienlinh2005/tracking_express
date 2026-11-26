import asyncio
import logging
import time
from collections import defaultdict

from telegram import Bot
from telegram.error import Forbidden, RetryAfter, TelegramError

from tracker.adapters.base import CarrierAdapter, NotConfigured, TrackingError
from tracker.adapters.jnt import JNTAdapter
from tracker.config import Settings
from tracker.database import Database
from tracker.models import Snapshot

logger = logging.getLogger(__name__)


class TrackingService:
    def __init__(
        self, settings: Settings, db: Database, adapters: dict[str, CarrierAdapter]
    ) -> None:
        self.settings, self.db, self.adapters = settings, db, adapters
        # Fixed stripes bound lock memory even when a public bot sees many codes.
        self.scan_locks = [asyncio.Lock() for _ in range(64)]
        self.delivery_lock = asyncio.Lock()
        self.cycle_lock = asyncio.Lock()
        self.telegram_cooldown_until = 0.0

    def lock_for(self, carrier: str, number: str) -> asyncio.Lock:
        return self.scan_locks[hash((carrier, number)) % len(self.scan_locks)]

    async def track(self, carrier: str, number: str, phone_last4: str = "") -> Snapshot:
        if carrier == "jnt":
            return await self.adapters[carrier].track(number, phone_last4=phone_last4)
        return await self.adapters[carrier].track(number)

    async def add(
        self, chat_id: int, number: str, nickname: str, carrier: str, phone_last4: str = ""
    ) -> tuple[dict, str | None]:
        adapter = self.adapters[carrier]
        if carrier == "jnt":
            JNTAdapter.validate_phone(phone_last4)
        if not adapter.configured:
            raise NotConfigured(
                f"{adapter.name} chưa có endpoint. Cấu hình config/carriers.json trước."
            )
        async with self.lock_for(carrier, number):
            row = self.db.add(
                chat_id, number, nickname, carrier, self.settings.max_orders, phone_last4
            )
            try:
                snapshot = await self.track(carrier, number, phone_last4)
            except TrackingError as error:
                self.db.record_failure(row["id"], str(error), getattr(error, "seconds", 0))
                return self.db.get(row["id"]), str(error)
            self.db.ingest(
                row["id"],
                snapshot,
                adapter.name,
                self.settings.timezone,
            )
            return self.db.get(row["id"]), None

    async def check(
        self, chat_id: int, number: str, carrier: str, phone_last4: str = ""
    ) -> Snapshot:
        async with self.lock_for(carrier, number):
            row = self.db.find(chat_id, number)
            phone_last4 = phone_last4 or (row["phone_last4"] if row else "")
            try:
                snapshot = await self.track(carrier, number, phone_last4)
            except TrackingError as error:
                if row:
                    self.db.record_failure(row["id"], str(error), getattr(error, "seconds", 0))
                raise
            if row:
                self.db.ingest(
                    row["id"],
                    snapshot,
                    self.adapters[carrier].name,
                    self.settings.timezone,
                )
            return snapshot

    async def delete(self, chat_id: int, number: str) -> bool:
        async with self.delivery_lock:
            return self.db.delete(chat_id, number)

    async def rename(self, chat_id: int, number: str, nickname: str) -> bool:
        async with self.delivery_lock:
            row = self.db.find(chat_id, number)
            if not row:
                return False
            return self.db.rename(
                chat_id,
                number,
                nickname,
                self.adapters[row["carrier"]].name,
                self.settings.timezone,
            )

    async def history(
        self, chat_id: int, number: str, carrier: str, phone_last4: str = ""
    ) -> tuple[Snapshot, str | None]:
        """Refresh the displayed timeline without advancing notification cursors."""
        async with self.lock_for(carrier, number):
            cached = self.db.history(chat_id, number)
            row = self.db.find(chat_id, number)
            phone_last4 = phone_last4 or (row["phone_last4"] if row else "")
            try:
                live = await self.track(carrier, number, phone_last4)
            except TrackingError:
                if not cached:
                    raise
                return Snapshot(
                    cached
                ), "API tạm thời không truy cập được; đang hiển thị lịch sử đã lưu trong bot."
            return Snapshot(cached + live.events), None

    async def cycle(self) -> None:
        if self.cycle_lock.locked():
            return
        async with self.cycle_lock:
            started = time.monotonic()
            groups = defaultdict(list)
            for row in self.db.due():
                groups[(row["carrier"], row["tracking_number"], row["phone_last4"])].append(
                    row["id"]
                )
            for (carrier, number, phone_last4), shipment_ids in groups.items():
                async with self.lock_for(carrier, number):
                    # /check may have refreshed or /del may have removed a queued row.
                    rows = [self.db.get(shipment_id) for shipment_id in shipment_ids]
                    rows = [
                        r
                        for r in rows
                        if r and not r["completed"] and r["next_check_at"] <= time.time()
                    ]
                    if not rows:
                        continue
                    try:
                        snapshot = await self.track(carrier, number, phone_last4)
                        for row in rows:
                            self.db.ingest(
                                row["id"],
                                snapshot,
                                self.adapters[carrier].name,
                                self.settings.timezone,
                            )
                    except TrackingError as error:
                        for row in rows:
                            self.db.record_failure(
                                row["id"], str(error), getattr(error, "seconds", 0)
                            )
                        logger.warning(
                            "Provider failure carrier=%s count=%d reason=%s",
                            carrier,
                            len(rows),
                            error,
                        )
                    except Exception:
                        for row in rows:
                            self.db.record_failure(
                                row["id"], "Lỗi xử lý dữ liệu; xem log trên máy chủ."
                            )
                        # Avoid logging response bodies, HTTP headers or private parcel contents.
                        logger.error("Unexpected tracking failure carrier=%s", carrier)
            elapsed = time.monotonic() - started
            if elapsed > self.settings.poll_interval:
                logger.warning(
                    "Scan took %.1fs for %d distinct parcels; interval=%ds",
                    elapsed,
                    len(groups),
                    self.settings.poll_interval,
                )

    async def deliver(self, bot: Bot, shipment_id: int | None = None) -> None:
        async with self.delivery_lock:
            removed = self.db.prune_delivered(shipment_id)
            if removed:
                logger.info("Removed %d delivered shipments with no pending notifications", removed)
            if time.monotonic() < self.telegram_cooldown_until:
                return
            blocked_shipments: set[int] = set()
            for message in self.db.pending():
                sid = message["shipment_id"]
                if shipment_id is not None and sid != shipment_id:
                    continue
                if sid in blocked_shipments:
                    continue
                if message["next_attempt_at"] > time.time():
                    blocked_shipments.add(sid)
                    continue
                try:
                    await bot.send_message(chat_id=message["chat_id"], text=message["payload"])
                except RetryAfter as error:
                    delay = error.retry_after
                    seconds = (
                        delay.total_seconds() if hasattr(delay, "total_seconds") else float(delay)
                    )
                    self.telegram_cooldown_until = time.monotonic() + seconds + 1
                    self.db.retry_message(message["id"], message["attempts"] + 1, seconds + 1)
                    return
                except Forbidden:
                    # Keep messages, but don't keep hammering a chat that blocked the bot.
                    self.db.retry_message(message["id"], message["attempts"] + 1, 86400)
                    blocked_shipments.add(sid)
                except TelegramError:
                    attempts = message["attempts"] + 1
                    self.db.retry_message(
                        message["id"], attempts, min(3600, 5 * 2 ** min(attempts, 10))
                    )
                    blocked_shipments.add(sid)
                else:
                    self.db.mark_sent(message["id"])
                    if self.db.prune_delivered(sid):
                        logger.info("Removed delivered shipment after its final notification")
                    await asyncio.sleep(0.05)
