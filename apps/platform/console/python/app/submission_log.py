import asyncio
from collections import deque

from .config import settings


class SubmissionLog:
    def __init__(self, maxlen: int):
        self._log = deque(maxlen=maxlen)
        self._lock = asyncio.Lock()

    async def append_batch(self, entry: dict):
        async with self._lock:
            self._log.appendleft(entry)

    async def get_all(self) -> list[dict]:
        async with self._lock:
            return list(self._log)

    async def clear(self):
        async with self._lock:
            self._log.clear()


submission_log = SubmissionLog(settings.log_buffer_size)
