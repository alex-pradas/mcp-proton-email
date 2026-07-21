"""Uniform error collapsing for tools: nothing sensitive leaks (spec 6.9)."""

import functools
from collections.abc import Callable

from fastmcp.exceptions import ToolError

from .sanitize import collapse_error
from .secrets import PassError, PassSessionError


def tool_guard[**P, T](fn: Callable[P, T]) -> Callable[P, T]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return fn(*args, **kwargs)
        except (ToolError, PassSessionError, PassError):
            raise
        except Exception as exc:
            collapsed = collapse_error(exc)
            raise ToolError(f"{fn.__name__} failed: {collapsed['name']}: {collapsed['message']}") from None

    return wrapper


def tool_guard_async[**P, T](fn: Callable[P, T]) -> Callable[P, T]:
    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs):
        try:
            return await fn(*args, **kwargs)
        except (ToolError, PassSessionError, PassError):
            raise
        except Exception as exc:
            collapsed = collapse_error(exc)
            raise ToolError(f"{fn.__name__} failed: {collapsed['name']}: {collapsed['message']}") from None

    return wrapper
