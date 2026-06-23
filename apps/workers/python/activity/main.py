"""Thin entrypoint — Orders activity worker (IO-bound side-effects).

One worker profile per directory. To add a CPU-bound activity worker, register
an "activity-cpu" profile in orders_kernel.worker.WORKER_PROFILES and add a
sibling apps/workers/python/activity-cpu/ that calls run_worker("activity-cpu").
"""

import asyncio

from orders_kernel.worker import run_worker

if __name__ == "__main__":
    asyncio.run(run_worker("activity"))
