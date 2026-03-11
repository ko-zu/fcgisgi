import io
import threading
import queue
from typing import Callable, Dict, Any, List
from .sansio import (
    FastCGIConnection, RequestStarted, ParamsReceived, StdinReceived, 
    EndOfStdin, AbortRequest, Event, FCGI_RESPONDER, FCGI_REQUEST_COMPLETE
)

class WSGIInput(io.RawIOBase):
    def __init__(self):
        self._queue = queue.Queue()
        self._eof = False
        self._buffer = b""

    def readinto(self, b):
        if not self._buffer and not self._eof:
            data = self._queue.get()
            if data is None:
                self._eof = True
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

class WSGIAdapter:
    def __init__(self, application: Callable, send_func: Callable[[bytes], None], spawn_func: Callable = None):
        self.application = application
        self.send_func = send_func
        self.spawn_func = spawn_func or self._default_spawn
        self.fcgi = FastCGIConnection()
        self._requests: Dict[int, Dict[str, Any]] = {}

    def _default_spawn(self, target, args):
        t = threading.Thread(target=target, args=args)
        t.start()
        return t

    def handle_data(self, data: bytes):
        events = self.fcgi.feed_data(data)
        for event in events:
            self.handle_event(event)

    def handle_event(self, event: Event):
        if isinstance(event, RequestStarted):
            if event.role != FCGI_RESPONDER:
                # We only support Responder for now
                self.send_func(self.fcgi.send_end_request(event.request_id, 0, 3)) # FCGI_UNKNOWN_ROLE
                return
            
            stdin = WSGIInput()
            self._requests[event.request_id] = {
                "id": event.request_id,
                "params": {},
                "stdin": stdin,
                "thread": None,
                "headers_set": False,
                "response_started": False,
            }
        
        elif isinstance(event, ParamsReceived):
            req = self._requests.get(event.request_id)
            if req:
                req["params"] = {k.decode('latin-1'): v.decode('latin-1') for k, v in event.params.items()}
                # Start the request using spawn_func
                req["thread"] = self.spawn_func(self._run_app, (req,))
        
        elif isinstance(event, StdinReceived):
            req = self._requests.get(event.request_id)
            if req:
                req["stdin"].put(event.data)
        
        elif isinstance(event, EndOfStdin):
            req = self._requests.get(event.request_id)
            if req:
                req["stdin"].put_eof()
        
        elif isinstance(event, AbortRequest):
            # Ideally we'd signal the thread to stop, but WSGI doesn't support this easily.
            # For now, just remove it from our tracking.
            self._requests.pop(event.request_id, None)

    def _run_app(self, req: Dict[str, Any]):
        request_id = req["id"]
        environ = req["params"]
        
        # Add required WSGI variables
        environ['wsgi.version'] = (1, 0)
        environ['wsgi.url_scheme'] = environ.get('HTTPS', 'off') in ('on', '1') and 'https' or 'http'
        environ['wsgi.input'] = io.BufferedReader(req["stdin"])
        environ['wsgi.errors'] = io.BytesIO() # For now
        environ['wsgi.multithread'] = True
        environ['wsgi.multiprocess'] = False
        environ['wsgi.run_once'] = False

        # Some servers don't provide these
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

        try:
            result = self.application(environ, start_response)
            try:
                for data in result:
                    if data:
                        self._write(req, data)
                # Ensure headers are sent if body was empty
                if not req["response_started"]:
                    self._write(req, b"")
            finally:
                if hasattr(result, 'close'):
                    result.close()
        except Exception as e:
            # Handle error
            pass
        finally:
            self.send_func(self.fcgi.send_stdout(request_id, b"")) # EOF for stdout
            self.send_func(self.fcgi.send_end_request(request_id, 0, FCGI_REQUEST_COMPLETE))
            self._requests.pop(request_id, None)

    def _write(self, req: Dict[str, Any], data: bytes):
        request_id = req["id"]
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
