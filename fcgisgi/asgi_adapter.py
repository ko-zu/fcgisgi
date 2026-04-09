import asyncio
from dataclasses import dataclass, field
from typing import Callable, Dict, Any, List, Optional

from .sansio import (
    FastCGIConnection, RequestStarted, ParamsReceived, StdinReceived,
    EndOfStdin, AbortRequest, Event, FCGI_RESPONDER, FCGI_REQUEST_COMPLETE,
    FCGI_OVERLOADED, FCGI_KEEP_CONN
)


@dataclass
class ASGIRequest:
    id: int
    input_queue: asyncio.Queue
    keep_conn: bool = True
    task: Optional[asyncio.Task] = None
    scope: Optional[Dict[str, Any]] = None
    aborted: bool = False
    response_started: bool = False


class ASGIAdapter:
    def __init__(self, app: Callable, send_func: Callable[[bytes], None], on_close: Callable[[], None], startup_complete: bool = True, force_script_name: Optional[str] = None):
        self.app = app
        self.send_func = send_func
        self.on_close = on_close
        self.fcgi = FastCGIConnection()
        self._requests: Dict[int, ASGIRequest] = {}
        self._startup_complete = startup_complete
        self.force_script_name = force_script_name

    def handle_data(self, data: bytes):
        events = self.fcgi.feed_data(data)
        for event in events:
            self.handle_event(event)

    def handle_event(self, event: Event):
        if isinstance(event, RequestStarted):
            if event.role != FCGI_RESPONDER:
                self.send_func(self.fcgi.send_end_request(
                    event.request_id, 0, 3))
                return

            if not self._startup_complete:
                self.send_func(self.fcgi.send_end_request(
                    event.request_id, 0, FCGI_OVERLOADED))
                return

            self._requests[event.request_id] = ASGIRequest(
                id=event.request_id,
                input_queue=asyncio.Queue(),
                keep_conn=bool(event.flags & FCGI_KEEP_CONN)
            )

        elif isinstance(event, ParamsReceived):
            req = self._requests.get(event.request_id)
            if req:
                req.scope = self._build_scope(
                    event.request_id, event.params)
                req.task = asyncio.create_task(self._run_app(req))

        elif isinstance(event, StdinReceived):
            req = self._requests.get(event.request_id)
            if req:
                req.input_queue.put_nowait({
                    "type": "http.request",
                    "body": event.data,
                    "more_body": True,
                })

        elif isinstance(event, EndOfStdin):
            req = self._requests.get(event.request_id)
            if req:
                req.input_queue.put_nowait({
                    "type": "http.request",
                    "body": b"",
                    "more_body": False,
                })

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
            if req.task:
                req.task.cancel()

    def close_all(self):
        """Abort all active requests in this connection."""
        for request_id in list(self._requests.keys()):
            self._abort_request(request_id)

    def _build_scope(self, request_id: int, params: Dict[bytes, bytes]) -> Dict[str, Any]:
        p = {k.decode('latin-1'): v.decode('latin-1')
             for k, v in params.items()}
        method = p.get("REQUEST_METHOD", "GET")
        path = p.get("PATH_INFO", "")
        query_string = p.get("QUERY_STRING", "").encode('latin-1')
        headers = []
        for k, v in params.items():
            k_str = k.decode('latin-1')
            if k_str.startswith("HTTP_"):
                header_name = k_str[5:].replace(
                    "_", "-").lower().encode('latin-1')
                headers.append((header_name, v))
            elif k_str in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                header_name = k_str.replace("_", "-").lower().encode('latin-1')
                headers.append((header_name, v))

        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": p.get("SERVER_PROTOCOL", "HTTP/1.1").split("/")[-1],
            "method": method,
            "scheme": p.get("wsgi.url_scheme", "http"),
            "path": path,
            "raw_path": p.get("REQUEST_URI", path).split("?")[0].encode('latin-1'),
            "query_string": query_string,
            "root_path": self.force_script_name if self.force_script_name is not None else p.get("SCRIPT_NAME", ""),
            "headers": headers,
            "client": (p.get("REMOTE_ADDR", ""), int(p.get("REMOTE_PORT", 0))),
            "server": (p.get("SERVER_NAME", ""), int(p.get("SERVER_PORT", 0))),
        }

    async def _run_app(self, req: ASGIRequest):
        request_id = req.id

        async def receive():
            return await req.input_queue.get()

        async def send(message):
            if req.aborted:
                return
            try:
                if message["type"] == "http.response.start":
                    req.response_started = True
                    status = message["status"]
                    headers = message.get("headers", [])
                    res = [f"Status: {status}\r\n".encode('latin-1')]
                    for name, value in headers:
                        res.append(name + b": " + value + b"\r\n")
                    res.append(b"\r\n")
                    self.send_func(self.fcgi.send_stdout(
                        request_id, b"".join(res)))
                elif message["type"] == "http.response.body":
                    body = message.get("body", b"")
                    if body:
                        self.send_func(self.fcgi.send_stdout(request_id, body))
                    if not message.get("more_body", False):
                        self.send_func(self.fcgi.send_stdout(
                            request_id, b""))  # EOF
                        self.send_func(self.fcgi.send_end_request(
                            request_id, 0, FCGI_REQUEST_COMPLETE))
            except Exception:
                self._abort_request(request_id)

        try:
            await self.app(req.scope, receive, send)
        except asyncio.CancelledError:
            pass
        except Exception:
            if not req.response_started and not req.aborted:
                try:
                    await send({
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [(b"content-type", b"text/plain")],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": b"Internal Server Error",
                    })
                except Exception:
                    pass
        finally:
            self._requests.pop(request_id, None)
            if not req.keep_conn and not self._requests:
                self.on_close()
