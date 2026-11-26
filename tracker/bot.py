import asyncio
import logging
import re
import secrets
import time

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from telegram.request import BaseRequest

from tracker.adapters.base import TrackingError
from tracker.adapters.jnt import JNTAdapter
from tracker.adapters.registry import build_registry, detect_carrier
from tracker.config import Settings
from tracker.database import Database, DuplicateShipment
from tracker.formatting import clean, format_history, format_update
from tracker.http import TrackingHTTP
from tracker.models import display_time
from tracker.service import TrackingService

logger = logging.getLogger(__name__)

HELP = """Bot theo dõi vận đơn tự động mỗi 60 giây.

/add <mã> [tên gợi nhớ] — thêm và kiểm tra ngay
/rename <mã> <tên mới> — đổi tên đơn đã theo dõi
/list — xem các đơn và trạng thái mới nhất
/check <mã> — kiểm tra ngay (có thể dùng khi chưa thêm)
/history <mã> — xem toàn bộ hành trình từ cũ đến mới
/del <mã> — xóa đơn và thông báo đang chờ

Ví dụ: /add SPXVN0123456789 Tai nghe
Cũng có thể dùng: /check <mã> --full
Chọn hãng thủ công: /add <mã> --carrier spx [tên]
Hãng: spx, jnt, ghn, ghtk, vtp. Mã chưa nhận diện được sẽ hiện nút chọn hãng.
J&T: /add <mã> --carrier jnt --phone <4 số cuối điện thoại> [tên]
Đơn đã giao/hủy/hoàn tất hoàn hàng sẽ tự ngừng quét. /list vẫn giữ đơn để tra cứu.
GHN/GHTK/Viettel Post cần cấu hình API trước. Bot dùng trong chat riêng."""


class BotHandlers:
    def __init__(self, service: TrackingService) -> None:
        self.service = service

    async def authorized(self, update: Update) -> bool:
        if not update.effective_chat or not update.effective_user:
            return False
        if update.effective_chat.type != ChatType.PRIVATE:
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Hãy nhắn riêng cho bot để quản lý vận đơn của bạn."
                )
            return False
        allowed = self.service.settings.allowed_users
        if allowed and update.effective_user.id not in allowed:
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Tài khoản này chưa được cấp quyền dùng bot."
                )
            return False
        return True

    @staticmethod
    def parse_args(args: list[str], *, allow_nickname: bool) -> tuple[str, str, str | None]:
        if not args:
            raise ValueError("Thiếu mã vận đơn.")
        number = args[0].strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{2,63}", number):
            raise ValueError("Mã cần có 3–64 ký tự chữ/số hoặc dấu . _ -.")
        remaining = args[1:].copy()
        carrier = None
        if "--carrier" in remaining:
            index = remaining.index("--carrier")
            if index + 1 >= len(remaining):
                raise ValueError("Dùng --carrier spx|jnt|ghn|ghtk|vtp.")
            carrier = remaining[index + 1].lower()
            del remaining[index : index + 2]
            if carrier not in {"spx", "jnt", "ghn", "ghtk", "vtp"}:
                raise ValueError("Hãng hợp lệ: spx, jnt, ghn, ghtk, vtp.")
        if any(part.startswith("--") for part in remaining):
            raise ValueError("Tùy chọn không hợp lệ. Dùng --carrier spx|jnt|ghn|ghtk|vtp.")
        if remaining and not allow_nickname:
            raise ValueError("Lệnh này chỉ nhận mã vận đơn và tùy chọn --carrier.")
        nickname = " ".join(remaining)
        if len(nickname) > 100:
            raise ValueError("Tên gợi nhớ tối đa 100 ký tự.")
        return number, nickname, carrier

    @classmethod
    def parse_lookup_args(
        cls, args: list[str], *, allow_nickname: bool
    ) -> tuple[str, str, str | None, str]:
        args = list(args)
        phone = ""
        if "--phone" in args:
            index = args.index("--phone")
            if args.count("--phone") != 1 or index + 1 >= len(args):
                raise ValueError("Dùng --phone <đúng 4 số cuối điện thoại> một lần.")
            phone = args[index + 1]
            JNTAdapter.validate_phone(phone)
            del args[index : index + 2]
        if args:
            combined = re.fullmatch(r"(JNT[A-Z0-9]+)-([0-9]{4})", args[0].upper())
            if combined:
                if phone and phone != combined[2]:
                    raise ValueError("Hai giá trị 4 số cuối điện thoại không khớp nhau.")
                args[0], phone = combined[1], combined[2]
        number, nickname, carrier = cls.parse_args(args, allow_nickname=allow_nickname)
        return number, nickname, carrier, phone

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self.authorized(update):
            await update.effective_message.reply_text(HELP)

    async def select_or_execute(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, action: str
    ) -> None:
        if not await self.authorized(update):
            return
        try:
            args = list(context.args)
            if action == "check" and "--full" in args:
                args.remove("--full")
                action = "history"
            number, nickname, requested, phone = self.parse_lookup_args(
                args, allow_nickname=action == "add"
            )
            stored = self.service.db.find(update.effective_chat.id, number)
            if stored and action == "add":
                raise DuplicateShipment(
                    "Mã này đã có trong danh sách. Dùng /check để kiểm tra hoặc /rename <mã> <tên mới> để đổi tên."
                )
            if stored and requested and stored["carrier"] != requested:
                raise ValueError("Mã đã được lưu với hãng khác. /del rồi /add để đổi hãng.")
            carrier = requested or (stored["carrier"] if stored else detect_carrier(number))
            phone = phone or (stored["phone_last4"] if stored else "")
            if not carrier:
                # Persist no untrusted nickname in callback_data; use a bounded, expiring selection.
                key = secrets.token_hex(8)
                context.user_data["carrier_selection"] = {
                    "key": key,
                    "expires": time.time() + 600,
                    "number": number,
                    "nickname": nickname,
                    "action": action,
                    "phone_last4": phone,
                }
                buttons = [
                    [
                        InlineKeyboardButton(
                            adapter.name + ("" if adapter.configured else " (chưa cấu hình)"),
                            callback_data=f"carrier:{key}:{code}",
                        )
                    ]
                    for code, adapter in self.service.adapters.items()
                ]
                await update.effective_message.reply_text(
                    f"Chọn hãng cho {number} (hết hạn sau 10 phút):",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                return
            await self.execute(update, context, action, number, nickname, carrier, phone)
        except (ValueError, TrackingError) as error:
            await update.effective_message.reply_text(str(error))

    async def add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.select_or_execute(update, context, "add")

    async def check(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.select_or_execute(update, context, "check")

    async def history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.select_or_execute(update, context, "history")

    async def rename(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.authorized(update):
            return
        try:
            if len(context.args) < 2:
                raise ValueError("Dùng /rename <mã vận đơn> <tên mới>.")
            number, _, _ = self.parse_args(context.args[:1], allow_nickname=False)
            nickname = " ".join(" ".join(context.args[1:]).split())
            if not nickname:
                raise ValueError("Tên mới không được để trống.")
            if len(nickname) > 100:
                raise ValueError("Tên mới tối đa 100 ký tự.")
            changed = await self.service.rename(update.effective_chat.id, number, nickname)
            await update.effective_message.reply_text(
                f"✅ Đã đổi tên đơn {number} thành: {nickname}"
                if changed
                else "Không tìm thấy mã trong danh sách của bạn. Dùng /list để xem các đơn."
            )
        except ValueError as error:
            await update.effective_message.reply_text(str(error))

    async def choose(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        if not await self.authorized(update):
            return
        selection = context.user_data.get("carrier_selection")
        _, key, carrier = query.data.split(":", 2)
        if not selection or selection["key"] != key or selection["expires"] < time.time():
            await query.edit_message_text(
                "Lựa chọn đã hết hạn hoặc đã thay bằng yêu cầu mới. Gửi lại lệnh."
            )
            return
        if carrier not in self.service.adapters:
            return
        if not self.service.adapters[carrier].configured:
            await update.effective_message.reply_text(
                f"{self.service.adapters[carrier].name} chưa có endpoint. Cấu hình API trước hoặc chọn hãng khác."
            )
            return
        context.user_data.pop("carrier_selection", None)
        await query.edit_message_text(
            f"Đã chọn {self.service.adapters[carrier].name} cho {selection['number']}."
        )
        try:
            await self.execute(
                update,
                context,
                selection["action"],
                selection["number"],
                selection["nickname"],
                carrier,
                selection.get("phone_last4", ""),
            )
        except (ValueError, TrackingError) as error:
            await update.effective_message.reply_text(str(error))

    async def execute(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        action: str,
        number: str,
        nickname: str,
        carrier: str,
        phone_last4: str = "",
    ) -> None:
        chat_id = update.effective_chat.id
        if carrier == "jnt":
            JNTAdapter.validate_phone(phone_last4)
        elif phone_last4:
            raise ValueError("Tùy chọn --phone chỉ dùng cho J&T Express.")
        if action == "add":
            row, error = await self.service.add(chat_id, number, nickname, carrier, phone_last4)
            if error:
                await update.effective_message.reply_text(
                    f"Đã lưu {number}. Chưa lấy được trạng thái: {error}\nBot sẽ tự thử lại. Xem /list để kiểm tra."
                )
            else:
                await self.service.deliver(context.bot, row["id"])
        elif action == "history":
            snapshot, warning = await self.service.history(chat_id, number, carrier, phone_last4)
            stored = self.service.db.find(chat_id, number)
            pages = format_history(
                self.service.adapters[carrier].name,
                number,
                stored["nickname"] if stored else "",
                snapshot,
                self.service.settings.timezone,
                warning,
            )
            for index, page in enumerate(pages):
                if index:
                    await asyncio.sleep(1.05)
                await update.effective_message.reply_text(page)
        else:
            snapshot = await self.service.check(chat_id, number, carrier, phone_last4)
            stored = self.service.db.find(chat_id, number)
            latest_is_queued = stored and any(
                message["shipment_id"] == stored["id"]
                and message["event_key"] == snapshot.latest.key
                for message in self.service.db.pending()
            )
            if latest_is_queued:
                # Deliver all new milestones in order, including the current status, exactly once.
                await self.service.deliver(context.bot, stored["id"])
            else:
                await update.effective_message.reply_text(
                    format_update(
                        self.service.adapters[carrier].name,
                        number,
                        stored["nickname"] if stored else "",
                        snapshot.latest,
                        self.service.settings.timezone,
                    )
                )

    async def list_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.authorized(update):
            return
        orders = self.service.db.list_chat(update.effective_chat.id)
        if not orders:
            await update.effective_message.reply_text(
                "Chưa có đơn nào. Dùng /add <mã> [tên gợi nhớ]."
            )
            return
        chunks, current = [], "📋 Danh sách vận đơn\n"
        for row in orders:
            name = self.service.adapters[row["carrier"]].name
            state = "✅ Đã hoàn thành" if row["completed"] else "🔄 Đang theo dõi"
            line = (
                f"\n📦 {row['tracking_number']} {clean(row['nickname'], 100)}\n{name} · {state}\n"
            )
            line += clean(row["last_status"] or "Chưa có trạng thái", 500) + "\n"
            if row["last_event_time"]:
                line += (
                    "🕒 "
                    + display_time(row["last_event_time"], self.service.settings.timezone)
                    + "\n"
                )
            if row["last_error"]:
                line += "⚠️ " + clean(row["last_error"], 250) + "\n"
            if len(current) + len(line) > 3200:
                chunks.append(current)
                current = ""
            current += line
        chunks.append(current)
        for chunk in chunks:
            await update.effective_message.reply_text(chunk)

    async def delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.authorized(update):
            return
        try:
            number, _, carrier = self.parse_args(context.args, allow_nickname=False)
            if carrier:
                raise ValueError("Dùng /del <mã vận đơn>.")
            deleted = await self.service.delete(update.effective_chat.id, number)
            await update.effective_message.reply_text(
                f"Đã xóa {number}." if deleted else "Không tìm thấy mã trong danh sách của bạn."
            )
        except ValueError as error:
            await update.effective_message.reply_text(str(error))


def build_application(settings: Settings, request: BaseRequest | None = None) -> Application:
    db = Database(settings.database_path)
    http = TrackingHTTP(
        settings.delay_min, settings.delay_max, settings.http_timeout, settings.http_backend
    )
    service = TrackingService(settings, db, build_registry(settings, http))
    handlers = BotHandlers(service)

    async def scan(context: ContextTypes.DEFAULT_TYPE) -> None:
        await service.cycle()
        await service.deliver(context.bot)

    async def deliver(context: ContextTypes.DEFAULT_TYPE) -> None:
        await service.deliver(context.bot)

    async def initialize(application: Application) -> None:
        await application.bot.set_my_commands(
            [
                BotCommand("start", "Hướng dẫn sử dụng"),
                BotCommand("add", "Thêm vận đơn"),
                BotCommand("rename", "Đổi tên đơn đã theo dõi"),
                BotCommand("list", "Danh sách vận đơn"),
                BotCommand("check", "Kiểm tra ngay"),
                BotCommand("history", "Xem toàn bộ hành trình"),
                BotCommand("del", "Xóa vận đơn"),
            ]
        )
        logger.info("Registered Telegram commands: /start /add /rename /list /check /history /del")
        logger.info("Tracking carriers: %s", ", ".join(service.adapters))
        # PTB JobQueue owns an APScheduler AsyncIOScheduler and waits for jobs at shutdown.
        application.job_queue.run_repeating(
            scan,
            interval=settings.poll_interval,
            first=1,
            name="scan-shipments",
            job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 30},
        )
        application.job_queue.run_repeating(
            deliver,
            interval=5,
            first=2,
            name="deliver-outbox",
            job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 10},
        )
        logger.info(
            "Bot started; scan interval=%ds timezone=%s", settings.poll_interval, settings.timezone
        )

    async def shutdown(application: Application) -> None:
        http.close()

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Exception URLs can contain a Telegram token. Log only the exception class.
        logger.error("Unhandled Telegram/job error type=%s", type(context.error).__name__)
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "Có lỗi tạm thời. Kiểm tra /list hoặc thử lại sau."
                )
            except Exception:
                logger.warning("Could not send error reply")

    builder = (
        Application.builder()
        .token(settings.token)
        .get_updates_connect_timeout(30)
        .concurrent_updates(False)
        .post_init(initialize)
        .post_shutdown(shutdown)
    )
    if request is not None:
        builder.request(request)
    else:
        builder.connect_timeout(30).read_timeout(30).write_timeout(30).pool_timeout(30)
    application = builder.build()
    application.bot_data["service"] = service
    for command, callback in [
        ("start", handlers.start),
        ("add", handlers.add),
        ("rename", handlers.rename),
        ("list", handlers.list_orders),
        ("check", handlers.check),
        ("history", handlers.history),
        ("del", handlers.delete),
    ]:
        application.add_handler(CommandHandler(command, callback))
    application.add_handler(
        CallbackQueryHandler(handlers.choose, pattern=r"^carrier:[0-9a-f]{16}:[a-z]+$")
    )
    application.add_error_handler(error_handler)
    return application
