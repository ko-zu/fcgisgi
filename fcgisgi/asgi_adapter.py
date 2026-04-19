import asyncio
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from .sansio import (
    FastCGIConnection,
    RequestStarted,
    ParamsReceived,
    StdinReceived,
    EndOfStdin,
    AbortRequest,
    Event,
    GetValues,
    FCGI_RESPONDER,
    FCGI_REQUEST_COMPLETE,
    FCGI_OVERLOADED,
    FCGI_KEEP_CONN,
)


class DisconnectedError(OSError):
    """Exception raised when trying to send data over a closed connection."""

    pass


@dataclass
class ASGIRequest:
    id: int
    input_queue: asyncio.Queue
    task: asyncio.Task | None = None
    cancel_task: asyncio.Task | None = None
    scope: dict[str, Any] | None = None
    aborted: bool = False
    response_started: bool = False
    response_complete: bool = False


class ASGIAdapter:
    def __init__(
        self,
        app: Callable,
        send_func: Callable[[bytes], None],
        on_close: Callable[[], None],
        startup_complete: bool = True,
        force_script_name: str | None = None,
        lifespan_state: dict[str, Any] | None = None,
        shutdown_timeout: float = 55.0,
    ):
        self.app = app
        self.send_func = send_func
        self.on_close = on_close
        self.fcgi = FastCGIConnection()
        self._requests: dict[int, ASGIRequest] = {}
        self._keep_conn = True
        self._startup_complete = startup_complete
        self.force_script_name = force_script_name
        self.lifespan_state = lifespan_state or {}
        self.shutdown_timeout = shutdown_timeout
        self._cancel_tasks = set()

    def handle_data(self, data: bytes):
        events = self.fcgi.feed_data(data)
        for event in events:
            self.handle_event(event)

    def handle_event(self, event: Event):
        if isinstance(event, RequestStarted):
            if event.role != FCGI_RESPONDER:
                self.send_func(self.fcgi.send_end_request(event.request_id, 0, 3))
                return

            if not self._startup_complete:
                self.send_func(self.fcgi.send_end_request(event.request_id, 0, FCGI_OVERLOADED))
                return

            keep_conn = bool(event.flags & FCGI_KEEP_CONN)
            if not keep_conn:
                self._keep_conn = False

            self._requests[event.request_id] = ASGIRequest(id=event.request_id, input_queue=asyncio.Queue())

        elif isinstance(event, ParamsReceived):
            req = self._requests.get(event.request_id)
            if req:
                req.scope = self._build_scope(event.request_id, event.params)
                req.task = asyncio.create_task(self._run_app(req))

        elif isinstance(event, StdinReceived):
            req = self._requests.get(event.request_id)
            if req:
                req.input_queue.put_nowait(
                    {
                        "type": "http.request",
                        "body": event.data,
                        "more_body": True,
                    }
                )

        elif isinstance(event, EndOfStdin):
            req = self._requests.get(event.request_id)
            if req:
                req.input_queue.put_nowait(
                    {
                        "type": "http.request",
                        "body": b"",
                        "more_body": False,
                    }
                )

        elif isinstance(event, AbortRequest):
            self._abort_request(event.request_id)

        elif isinstance(event, GetValues):
            from .sansio import FCGI_MAX_CONNS, FCGI_MAX_REQS, FCGI_MPXS_CONNS

            values = {
                FCGI_MAX_CONNS: b"100",
                FCGI_MAX_REQS: b"100",
                FCGI_MPXS_CONNS: b"1",
            }
            self.send_func(self.fcgi.send_get_values_result(values))

    def _abort_request(self, request_id: int):
        req = self._requests.get(request_id)
        if req and not req.aborted:
            req.aborted = True
            req.input_queue.put_nowait({"type": "http.disconnect"})
            if req.task and not req.task.done():
                cancel_task = asyncio.create_task(self._delayed_cancel(req.task))
                req.cancel_task = cancel_task
                self._cancel_tasks.add(cancel_task)
                cancel_task.add_done_callback(self._cancel_tasks.discard)

    async def _delayed_cancel(self, task: asyncio.Task):
        try:
            await asyncio.sleep(self.shutdown_timeout)
        finally:
            if not task.done():
                task.cancel()

    async def wait_all(self):
        """Wait for all active request tasks to complete."""
        tasks = []
        for req in list(self._requests.values()):
            if req.task and not req.task.done():
                tasks.append(req.task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def close_all(self):
        """Abort all active requests in this connection."""
        for request_id in list(self._requests.keys()):
            self._abort_request(request_id)

    def _build_scope(self, request_id: int, params: list[tuple[bytes, bytes]]) -> dict[str, Any]:
        # Use reversed() before dict() to ensure that if duplicate keys exist for metadata
        # (like REQUEST_METHOD), the FIRST occurrence in the original params list is prioritized.
        p = dict(reversed(params))

        # According to CGI spec:
        # - SCRIPT_NAME and PATH_INFO are expected to be percent-decoded (usually UTF-8).
        # - QUERY_STRING and REQUEST_URI are usually raw percent-encoded ASCII/latin-1.
        # ASGI expects 'path' to be a decoded string and 'raw_path' to be the original bytes.
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "method": p.get(b"REQUEST_METHOD", b"GET").decode("latin-1"),
            "query_string": p.get(b"QUERY_STRING", b""),
            "scheme": p.get(b"HTTPS") in (b"on", b"1") and "https" or "http",
            "state": self.lifespan_state.copy(),
            "extensions": {
                "fcgisgi": {"fcgi_params": list(params)},
            },
        }

        if self.force_script_name is not None:
            scope["root_path"] = self.force_script_name
        else:
            scope["root_path"] = p.get(b"SCRIPT_NAME", b"").decode("utf-8", "surrogateescape")

        scope["path"] = scope["root_path"] + p.get(b"PATH_INFO", b"").decode("utf-8", "surrogateescape")

        if b"REQUEST_URI" in p:
            scope["raw_path"] = p[b"REQUEST_URI"].split(b"?", 1)[0]

        # Normalize HTTP version string to follow ASGI spec (e.g., "1.1", "2", "3")
        http_version = p.get(b"SERVER_PROTOCOL", b"HTTP/1.1").decode("latin-1").split("/")[-1]
        if http_version in ("2.0", "3.0"):
            http_version = http_version[0]
        elif http_version not in ("1.0", "1.1", "2", "3"):
            http_version = "1.1"
        scope["http_version"] = http_version

        if b"REMOTE_ADDR" in p:
            scope["client"] = (
                p[b"REMOTE_ADDR"].decode("utf-8", "surrogateescape"),
                int(p[b"REMOTE_PORT"]) if b"REMOTE_PORT" in p else 0,
            )
        if b"SERVER_ADDR" in p:
            scope["server"] = (
                p[b"SERVER_ADDR"].decode("utf-8", "surrogateescape"),
                int(p[b"SERVER_PORT"]) if b"SERVER_PORT" in p else None,
            )

        # Process headers: Preserve all duplicate headers as per ASGI spec.
        # Headers are converted from HTTP_VAR_NAME to header-name format.
        headers = []
        for k, v in params:
            if k.startswith(b"HTTP_"):
                header_name = k[5:].replace(b"_", b"-").lower()
                headers.append((header_name, v))
            elif k in (b"CONTENT_TYPE", b"CONTENT_LENGTH"):
                header_name = k.replace(b"_", b"-").lower()
                headers.append((header_name, v))
        scope["headers"] = headers
        return scope

    async def _run_app(self, req: ASGIRequest):
        request_id = req.id

        async def receive():
            return await req.input_queue.get()

        async def send(message):
            if req.aborted or req.response_complete:
                raise DisconnectedError("Connection closed or response already completed")
            unsupported_type = None
            try:
                if message["type"] == "http.response.start":
                    req.response_started = True
                    status = message["status"]
                    headers = message.get("headers", [])
                    res = [f"Status: {status}\r\n".encode("latin-1")]
                    for name, value in headers:
                        res.append(name + b": " + value + b"\r\n")
                    res.append(b"\r\n")
                    self.send_func(self.fcgi.send_stdout(request_id, b"".join(res)))
                elif message["type"] == "http.response.body":
                    body = message.get("body", b"")
                    if body:
                        self.send_func(self.fcgi.send_stdout(request_id, body))
                    if not message.get("more_body", False):
                        req.response_complete = True
                        self.send_func(self.fcgi.send_stdout(request_id, b""))  # EOF
                        self.send_func(self.fcgi.send_end_request(request_id, 0, FCGI_REQUEST_COMPLETE))
                else:
                    unsupported_type = message["type"]
            except Exception:
                self._abort_request(request_id)
                raise DisconnectedError("Connection lost during send")

            if unsupported_type:
                raise ValueError(f"Unsupported ASGI message type: {unsupported_type}")

        try:
            await self.app(req.scope, receive, send)
        except asyncio.CancelledError:
            pass
        except Exception:
            if not req.response_started and not req.aborted:
                try:
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 500,
                            "headers": [(b"content-type", b"text/plain")],
                        }
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b"Internal Server Error",
                        }
                    )
                except Exception:
                    pass
        finally:
            if req.cancel_task and not req.cancel_task.done():
                req.cancel_task.cancel()
            self._requests.pop(request_id, None)
            if not self._keep_conn and not self._requests:
                self.on_close()
