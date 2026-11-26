import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    token: str
    database_path: Path
    timezone: ZoneInfo
    poll_interval: int
    delay_min: float
    delay_max: float
    http_timeout: float
    max_orders: int
    allowed_users: frozenset[int]
    spx_url: str
    carrier_config: Path
    log_level: str
    http_backend: str = "curl_cffi"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token or token == "replace_with_token_from_BotFather" or ":" not in token:
            raise ValueError("Điền TELEGRAM_BOT_TOKEN hợp lệ trong .env trước khi chạy.")
        settings = cls(
            token=token,
            database_path=Path(os.getenv("DATABASE_PATH", "data/tracking.sqlite3")),
            timezone=ZoneInfo(os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh")),
            poll_interval=int(os.getenv("POLL_INTERVAL_SECONDS", "60")),
            delay_min=float(os.getenv("REQUEST_DELAY_MIN", "1")),
            delay_max=float(os.getenv("REQUEST_DELAY_MAX", "3")),
            http_timeout=float(os.getenv("HTTP_TIMEOUT_SECONDS", "20")),
            max_orders=int(os.getenv("MAX_ORDERS_PER_CHAT", "50")),
            allowed_users=frozenset(
                int(value.strip())
                for value in os.getenv("ALLOWED_USER_IDS", "").split(",")
                if value.strip()
            ),
            spx_url=os.getenv(
                "SPX_API_URL", "https://spx.vn/shipment/order/open/order/get_order_info"
            ),
            carrier_config=Path(os.getenv("CARRIER_CONFIG_PATH", "config/carriers.json")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            http_backend=os.getenv("HTTP_BACKEND", "curl_cffi").strip(),
        )
        if settings.poll_interval < 60:
            raise ValueError("POLL_INTERVAL_SECONDS phải >= 60.")
        if settings.http_backend not in {"requests", "curl_cffi"}:
            raise ValueError("HTTP_BACKEND phải là requests hoặc curl_cffi.")
        if not 1 <= settings.delay_min <= settings.delay_max <= 60:
            raise ValueError("Delay phải thỏa 1 <= REQUEST_DELAY_MIN <= REQUEST_DELAY_MAX <= 60.")
        if not 1 <= settings.http_timeout <= 120 or settings.max_orders < 1:
            raise ValueError("HTTP_TIMEOUT_SECONDS cần thuộc [1,120]; MAX_ORDERS_PER_CHAT >= 1.")
        return settings
