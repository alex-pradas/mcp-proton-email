"""Uniform error collapsing for tools: nothing sensitive leaks (spec 6.9)."""

import functools
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from fastmcp.exceptions import ToolError

from .sanitize import collapse_error
from .secrets import PassError, PassSessionError

_P = ParamSpec("_P")
_R = TypeVar("_R")


def tool_guard(fn: Callable[_P, _R]) -> Callable[_P, _R]:
    @functools.wraps(fn)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return fn(*args, **kwargs)
        except (ToolError, PassSessionError, PassError):
            raise
        except Exception as exc:
            collapsed = collapse_error(exc)
            raise ToolError(f"{fn.__name__} failed: {collapsed['name']}: {collapsed['message']}") from None

    return wrapper


def tool_guard_async(fn: Callable[_P, _R]) -> Callable[_P, _R]:
    @functools.wraps(fn)
    async def wrapper(*args: _P.args, **kwargs: _P.kwargs):
        try:
            return await fn(*args, **kwargs)
        except (ToolError, PassSessionError, PassError):
            raise
        except Exception as exc:
            collapsed = collapse_error(exc)
            raise ToolError(f"{fn.__name__} failed: {collapsed['name']}: {collapsed['message']}") from None

    return wrapper
