import asyncio
import logging
import sys

from tracker.bot import build_application
from tracker.config import Settings


def main() -> None:
    try:
        settings = Settings.from_env()
    except (ValueError, KeyError) as error:
        print(f"Lỗi cấu hình: {error}", file=sys.stderr)
        raise SystemExit(2) from None
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # HTTPX logs Telegram endpoint URLs, which include bot tokens.
    for name in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    # Python 3.14 no longer creates the default event loop implicitly; PTB run_polling needs it.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    application = build_application(settings)
    application.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=False,
        bootstrap_retries=5,
    )


if __name__ == "__main__":
    main()
