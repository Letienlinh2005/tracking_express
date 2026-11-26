"""Hidden Windows entry point with rotating, token-redacted logs."""

import argparse
import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        result = super().format(record)
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        return result.replace(token, "[REDACTED]") if token else result


def wait_for_previous_bot(pid: int, created: int) -> None:
    """Wait using a read-only process handle; creation time prevents PID reuse confusion."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x101000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # PID no longer exists.
            return
        raise ctypes.WinError(error)
    try:
        timestamps = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in timestamps]):
            raise ctypes.WinError(ctypes.get_last_error())
        actual_created = (timestamps[0].dwHighDateTime << 32) | timestamps[0].dwLowDateTime
        if actual_created != created:
            return
        logging.info("Waiting for previous bot PID %d to exit before starting the hidden bot", pid)
        while True:
            status = kernel.WaitForSingleObject(handle, 1000)
            if status == 0:
                logging.info("Previous bot exited; starting the updated hidden bot")
                return
            if status != 258:  # WAIT_TIMEOUT
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def run(wait_pid: int = 0, wait_created: int = 0) -> None:
    log_directory = ROOT / "data" / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_directory / "bot.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(SafeFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), handlers=[handler], force=True)
    from telegram.error import NetworkError

    from tracker.__main__ import main

    try:
        if wait_pid:
            if not wait_created:
                raise ValueError("A process creation time is required when waiting for a PID")
            wait_for_previous_bot(wait_pid, wait_created)
        while True:
            try:
                main()
                break
            except NetworkError as error:
                logging.warning(
                    "Telegram startup network failure (%s); retrying in 30 seconds",
                    type(error).__name__,
                )
                time.sleep(30)
    except SystemExit as error:
        if error.code:
            logging.error("Bot stopped with configuration/startup exit code %s", error.code)
        raise
    except Exception:
        logging.exception("Bot stopped unexpectedly; Task Scheduler will restart it")
        raise SystemExit(1) from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-for-pid", type=int, default=0)
    parser.add_argument("--wait-for-created", type=int, default=0)
    arguments = parser.parse_args()
    run(arguments.wait_for_pid, arguments.wait_for_created)
