"""Thin entrypoint — Orders workflow worker.

One worker profile per directory. The fleet definition (task queue, workflows,
activity groups) lives in orders.worker.WORKER_PROFILES.
"""

import asyncio

from orders.worker import run_worker

if __name__ == "__main__":
    asyncio.run(run_worker("workflow"))
