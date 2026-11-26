from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tracker.config import Settings
from tracker.database import Database


@pytest.fixture
def tz():
    return ZoneInfo("Asia/Ho_Chi_Minh")


@pytest.fixture
def settings(tmp_path, tz):
    return Settings(
        token="123456789:AA_test_token_for_offline_tests_only",
        database_path=tmp_path / "tracking.sqlite3",
        timezone=tz,
        poll_interval=60,
        delay_min=1,
        delay_max=3,
        http_timeout=20,
        max_orders=50,
        allowed_users=frozenset(),
        spx_url="https://spx.vn/shipment/order/open/order/get_order_info",
        carrier_config=Path("config/carriers.example.json"),
        log_level="INFO",
    )


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.sqlite3")
