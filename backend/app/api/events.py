import asyncio
import json
import time

from starlette.concurrency import run_in_threadpool

from .schemas import TERMINAL_STATES


async def event_stream(service, request, run_id: str, cursor: int, follow: bool):
    """SSE over committed events, with resumable sequence IDs and disconnect cleanup."""
    backend = service.require_backend()
    heartbeat_at = time.monotonic() + service.settings.sse_heartbeat_seconds
    try:
        yield ": connected\nretry: 1000\n\n"
        while not await request.is_disconnected():
            # Read status first: a terminal snapshot must include its final persisted events.
            run = await run_in_threadpool(backend.get_run, run_id)
            batch = await run_in_threadpool(backend.list_events, run_id, after=cursor, limit=100)
            for event in batch:
                if str(event.run_id) != run_id or event.sequence <= cursor:
                    raise ValueError("Invalid event cursor or run ownership")
                cursor = event.sequence
                yield f"id: {cursor}\nevent: {event.kind}\ndata: {event.model_dump_json()}\n\n"
            if len(batch) == 100:
                continue
            if (
                not follow
                or run.status in TERMINAL_STATES
                or (run.result and run.result.status == "needs_user_action")
            ):
                break
            if time.monotonic() >= heartbeat_at:
                yield ": heartbeat\n\n"
                heartbeat_at = time.monotonic() + service.settings.sse_heartbeat_seconds
            await asyncio.sleep(service.settings.sse_poll_seconds)
    except asyncio.CancelledError:
        raise
    except Exception:
        # HTTP headers have already been sent; terminate with a safe SSE error.
        error = {"code": "EVENT_STREAM_FAILED", "message": "Reconnect to resume persisted events."}
        yield f"event: error\ndata: {json.dumps(error)}\n\n"
    finally:
        service.streams -= 1
