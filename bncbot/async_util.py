"""
Utility functions for working with asyncio
"""

import asyncio
from functools import partial


def is_coro(func) -> bool:
    return asyncio.iscoroutine(func) or asyncio.iscoroutinefunction(func)


async def call_func(func, *args, **kwargs):
    part = partial(func, *args, **kwargs)
    if is_coro(func):
        return await part()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, part)
