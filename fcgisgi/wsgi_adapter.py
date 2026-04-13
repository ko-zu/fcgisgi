import io
import sys
import threading
import queue
import socket
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, Any, List, Optional, Tuple
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
    FCGI_MAX_CONNS,
    FCGI_MAX_REQS,
    FCGI_MPXS_CONNS,
    FCGI_KEEP_CONN,
)

logger = logging.getLogger(__name__)


class WSGIAbortError(Exception):
    """Exception raised to interrupt the WSGI application when a request is aborted."""

    pass


class WSGIInput(io.RawIOBase):
    def __init__(self):
        self._queue = queue.Queue()
        self._eof = False
        self._buffer = b""
        self._aborted = False

    def readinto(self, b):
        if self._aborted:
            raise WSGIAbortError("Request aborted")

        if not self._buffer and not self._eof:
            data = self._queue.get()
            if data is None:
                self._eof = True
            elif data == "ABORT":
                self._aborted = True
                raise WSGIAbortError("Request aborted")
            else:
                self._buffer += data

        if not self._buffer:
            return 0

        n = min(len(b), len(self._buffer))
        b[:n] = self._buffer[:n]
        self._buffer = self._buffer[n:]
        return n

    def readable(self):
        return True

    def put(self, data: bytes):
        self._queue.put(data)

    def put_eof(self):
        self._queue.put(None)

    def abort(self):
        self._aborted = True
        self._queue.put("ABORT")


class WSGIErrors:
    def __init__(self, adapter: "WSGIAdapter", request_id: int):
        self.adapter = adapter
        self.request_id = request_id

    def write(self, data: str):
        if not isinstance(data, str):
            raise TypeError("write() argument must be str")
        encoded = data.encode("utf-8", "replace")
        self.adapter.send_func(self.adapter.fcgi.send_stderr(self.request_id, encoded))

    def writelines(self, lines: List[str]):
        for line in lines:
            self.write(line)

    def flush(self):
        pass


@dataclass
class WSGIRequest:
    id: int
    stdin: "WSGIInput"
    params: Dict[str, str] = field(default_factory=dict)
    params_list: List[Tuple[bytes, bytes]] = field(default_factory=list)
    thread: Optional[threading.Thread] = None
    headers_set: Optional[Tuple[str, List[Tuple[str, str]]]] = None
    response_started: bool = False
    aborted: bool = False


class WSGIAdapter:
    def __init__(
        self,
        application: Callable,
        send_func: Callable[[bytes], None],
        spawn_func: Callable,
        call_soon_func: Callable,
        on_close: Callable[[], None],
        force_script_name: Optional[str] = None,
    ):
        self.application = application
        self.send_func = send_func
        self.on_close = on_close
        self.spawn_func = spawn_func
        self.call_soon_func = call_soon_func
        self.fcgi = FastCGIConnection()
        self._requests: Dict[int, WSGIRequest] = {}
        self._keep_conn = True
        self.force_script_name = force_script_name

    def handle_data(self, data: bytes, send_func: Any = None):
        events = self.fcgi.feed_data(data)
        for event in events:
            self.handle_event(event)

    def handle_event(self, event: Event):
        if isinstance(event, RequestStarted):
            if event.role != FCGI_RESPONDER:
                self.send_func(self.fcgi.send_end_request(event.request_id, 0, 3))
                return

            keep_conn = bool(event.flags & FCGI_KEEP_CONN)
            if not keep_conn:
                self._keep_conn = False

            self._requests[event.request_id] = WSGIRequest(id=event.request_id, stdin=WSGIInput())

        elif isinstance(event, ParamsReceived):
            req = self._requests.get(event.request_id)
            if req:
                # Merge headers
                # Discard: CGI variables
                # Semicolon-join: cookie
                # Comma-join: everything else

                environ = {}

                for k_bytes, v_bytes in event.params:
                    k = k_bytes.decode("latin-1")
                    v = v_bytes.decode("latin-1")

                    if k.startswith("HTTP_"):
                        header_key = k[5:]
                        if header_key == "COOKIE":
                            if k in environ:
                                environ[k] += "; " + v
                            else:
                                environ[k] = v
                        else:
                            if k in environ:
                                environ[k] += ", " + v
                            else:
                                environ[k] = v
                    else:
                        # Non-header params (REQUEST_METHOD, etc.)
                        if k not in environ:
                            environ[k] = v

                req.params = environ
                req.params_list = list(event.params)
                req.thread = self.spawn_func(self._run_app, (req,))

        elif isinstance(event, StdinReceived):
            req = self._requests.get(event.request_id)
            if req:
                self.spawn_func(req.stdin.put, (event.data,))

        elif isinstance(event, EndOfStdin):
            req = self._requests.get(event.request_id)
            if req:
                self.spawn_func(req.stdin.put_eof, ())

        elif isinstance(event, AbortRequest):
            self._abort_request(event.request_id)

        elif isinstance(event, GetValues):
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
            self.spawn_func(req.stdin.abort, ())

    def close_all(self):
        for rid in list(self._requests.keys()):
            self._abort_request(rid)

    def close_connection(self, send_func: Any = None):
        self.close_all()

    def _run_app(self, req: WSGIRequest):
        request_id = req.id
        environ = req.params

        environ["wsgi.version"] = (1, 0)
        environ["wsgi.url_scheme"] = environ.get("HTTPS", "off") in ("on", "1") and "https" or "http"
        environ["wsgi.input"] = io.BufferedReader(req.stdin)
        environ["wsgi.errors"] = WSGIErrors(self, request_id)
        environ["wsgi.multithread"] = True
        environ["wsgi.multiprocess"] = True
        environ["wsgi.run_once"] = False
        environ["fcgisgi.fcgi_params"] = req.params_list

        if self.force_script_name is not None:
            environ["SCRIPT_NAME"] = self.force_script_name
        elif "SCRIPT_NAME" not in environ:
            environ["SCRIPT_NAME"] = ""
        if "PATH_INFO" not in environ:
            environ["PATH_INFO"] = ""

        def start_response(status, response_headers, exc_info=None):
            if exc_info:
                if req.response_started:
                    raise exc_info[1].with_traceback(exc_info[2])
            req.headers_set = (status, response_headers)
            return lambda data: self._write(req, data)

        result = None
        try:
            result = self.application(environ, start_response)
            for data in result:
                if data:
                    self._write(req, data)
                if req.aborted:
                    break
            if not req.response_started and not req.aborted:
                self._write(req, b"")
        except WSGIAbortError:
            pass
        except Exception as e:
            logger.exception("Exception in WSGI application")
            if not req.response_started and not req.aborted:
                try:
                    start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
                    self._write(req, b"Internal Server Error")
                except Exception:
                    pass
        finally:
            if hasattr(result, "close"):
                try:
                    result.close()
                except Exception:
                    logger.exception("Exception calling result.close()")

            if not req.aborted:
                try:
                    self.send_func(self.fcgi.send_stdout(request_id, b""))
                    self.send_func(self.fcgi.send_end_request(request_id, 0, FCGI_REQUEST_COMPLETE))
                except Exception:
                    pass

            self.call_soon_func(self._check_close, request_id)

    def _check_close(self, request_id: int):
        self._requests.pop(request_id, None)
        if not self._keep_conn and not self._requests:
            self.on_close()

    def _write(self, req: WSGIRequest, data: Any):
        if req.aborted:
            raise WSGIAbortError("Request aborted")

        # Convert str to bytes if necessary (WSGI spec allows str in some cases)
        if isinstance(data, str):
            data = data.encode("latin-1")

        request_id = req.id
        try:
            if not req.response_started:
                status, headers = req.headers_set
                res = [f"Status: {status}\r\n".encode("latin-1")]
                for name, value in headers:
                    res.append(f"{name}: {value}\r\n".encode("latin-1"))
                res.append(b"\r\n")
                self.send_func(self.fcgi.send_stdout(request_id, b"".join(res)))
                req.response_started = True

            if data:
                self.send_func(self.fcgi.send_stdout(request_id, data))
        except Exception as e:
            logger.error(f"Error writing to FastCGI socket: {e}")
            raise WSGIAbortError("Connection lost")
