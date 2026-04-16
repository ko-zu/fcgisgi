import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

# FastCGI Constants
FCGI_LISTENSOCK_FILENO = 0
FCGI_VERSION_1 = 1

FCGI_BEGIN_REQUEST = 1
FCGI_ABORT_REQUEST = 2
FCGI_END_REQUEST = 3
FCGI_PARAMS = 4
FCGI_STDIN = 5
FCGI_STDOUT = 6
FCGI_STDERR = 7
FCGI_DATA = 8
FCGI_GET_VALUES = 9
FCGI_GET_VALUES_RESULT = 10
FCGI_UNKNOWN_TYPE = 11

FCGI_MAX_CONNS = b"FCGI_MAX_CONNS"
FCGI_MAX_REQS = b"FCGI_MAX_REQS"
FCGI_MPXS_CONNS = b"FCGI_MPXS_CONNS"

FCGI_RESPONDER = 1
FCGI_AUTHORIZER = 2
FCGI_FILTER = 3

FCGI_REQUEST_COMPLETE = 0
FCGI_CANT_MPX_CONN = 1
FCGI_OVERLOADED = 2
FCGI_UNKNOWN_ROLE = 3

FCGI_KEEP_CONN = 1

FCGI_HEADER_FORMAT = "!BBHHBx"
FCGI_HEADER_LEN = struct.calcsize(FCGI_HEADER_FORMAT)

FCGI_BEGIN_REQUEST_BODY_FORMAT = "!HB5x"
FCGI_END_REQUEST_BODY_FORMAT = "!LB3x"

MAX_CONTENT_LEN = 65535


@dataclass
class RequestStarted:
    request_id: int
    role: int
    flags: int


@dataclass
class ParamsReceived:
    request_id: int
    params: List[Tuple[bytes, bytes]]


@dataclass
class StdinReceived:
    request_id: int
    data: bytes


@dataclass
class EndOfStdin:
    request_id: int


@dataclass
class DataReceived:
    request_id: int
    data: bytes


@dataclass
class EndOfData:
    request_id: int


@dataclass
class AbortRequest:
    request_id: int


@dataclass
class GetValues:
    keys: Dict[bytes, bytes]


Event = Union[
    RequestStarted,
    ParamsReceived,
    StdinReceived,
    EndOfStdin,
    DataReceived,
    EndOfData,
    AbortRequest,
    GetValues,
]


class FastCGIConnection:
    def __init__(self):
        self._buffer = bytearray()
        self._requests: Dict[int, dict] = {}

    def feed_data(self, data: bytes) -> List[Event]:
        self._buffer.extend(data)
        events = []
        while len(self._buffer) >= FCGI_HEADER_LEN:
            version, type_, request_id, content_len, padding_len = struct.unpack(
                FCGI_HEADER_FORMAT, self._buffer[:FCGI_HEADER_LEN]
            )

            total_len = FCGI_HEADER_LEN + content_len + padding_len
            if len(self._buffer) < total_len:
                break

            content = self._buffer[FCGI_HEADER_LEN : FCGI_HEADER_LEN + content_len]
            del self._buffer[:total_len]

            event = self._handle_record(type_, request_id, content)
            if event:
                events.append(event)

        return events

    def _handle_record(self, type_: int, request_id: int, content: bytes) -> Optional[Event]:
        if type_ == FCGI_BEGIN_REQUEST:
            role, flags = struct.unpack(FCGI_BEGIN_REQUEST_BODY_FORMAT, content)
            self._requests[request_id] = {
                "role": role,
                "flags": flags,
                "params": bytearray(),
            }
            return RequestStarted(request_id, role, flags)

        elif type_ == FCGI_ABORT_REQUEST:
            return AbortRequest(request_id)

        elif type_ == FCGI_PARAMS:
            if request_id in self._requests:
                if content:
                    self._requests[request_id]["params"].extend(content)
                    return None
                else:
                    params_data = self._requests[request_id].pop("params")
                    params = self._decode_pairs(params_data)
                    return ParamsReceived(request_id, params)

        elif type_ == FCGI_STDIN:
            return StdinReceived(request_id, bytes(content)) if content else EndOfStdin(request_id)

        elif type_ == FCGI_DATA:
            return DataReceived(request_id, bytes(content)) if content else EndOfData(request_id)

        elif type_ == FCGI_GET_VALUES:
            return GetValues(self._decode_pairs(content))

        return None

    def _decode_pairs(self, data: bytes) -> List[Tuple[bytes, bytes]]:
        pairs = []
        pos = 0
        while pos < len(data):
            try:
                name_len = data[pos]
                if name_len & 128:
                    name_len = struct.unpack("!L", data[pos : pos + 4])[0] & 0x7FFFFFFF
                    pos += 4
                else:
                    pos += 1

                value_len = data[pos]
                if value_len & 128:
                    value_len = struct.unpack("!L", data[pos : pos + 4])[0] & 0x7FFFFFFF
                    pos += 4
                else:
                    pos += 1

                name = bytes(data[pos : pos + name_len])
                pos += name_len
                value = bytes(data[pos : pos + value_len])
                pos += value_len
                pairs.append((name, value))
            except (IndexError, struct.error):
                break
        return pairs

    def send_stdout(self, request_id: int, data: bytes) -> bytes:
        return self._encode_split_records(FCGI_STDOUT, request_id, data)

    def send_stderr(self, request_id: int, data: bytes) -> bytes:
        return self._encode_split_records(FCGI_STDERR, request_id, data)

    def send_end_request(self, request_id: int, app_status: int, protocol_status: int) -> bytes:
        content = struct.pack(FCGI_END_REQUEST_BODY_FORMAT, app_status, protocol_status)
        return self._encode_record(FCGI_END_REQUEST, request_id, content)

    def send_get_values_result(self, values: Dict[bytes, bytes]) -> bytes:
        content = bytearray()
        for name, value in values.items():
            content.extend(self.encode_pair(name, value))
        return self._encode_record(FCGI_GET_VALUES_RESULT, 0, bytes(content))

    def _encode_split_records(self, type_: int, request_id: int, data: bytes) -> bytes:
        if not data:
            return self._encode_record(type_, request_id, b"")

        res = bytearray()
        for i in range(0, len(data), MAX_CONTENT_LEN):
            chunk = data[i : i + MAX_CONTENT_LEN]
            res.extend(self._encode_record(type_, request_id, chunk))
        return bytes(res)

    def _encode_record(self, type_: int, request_id: int, content: bytes) -> bytes:
        content_len = len(content)
        padding_len = -content_len & 7
        header = struct.pack(
            FCGI_HEADER_FORMAT,
            FCGI_VERSION_1,
            type_,
            request_id,
            content_len,
            padding_len,
        )
        return header + content + b"\x00" * padding_len

    def encode_pair(self, name: bytes, value: bytes) -> bytes:
        res = bytearray()
        for data in (name, value):
            length = len(data)
            if length < 128:
                res.append(length)
            else:
                res.extend(struct.pack("!L", length | 0x80000000))
        res.extend(name)
        res.extend(value)
        return bytes(res)
