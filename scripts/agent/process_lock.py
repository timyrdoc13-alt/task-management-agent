"""Single-instance lock for Telegram bot."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from kaiten_api import STATE_DIR

LOCK_PATH = STATE_DIR / "bot.lock"


class BotLockError(RuntimeError):
    pass


class BotProcessLock:
    def __init__(self) -> None:
        self._fh = None

    def acquire(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._fh = open(LOCK_PATH, "w")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise BotLockError(
                "Another bot instance holds the lock. "
                "Run: pgrep -fl bot.py && launchctl kickstart -k gui/$(id -u)/com.kaiten-agent.bot"
            ) from e
        self._fh.write(str(os.getpid()))
        self._fh.flush()

    def release(self) -> None:
        if self._fh:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self._fh.close()
            self._fh = None
