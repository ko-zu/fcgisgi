import io
import threading
import queue
import socket
from typing import Callable, Dict, Any, List, Optional
from .sansio import (
    FastCGIConnection, RequestStarted, ParamsReceived, StdinReceived, 
    EndOfStdin, AbortRequest, Event, FCGI_RESPONDER, FCGI_REQUEST_COMPLETE
)

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
            # Block until data is available or EOF/Abort is signaled
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

class WSGIAdapter:
    def __init__(self, application: Callable, send_func: Callable[[bytes], None], spawn_func: Callable = None):
        self.application = application
        self.send_func = send_func
        self.spawn_func = spawn_func or self._default_spawn
        self.fcgi = FastCGIConnection()
        self._requests: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _default_spawn(self, target, args):
        t = threading.Thread(target=target, args=args)
        t.daemon = True
        t.start()
        return t

    def handle_data(self, data: bytes):
        events = self.fcgi.feed_data(data)
        for event in events:
            self.handle_event(event)

    def handle_event(self, event: Event):
        if isinstance(event, RequestStarted):
            if event.role != FCGI_RESPONDER:
                self.send_func(self.fcgi.send_end_request(event.request_id, 0, 3))
                return
            
            stdin = WSGIInput()
            with self._lock:
                self._requests[event.request_id] = {
                    "id": event.request_id,
                    "params": {},
                    "stdin": stdin,
                    "thread": None,
                    "headers_set": False,
                    "response_started": False,
                    "aborted": False,
                }
        
        elif isinstance(event, ParamsReceived):
            with self._lock:
                req = self._requests.get(event.request_id)
            if req:
                req["params"] = {k.decode('latin-1'): v.decode('latin-1') for k, v in event.params.items()}
                req["thread"] = self.spawn_func(self._run_app, (req,))
        
        elif isinstance(event, StdinReceived):
            with self._lock:
                req = self._requests.get(event.request_id)
            if req:
                req["stdin"].put(event.data)
        
        elif isinstance(event, EndOfStdin):
            with self._lock:
                req = self._requests.get(event.request_id)
            if req:
                req["stdin"].put_eof()
        
        elif isinstance(event, AbortRequest):
            self._abort_request(event.request_id)

    def _abort_request(self, request_id: int):
        with self._lock:
            req = self._requests.get(request_id)
            if req and not req["aborted"]:
                req["aborted"] = True
                req["stdin"].abort()

    def close_all(self):
        """Signals abort to all active requests. Call this when the connection is lost."""
        with self._lock:
            ids = list(self._requests.keys())
        for rid in ids:
            self._abort_request(rid)

    def _run_app(self, req: Dict[str, Any]):
        request_id = req["id"]
        environ = req["params"]
        
        environ['wsgi.version'] = (1, 0)
        environ['wsgi.url_scheme'] = environ.get('HTTPS', 'off') in ('on', '1') and 'https' or 'http'
        environ['wsgi.input'] = io.BufferedReader(req["stdin"])
        environ['wsgi.errors'] = io.BytesIO()
        environ['wsgi.multithread'] = True
        environ['wsgi.multiprocess'] = False
        environ['wsgi.run_once'] = False

        if 'SCRIPT_NAME' not in environ:
            environ['SCRIPT_NAME'] = ''
        if 'PATH_INFO' not in environ:
            environ['PATH_INFO'] = ''

        def start_response(status, response_headers, exc_info=None):
            if exc_info:
                if req["response_started"]:
                    raise exc_info[1].with_traceback(exc_info[2])
            
            req["headers_set"] = (status, response_headers)
            return lambda data: self._write(req, data)

        result = None
        try:
            result = self.application(environ, start_response)
            for data in result:
                if data:
                    self._write(req, data)
                if req["aborted"]:
                    break
            if not req["response_started"] and not req["aborted"]:
                self._write(req, b"")
        except WSGIAbortError:
            pass
        except Exception:
            if not req["response_started"] and not req["aborted"]:
                try:
                    start_response('500 Internal Server Error', [('Content-Type', 'text/plain')])
                    self._write(req, b"Internal Server Error")
                except Exception:
                    pass
        finally:
            # WSGI spec requirement: always call close() if it exists
            if hasattr(result, 'close'):
                try:
                    result.close()
                except Exception:
                    pass
            
            if not req["aborted"]:
                try:
                    self.send_func(self.fcgi.send_stdout(request_id, b""))
                    self.send_func(self.fcgi.send_end_request(request_id, 0, FCGI_REQUEST_COMPLETE))
                except Exception:
                    pass
            
            with self._lock:
                if request_id in self._requests:
                    self._requests.pop(request_id)

    def _write(self, req: Dict[str, Any], data: bytes):
        if req["aborted"]:
            raise WSGIAbortError("Request aborted")

        request_id = req["id"]
        try:
            if not req["response_started"]:
                status, headers = req["headers_set"]
                res = [f"Status: {status}\r\n".encode('latin-1')]
                for name, value in headers:
                    res.append(f"{name}: {value}\r\n".encode('latin-1'))
                res.append(b"\r\n")
                self.send_func(self.fcgi.send_stdout(request_id, b"".join(res)))
                req["response_started"] = True
            
            if data:
                self.send_func(self.fcgi.send_stdout(request_id, data))
        except Exception:
            req["aborted"] = True
            raise WSGIAbortError("Connection lost")
