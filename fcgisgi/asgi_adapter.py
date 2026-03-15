import asyncio
from typing import Callable, Dict, Any, List, Optional
from .sansio import (
    FastCGIConnection, RequestStarted, ParamsReceived, StdinReceived, 
    EndOfStdin, AbortRequest, Event, FCGI_RESPONDER, FCGI_REQUEST_COMPLETE,
    FCGI_OVERLOADED
)

class ASGIAdapter:
    def __init__(self, app: Callable, send_func: Optional[Callable[[bytes], None]] = None):
        self.app = app
        self._default_send_func = send_func
        self.fcgi = FastCGIConnection()
        self._requests: Dict[int, Dict[str, Any]] = {}
        
        # Lifespan state
        self._lifespan_queue: Optional[asyncio.Queue] = None
        self._lifespan_task: Optional[asyncio.Task] = None
        self._startup_event = asyncio.Event()
        self._shutdown_event = asyncio.Event()
        self._startup_complete = False
        self._shutdown_started = False

    async def startup(self, timeout: Optional[float] = None):
        self._lifespan_queue = asyncio.Queue()
        self._lifespan_task = asyncio.create_task(self._run_lifespan())
        await self._lifespan_queue.put({"type": "lifespan.startup"})
        try:
            if timeout is not None:
                await asyncio.wait_for(self._startup_event.wait(), timeout=timeout)
            else:
                await self._startup_event.wait()
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # If startup fails/times out, we should probably stop the lifespan task
            pass
        self._startup_complete = True

    async def shutdown(self, timeout: Optional[float] = None):
        self._shutdown_started = True
        if self._lifespan_queue:
            try:
                await self._lifespan_queue.put({"type": "lifespan.shutdown"})
                if timeout is not None:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=timeout)
                else:
                    await self._shutdown_event.wait()
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        
        if self._lifespan_task:
            self._lifespan_task.cancel()
            try:
                await self._lifespan_task
            except (asyncio.CancelledError, Exception):
                pass
            self._lifespan_task = None

    async def _run_lifespan(self):
        scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}}
        async def receive():
            try:
                return await self._lifespan_queue.get()
            except asyncio.CancelledError:
                # Return a dummy shutdown message or re-raise
                return {"type": "lifespan.shutdown"}
        async def send(message):
            if message["type"] == "lifespan.startup.complete":
                self._startup_event.set()
            elif message["type"] == "lifespan.shutdown.complete":
                self._shutdown_event.set()
            elif message["type"] in ("lifespan.startup.failed", "lifespan.shutdown.failed"):
                self._startup_event.set()
                self._shutdown_event.set()
        try:
            await self.app(scope, receive, send)
        except Exception:
            pass
        finally:
            self._startup_event.set()
            self._shutdown_event.set()

    def handle_data(self, data: bytes, send_func: Optional[Callable[[bytes], None]] = None):
        events = self.fcgi.feed_data(data)
        for event in events:
            self.handle_event(event, send_func or self._default_send_func)

    def handle_event(self, event: Event, send_func: Callable[[bytes], None]):
        if isinstance(event, RequestStarted):
            if event.role != FCGI_RESPONDER:
                send_func(self.fcgi.send_end_request(event.request_id, 0, 3))
                return
            
            # If shutdown started, reject new requests
            # If startup not complete, we could either buffer or reject. 
            # Rejecting with OVERLOADED is common for simple implementations.
            if self._shutdown_started or not self._startup_complete:
                send_func(self.fcgi.send_end_request(event.request_id, 0, FCGI_OVERLOADED))
                return

            self._requests[event.request_id] = {
                "id": event.request_id,
                "input_queue": asyncio.Queue(),
                "task": None,
                "scope": None,
                "aborted": False,
                "send_func": send_func,
            }
        
        elif isinstance(event, ParamsReceived):
            req = self._requests.get(event.request_id)
            if req:
                req["scope"] = self._build_scope(event.request_id, event.params)
                req["task"] = asyncio.create_task(self._run_app(req))
        
        elif isinstance(event, StdinReceived):
            req = self._requests.get(event.request_id)
            if req:
                req["input_queue"].put_nowait({
                    "type": "http.request",
                    "body": event.data,
                    "more_body": True,
                })
        
        elif isinstance(event, EndOfStdin):
            req = self._requests.get(event.request_id)
            if req:
                req["input_queue"].put_nowait({
                    "type": "http.request",
                    "body": b"",
                    "more_body": False,
                })
        
        elif isinstance(event, AbortRequest):
            self._abort_request(event.request_id)

    def _abort_request(self, request_id: int):
        req = self._requests.get(request_id)
        if req and not req["aborted"]:
            req["aborted"] = True
            req["input_queue"].put_nowait({"type": "http.disconnect"})
            if req["task"]:
                req["task"].cancel()

    def close_connection(self, send_func: Callable[[bytes], None]):
        """Abort all active requests for a specific connection."""
        for request_id in list(self._requests.keys()):
            if self._requests[request_id]["send_func"] == send_func:
                self._abort_request(request_id)

    def _build_scope(self, request_id: int, params: Dict[bytes, bytes]) -> Dict[str, Any]:
        p = {k.decode('latin-1'): v.decode('latin-1') for k, v in params.items()}
        method = p.get("REQUEST_METHOD", "GET")
        path = p.get("PATH_INFO", "")
        query_string = p.get("QUERY_STRING", "").encode('latin-1')
        headers = []
        for k, v in params.items():
            k_str = k.decode('latin-1')
            if k_str.startswith("HTTP_"):
                header_name = k_str[5:].replace("_", "-").lower().encode('latin-1')
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
            "root_path": p.get("SCRIPT_NAME", ""),
            "headers": headers,
            "client": (p.get("REMOTE_ADDR", ""), int(p.get("REMOTE_PORT", 0))),
            "server": (p.get("SERVER_NAME", ""), int(p.get("SERVER_PORT", 0))),
        }

    async def _run_app(self, req: Dict[str, Any]):
        request_id = req["id"]
        send_func = req["send_func"]
        
        async def receive():
            return await req["input_queue"].get()

        async def send(message):
            if req["aborted"]:
                return
            try:
                if message["type"] == "http.response.start":
                    req["response_started"] = True
                    status = message["status"]
                    headers = message.get("headers", [])
                    res = [f"Status: {status}\r\n".encode('latin-1')]
                    for name, value in headers:
                        res.append(name + b": " + value + b"\r\n")
                    res.append(b"\r\n")
                    send_func(self.fcgi.send_stdout(request_id, b"".join(res)))
                elif message["type"] == "http.response.body":
                    body = message.get("body", b"")
                    if body:
                        send_func(self.fcgi.send_stdout(request_id, body))
                    if not message.get("more_body", False):
                        send_func(self.fcgi.send_stdout(request_id, b"")) # EOF
                        send_func(self.fcgi.send_end_request(request_id, 0, FCGI_REQUEST_COMPLETE))
            except Exception:
                self._abort_request(request_id)

        try:
            await self.app(req["scope"], receive, send)
        except asyncio.CancelledError:
            pass
        except Exception:
            if not req.get("response_started", False) and not req["aborted"]:
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
