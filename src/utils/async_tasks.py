import asyncio
from contextlib import asynccontextmanager


@asynccontextmanager
async def managed_tasks(coroutines):
    """Own concurrent tasks until all results or exceptions are retrieved."""
    tasks = []
    try:
        for coroutine in coroutines:
            tasks.append(asyncio.create_task(coroutine))
        yield tasks
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
