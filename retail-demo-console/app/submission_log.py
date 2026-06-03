from collections import deque
import asyncio
from typing import List, Dict

from .config import settings


class SubmissionLog:
    def __init__(self, maxlen: int):
        self._log = deque(maxlen=maxlen)
        self._lock = asyncio.Lock()

    async def append_batch(self, entry: Dict):
        async with self._lock:
            self._log.appendleft(entry)

    async def get_all(self) -> List[Dict]:
        async with self._lock:
            return list(self._log)


submission_log = SubmissionLog(settings.log_buffer_size)
