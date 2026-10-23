"""Optional structured events; CLI callers keep their usual behavior."""
from contextlib import contextmanager
from contextvars import ContextVar
import asyncio
import time
import uuid


_sink = ContextVar("translation_event_sink", default=None)


def emit(event_type, **fields):
    sink = _sink.get()
    if sink is not None:
        sink({"type": event_type, "at": time.time(), **fields})


@contextmanager
def event_sink(callback):
    token = _sink.set(callback)
    try:
        yield
    finally:
        _sink.reset(token)


@contextmanager
def request_event(model, part, attempt, timeout=100):
    request_id = uuid.uuid4().hex[:12]
    started = time.monotonic()
    fields = dict(request_id=request_id, model=model, part=str(part), attempt=attempt)
    emit("request_start", **fields, timeout=timeout)
    try:
        yield
    except Exception as exc:
        reason = str(exc).strip() or type(exc).__name__
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timeout" in type(exc).__name__.lower():
            reason = f"请求超过 {timeout} 秒未完成（TimeoutError）"
        emit("request_error", **fields, error=reason, error_type=type(exc).__name__,
             status=getattr(exc, "status", None) or getattr(getattr(exc, "response", None), "status_code", None),
             elapsed=time.monotonic() - started)
        raise
    except BaseException:
        emit("request_cancelled", **fields)
        raise
    else:
        emit("request_success", **fields, elapsed=time.monotonic() - started)
