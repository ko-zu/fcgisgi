import asyncio
from typing import Callable, Dict, Any, List, Optional
from .sansio import (
    FastCGIConnection, RequestStarted, ParamsReceived, StdinReceived, 
    EndOfStdin, AbortRequest, Event, FCGI_RESPONDER, FCGI_REQUEST_COMPLETE
)

class ASGIAdapter:
    def __init__(self, app: Callable, send_func: Callable[[bytes], None]):
        self.app = app
        self.send_func = send_func
        self.fcgi = FastCGIConnection()
        self._requests: Dict[int, Dict[str, Any]] = {}

    def handle_data(self, data: bytes):
        events = self.fcgi.feed_data(data)
        for event in events:
            self.handle_event(event)

    def handle_event(self, event: Event):
        if isinstance(event, RequestStarted):
            if event.role != FCGI_RESPONDER:
                self.send_func(self.fcgi.send_end_request(event.request_id, 0, 3))
                return
            
            self._requests[event.request_id] = {
                "id": event.request_id,
                "input_queue": asyncio.Queue(),
                "task": None,
                "scope": None,
                "aborted": False,
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
            # Send disconnect to the application
            req["input_queue"].put_nowait({"type": "http.disconnect"})
            # Also cancel the task to ensure it stops if it's waiting on something else
            if req["task"]:
                req["task"].cancel()

    def close_all(self):
        """Abort all active requests. Call this when connection is lost."""
        # Use a list of keys to avoid modification during iteration issues
        for request_id in list(self._requests.keys()):
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
                    self.send_func(self.fcgi.send_stdout(request_id, b"".join(res)))
                
                elif message["type"] == "http.response.body":
                    body = message.get("body", b"")
                    if body:
                        self.send_func(self.fcgi.send_stdout(request_id, body))
                    if not message.get("more_body", False):
                        self.send_func(self.fcgi.send_stdout(request_id, b"")) # EOF
                        self.send_func(self.fcgi.send_end_request(request_id, 0, FCGI_REQUEST_COMPLETE))
            except Exception:
                # Connection might be lost during send
                self._abort_request(request_id)

        try:
            await self.app(req["scope"], receive, send)
        except asyncio.CancelledError:
            # Task was cancelled due to abort/disconnect
            pass
        except Exception:
            # Handle other errors - send 500 if not started
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
