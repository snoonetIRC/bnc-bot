"""
Utility functions for working with asyncio
"""

import asyncio
from datetime import timedelta
from functools import partial


def is_coro(func) -> bool:
    return asyncio.iscoroutine(func) or asyncio.iscoroutinefunction(func)


async def call_func(func, *args, **kwargs):
    part = partial(func, *args, **kwargs)
    if is_coro(func):
        return await part()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, part)


async def timer(interval, func, *args, initial_interval=None):
    if initial_interval is None:
        initial_interval = interval

    if isinstance(interval, timedelta):
        interval = interval.total_seconds()

    if isinstance(initial_interval, timedelta):
        initial_interval = initial_interval.total_seconds()

    await asyncio.sleep(initial_interval)
    while True:
        await call_func(func, *args)
        await asyncio.sleep(interval)
