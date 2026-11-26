import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from tracker.formatting import format_update
from tracker.models import Snapshot, TrackingEvent


class DuplicateShipment(ValueError):
    """A private chat already tracks this code."""


class OrderLimit(ValueError):
    """Per-chat storage limit reached."""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS shipments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    tracking_number TEXT NOT NULL,
                    nickname TEXT NOT NULL DEFAULT '',
                    phone_last4 TEXT NOT NULL DEFAULT '',
                    carrier TEXT NOT NULL,
                    initialized INTEGER NOT NULL DEFAULT 0,
                    last_event_time INTEGER,
                    last_status TEXT NOT NULL DEFAULT '',
                    last_event_json TEXT,
                    completed INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0,
                    next_check_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(chat_id, tracking_number)
                );
                CREATE INDEX IF NOT EXISTS active_shipments ON shipments(completed, next_check_at);
                CREATE TABLE IF NOT EXISTS events (
                    shipment_id INTEGER NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
                    event_key TEXT NOT NULL,
                    event_time INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY(shipment_id, event_key)
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY,
                    shipment_id INTEGER NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
                    event_key TEXT NOT NULL,
                    event_time INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    delivered_at REAL,
                    UNIQUE(shipment_id, event_key)
                );
                CREATE INDEX IF NOT EXISTS pending_messages ON outbox(delivered_at, shipment_id);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(shipments)")}
            if "phone_last4" not in columns:
                db.execute("ALTER TABLE shipments ADD COLUMN phone_last4 TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(
        self,
        chat_id: int,
        number: str,
        nickname: str,
        carrier: str,
        limit: int,
        phone_last4: str = "",
    ) -> dict:
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM shipments WHERE chat_id=? AND tracking_number=?", (chat_id, number)
            ).fetchone():
                raise DuplicateShipment("Mã này đã có trong danh sách. Dùng /check để kiểm tra.")
            count = db.execute(
                "SELECT COUNT(*) FROM shipments WHERE chat_id=?", (chat_id,)
            ).fetchone()[0]
            if count >= limit:
                raise OrderLimit(f"Đã đạt giới hạn {limit} đơn. Dùng /del để xóa bớt.")
            cursor = db.execute(
                """INSERT INTO shipments(chat_id,tracking_number,nickname,carrier,created_at,updated_at,phone_last4)
                   VALUES(?,?,?,?,?,?,?)""",
                (chat_id, number, nickname, carrier, now, now, phone_last4),
            )
            return dict(
                db.execute("SELECT * FROM shipments WHERE id=?", (cursor.lastrowid,)).fetchone()
            )

    def get(self, shipment_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM shipments WHERE id=?", (shipment_id,)).fetchone()
            return dict(row) if row else None

    def find(self, chat_id: int, number: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM shipments WHERE chat_id=? AND tracking_number=?", (chat_id, number)
            ).fetchone()
            return dict(row) if row else None

    def list_chat(self, chat_id: int) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM shipments WHERE chat_id=? ORDER BY created_at DESC,id DESC",
                    (chat_id,),
                )
            ]

    def history(self, chat_id: int, number: str) -> tuple[TrackingEvent, ...]:
        with self.connect() as db:
            return tuple(
                TrackingEvent.from_json(row["event_json"])
                for row in db.execute(
                    """SELECT e.event_json FROM events e
                       JOIN shipments s ON s.id=e.shipment_id
                       WHERE s.chat_id=? AND s.tracking_number=?
                       ORDER BY e.event_time,e.rowid""",
                    (chat_id, number),
                )
            )

    def due(self) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM shipments WHERE completed=0 AND next_check_at<=? ORDER BY next_check_at,id",
                    (time.time(),),
                )
            ]

    def delete(self, chat_id: int, number: str) -> bool:
        with self.connect() as db:
            return bool(
                db.execute(
                    "DELETE FROM shipments WHERE chat_id=? AND tracking_number=?", (chat_id, number)
                ).rowcount
            )

    def rename(
        self, chat_id: int, number: str, nickname: str, carrier_name: str, tz: ZoneInfo
    ) -> bool:
        nickname = " ".join(nickname.split())
        if not nickname:
            raise ValueError("Tên mới không được để trống.")
        if len(nickname) > 100:
            raise ValueError("Tên mới tối đa 100 ký tự.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM shipments WHERE chat_id=? AND tracking_number=?", (chat_id, number)
            ).fetchone()
            if not row:
                return False
            db.execute(
                "UPDATE shipments SET nickname=?,updated_at=? WHERE id=?",
                (nickname, time.time(), row["id"]),
            )
            # Refresh only unsent messages; keep delivery attempts, event history and cursors.
            pending = db.execute(
                """SELECT o.id,e.event_json FROM outbox o JOIN events e
                   ON e.shipment_id=o.shipment_id AND e.event_key=o.event_key
                   WHERE o.shipment_id=? AND o.delivered_at IS NULL""",
                (row["id"],),
            ).fetchall()
            for message in pending:
                payload = format_update(
                    carrier_name,
                    number,
                    nickname,
                    TrackingEvent.from_json(message["event_json"]),
                    tz,
                )
                db.execute("UPDATE outbox SET payload=? WHERE id=?", (payload, message["id"]))
            return True

    def record_failure(self, shipment_id: int, message: str, minimum_delay: float = 0) -> None:
        with self.connect() as db:
            row = db.execute("SELECT failures FROM shipments WHERE id=?", (shipment_id,)).fetchone()
            if not row:
                return
            failures = row[0] + 1
            delay = max(minimum_delay, min(3600, 60 * 2 ** min(failures - 1, 6)))
            db.execute(
                "UPDATE shipments SET failures=?,next_check_at=?,last_error=?,updated_at=? WHERE id=?",
                (failures, time.time() + delay, message, time.time(), shipment_id),
            )

    def ingest(self, shipment_id: int, snapshot: Snapshot, carrier_name: str, tz: ZoneInfo) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM shipments WHERE id=?", (shipment_id,)).fetchone()
            if not row:
                return
            baseline = not row["initialized"]
            watermark = row["last_event_time"]
            new_keys = set()
            for event in snapshot.events:
                inserted = db.execute(
                    "INSERT OR IGNORE INTO events VALUES(?,?,?,?)",
                    (shipment_id, event.key, event.time, event.to_json()),
                ).rowcount
                if inserted:
                    new_keys.add(event.key)
                notify = (baseline and event.key == snapshot.latest.key) or (
                    not baseline and inserted and event.time >= watermark
                )
                if notify:
                    db.execute(
                        """INSERT OR IGNORE INTO outbox(shipment_id,event_key,event_time,payload)
                           VALUES(?,?,?,?)""",
                        (
                            shipment_id,
                            event.key,
                            event.time,
                            format_update(
                                carrier_name, row["tracking_number"], row["nickname"], event, tz
                            ),
                        ),
                    )
            latest = snapshot.latest
            same_as_saved = latest.to_json() == row["last_event_json"]
            if (
                baseline
                or latest.time > watermark
                or (latest.time == watermark and (latest.key in new_keys or same_as_saved))
            ):
                db.execute(
                    """UPDATE shipments SET initialized=1,last_event_time=?,last_status=?,last_event_json=?,
                       completed=?,failures=0,next_check_at=?,last_error=NULL,updated_at=? WHERE id=?""",
                    (
                        latest.time,
                        latest.detail,
                        latest.to_json(),
                        int(bool(row["completed"]) or latest.terminal),
                        0,
                        time.time(),
                        shipment_id,
                    ),
                )
            else:
                # A truncated/stale provider response must not move the cursor backward.
                db.execute(
                    "UPDATE shipments SET failures=0,next_check_at=?,last_error=NULL WHERE id=?",
                    (0, shipment_id),
                )

    def pending(self) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    """SELECT o.*,s.chat_id FROM outbox o JOIN shipments s ON s.id=o.shipment_id
                   WHERE delivered_at IS NULL ORDER BY shipment_id,event_time,o.id"""
                )
            ]

    def mark_sent(self, message_id: int) -> None:
        with self.connect() as db:
            db.execute("UPDATE outbox SET delivered_at=? WHERE id=?", (time.time(), message_id))

    def retry_message(self, message_id: int, attempts: int, delay: float) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE outbox SET attempts=?,next_attempt_at=? WHERE id=?",
                (attempts, time.time() + delay, message_id),
            )
