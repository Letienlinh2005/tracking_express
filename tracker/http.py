import asyncio
import random
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import requests
from curl_cffi import requests as curl_requests
from curl_cffi.requests.exceptions import RequestException as CurlRequestError

from tracker.adapters.base import RateLimited, TrackingError


def retry_seconds(value: str | None) -> float:
    try:
        return max(1, float(value or "60"))
    except ValueError:
        try:
            return max(1, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 60


class TrackingHTTP:
    """One serialized HTTP session for all adapters, including manual commands."""

    def __init__(
        self, delay_min: float, delay_max: float, timeout: float, backend: str = "requests"
    ) -> None:
        self.delay_min, self.delay_max, self.timeout = delay_min, delay_max, timeout
        self.lock = asyncio.Lock()
        self.next_request_at = 0.0
        if backend == "curl_cffi":
            self.session = curl_requests.Session(impersonate="chrome", use_thread_local_curl=False)
        elif backend == "requests":
            self.session = requests.Session()
            self.session.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
            )
        else:
            raise ValueError("Unsupported HTTP backend")
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
            }
        )

    @staticmethod
    def validate_url(url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError("Endpoint phải là URL HTTPS, không chứa tài khoản/mật khẩu.")

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.validate_url(url)
        async with self.lock:
            remaining = getattr(self, "cooldown_until", 0.0) - time.monotonic()
            if remaining > 0:
                raise RateLimited(remaining)
            wait = self.next_request_at - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                response = await asyncio.to_thread(
                    self.session.request,
                    method,
                    url,
                    timeout=(min(10, self.timeout), self.timeout),
                    allow_redirects=False,
                    **kwargs,
                )
            except (requests.RequestException, CurlRequestError):
                raise TrackingError("Không kết nối được API của hãng; bot sẽ thử lại.") from None
            finally:
                self.next_request_at = time.monotonic() + random.uniform(
                    self.delay_min, self.delay_max
                )
            try:
                if response.status_code in (403, 429):
                    delay = retry_seconds(response.headers.get("Retry-After"))
                    # Do not hold the global lock sleeping for a long Retry-After.
                    # The request gate rejects new work until the cooldown expires.
                    self.cooldown_until = time.monotonic() + delay
                    raise RateLimited(delay)
                if response.status_code >= 500:
                    raise TrackingError("API của hãng tạm thời lỗi; bot sẽ thử lại.")
                if response.status_code >= 300:
                    raise TrackingError(f"API của hãng trả HTTP {response.status_code}.")
                try:
                    return response.json()
                except ValueError:
                    raise TrackingError("API trả dữ liệu không phải JSON hợp lệ.") from None
            finally:
                response.close()

    async def json(self, method: str, url: str, **kwargs: Any) -> Any:
        remaining = getattr(self, "cooldown_until", 0.0) - time.monotonic()
        if remaining > 0:
            raise RateLimited(remaining)
        return await self.request(method, url, **kwargs)

    def close(self) -> None:
        self.session.close()
