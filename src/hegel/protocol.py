"""
This defines the basic wire protocol of Hegel. It is a message oriented protocol,
with many logical connections (channels) multiplexed over a single connections.
Messages are in principle arbitrary bytes, and this is used for the version handshakes,
but in actual operation will be CBOR.

Handshake for running a test:

1. SDK creates a channel and sends a message to Hegel saying it
   wants a test of this name running on this channel.
2. Hegel creates a ConjectureRunner running in a thread whose test
   function operates as follows:
    1. Creates a channel and a ConjectureData and sends a message
       on the test channel saying it wants a new test case using
       this data.
    2. Goes into data serving mode where the Hegel SDK can now make
       requests for data to be generated (in our limited JSON
       schema) on that channel.
    3. stop/start_span, target, and mark_complete can also be sent.
       Note that the test function in the SDK *must* call
       mark complete.
3. Once the runner has finished running, it makes some number
   (possibly zero) of additional calls with test cases, adding an
   is_final: True to the args.
4. After that it sends a test_done message.
"""

import contextlib
import os
import socket
import struct
import sys
import traceback
import zlib
from collections import deque
from dataclasses import dataclass
from enum import Enum
from queue import Empty, SimpleQueue
from threading import Lock, Thread, current_thread
from typing import Any, NewType

import cbor2

from hegel.utils import UniqueIdentifier, not_set


def _is_protocol_debug():
    value = os.environ.get("HEGEL_PROTOCOL_DEBUG")
    value = value.lower() if value is not None else None
    if value not in {
        None,
        "1",
        "0",
        "true",
        "false",
    }:  # pragma: no cover # tested in subprocess
        raise ValueError(
            "invalid value for HEGEL_PROTOCOL_DEBUG: expected either '1', '0', 'true', "
            f"'false', or unset, but got {value!r}"
        )
    return value in {"1", "true"}


CHANNEL_TIMEOUT = float(os.getenv("HEGEL_CHANNEL_TIMEOUT", 30))


VERSION = "0.1"
VERSION_NEGOTIATION_MESSAGE = b"Hegel/1.0"
VERSION_NEGOTIATION_OK = b"Ok"

# HEGL in hex
MAGIC = 0x4845474C

# 5 unsigned 32-bit integers, big-endian:
# magic cookie, checksum, channel, message ID, payload length
HEADER_FORMAT = ">5I"
TERMINATOR = 0x0A  # '\n'

# If this is set in the ID, this is a reply to a previous message
REPLY_BIT = 1 << 31


# Special payload that is sent on a channel when it is shut down. The shutdown
# is not acked and is handled specifically
# Chosen to be invalid CBOR as per https://www.rfc-editor.org/rfc/rfc8949.html
# It is currently also not the prefix of any valid CBOR (this is a reserved)
# tag byte) but even if it became valid in future this would not be a problem.
CLOSE_CHANNEL_MESSAGE_ID = (1 << 31) - 1
CLOSE_CHANNEL_PAYLOAD = bytes([0b11111110])

SHUTDOWN = UniqueIdentifier("shutdown")

ChannelId = NewType("ChannelId", int)
MessageId = NewType("MessageId", int)


@dataclass(frozen=True, slots=True)
class Packet:
    """A single message in the wire protocol."""

    channel_id: ChannelId
    message_id: MessageId
    is_reply: bool
    payload: bytes


class PartialPacket(ConnectionError):
    """Raised when connection closes mid-packet."""


def recv_exact(sock: socket.socket, n: int) -> bytes:
    """Receive exactly n bytes from socket."""
    assert n >= 0
    if n == 0:
        return b""
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            if data:
                raise ConnectionError("Connection closed while reading data")
            raise PartialPacket("Connection closed partway through reading packet.")
        data.extend(chunk)
    return bytes(data)


def read_packet(sock: socket.socket) -> Packet:
    """Read and parse a single packet from the socket."""
    header = recv_exact(sock, struct.calcsize(HEADER_FORMAT))
    magic, checksum, channel, message_id, length = struct.unpack(HEADER_FORMAT, header)

    is_reply = message_id & REPLY_BIT != 0
    if is_reply:
        message_id ^= REPLY_BIT

    if magic != MAGIC:
        raise ValueError(
            f"Invalid magic number: expected 0x{MAGIC:08X}, got 0x{magic:08X}",
        )

    payload = recv_exact(sock, length)
    terminator = recv_exact(sock, 1)[0]
    if terminator != TERMINATOR:
        raise ValueError(
            f"Invalid terminator: expected 0x{TERMINATOR:02X}, got 0x{terminator:02X}",
        )

    # Verify checksum (CRC32 over header with checksum field zeroed + payload)
    header_for_check = header[:4] + b"\x00\x00\x00\x00" + header[8:]
    computed_crc = zlib.crc32(header_for_check + payload) & 0xFFFFFFFF
    if computed_crc != checksum:
        raise ValueError(
            f"Checksum mismatch: expected 0x{checksum:08X}, got 0x{computed_crc:08X}",
        )

    return Packet(
        channel_id=channel,
        message_id=message_id,
        payload=payload,
        is_reply=is_reply,
    )


def write_packet(sock: socket.socket, packet: Packet) -> None:
    message_id: int = packet.message_id
    if packet.is_reply:
        message_id |= REPLY_BIT

    # build a fake header to calculate the checksum
    header = struct.pack(
        ">5I", MAGIC, 0, packet.channel_id, message_id, len(packet.payload)
    )
    checksum = zlib.crc32(header + packet.payload) & 0xFFFFFFFF

    header = struct.pack(
        ">5I", MAGIC, checksum, packet.channel_id, message_id, len(packet.payload)
    )
    sock.sendall(header + packet.payload + bytes([TERMINATOR]))


@dataclass(frozen=True, slots=True)
class DeadChannel:
    """Marker for a closed channel, used for debugging."""

    channel_id: ChannelId
    name: str


class ConnectionState(Enum):
    """Connection state after handshake."""

    UNRESOLVED = 0
    CLIENT = 1
    SERVER = 2


class Connection:
    """Thread-safe multiplexed socket connection to a Hegel peer."""

    def __init__(self, socket, *, name=None, debug=None):
        """Initialize connection and start the reader thread."""
        self.name = name
        self.__socket = socket
        self.__next_channel_id = 1
        self.channels = {}
        self.__running = True
        self.__lock = Lock()
        if debug is None:
            debug = _is_protocol_debug()
        self._debug = debug
        self.__connection_state = ConnectionState.UNRESOLVED
        # Control channel must be created before the reader thread starts,
        # otherwise an incoming packet for channel 0 could arrive before
        # the channel is registered and be treated as a non-existent channel.
        self.__control_channel = self.new_channel(role="Control")
        self.__threads = [Thread(target=self.__run_reader, daemon=True)]
        for t in self.__threads:
            t.start()

    @property
    def live(self):
        return self.__running and all(t.is_alive() for t in self.__threads)

    @classmethod
    def create_server(cls, address, **kwargs):
        return cls(socket.create_server(address, **kwargs))

    def _debug_print(self, *args):
        if not self._debug:
            return
        print(*args, file=sys.stderr)

    def _debug_packet(self, direction: str, packet: Packet) -> None:
        """Print packet info for debugging."""
        if not self._debug:
            return

        try:
            payload_repr: object = packet.payload.decode("ascii")
        except UnicodeDecodeError:
            try:
                payload_repr = cbor2.loads(packet.payload)
            except Exception:
                payload_repr = packet.payload
        reply = "reply" if packet.is_reply else "request"

        if packet.channel_id == 0:
            ch = "Control"
        else:
            try:
                channel = self.channels[packet.channel_id]
                ch = channel.name
            except KeyError:
                ch = f"Unknown channel {packet.channel_id}"

        self._debug_print(
            f"[{self.name or '?'}] {direction} ch={ch}"
            f" message_id={packet.message_id}"
            f" {reply}: {payload_repr!r}",
        )

    def __run_reader(self):
        try:
            while self.__running:
                packet = read_packet(self.__socket)
                channel = self.channels.get(packet.channel_id)
                channel_name = (
                    f"channel {packet.channel_id}" if channel is None else channel.name
                )
                self._debug_packet("RECV", packet)
                if packet.payload == CLOSE_CHANNEL_PAYLOAD:
                    assert packet.message_id == CLOSE_CHANNEL_MESSAGE_ID
                    self._debug_print(f"Received close for {channel_name}")
                    # Dead channel markers only exist for debugging purposes to help
                    # distinguish messages sent to channels after they were closed
                    # from messages sent before they were opened.
                    if self._debug:
                        self.channels[packet.channel_id] = DeadChannel(
                            channel_id=packet.channel_id,
                            name=(
                                self.channels[packet.channel_id].name
                                if packet.channel_id in self.channels
                                else "Never opened!"
                            ),
                        )
                else:
                    if channel is None or isinstance(channel, DeadChannel):
                        error_type = "non-existent" if channel is None else "closed"
                        error = (
                            f"Message {packet.message_id}"
                            f" sent to {error_type} {channel_name}"
                        )

                        self._debug_print(error)
                        if not packet.is_reply:
                            self.send_packet(
                                Packet(
                                    channel_id=packet.channel_id,
                                    message_id=packet.message_id,
                                    is_reply=True,
                                    payload=cbor2.dumps({"error": error}),
                                ),
                            )
                    else:
                        channel.inbox.put(packet)
        except ConnectionError:
            pass
        except Exception:
            traceback.print_exc()
        finally:
            self.close()

    def send_packet(self, packet: Packet) -> None:
        """Send a packet to the peer, thread-safe."""
        with self.__lock:
            self._debug_packet("SEND", packet)
            write_packet(self.__socket, packet)

    def close(self) -> None:
        """Close the connection and clean up resources."""
        if not self.__running:
            return

        self.__running = False
        with contextlib.suppress(OSError):
            self.__socket.shutdown(socket.SHUT_RDWR)
        self.__socket.close()

        current = current_thread()
        for t in self.__threads:
            if t is not current:
                t.join(timeout=0.1)

        for v in self.channels.values():
            if not isinstance(v, DeadChannel):
                v.inbox.put(SHUTDOWN)

        assert self.__socket._closed

    def send_handshake(self):
        """Initiate handshake as a client."""
        if self.__connection_state != ConnectionState.UNRESOLVED:
            raise ValueError("Handshake already established")

        self.__connection_state = ConnectionState.CLIENT
        message_id = self.control_channel.send_request_raw(VERSION_NEGOTIATION_MESSAGE)
        response = self.control_channel.receive_response_raw(message_id)
        if response != VERSION_NEGOTIATION_OK:
            raise ConnectionError(f"Bad handshake result {response!r}")

    def receive_handshake(self):
        """Accept handshake as a server."""
        if self.__connection_state != ConnectionState.UNRESOLVED:
            raise ValueError("Handshake already established")

        self.__connection_state = ConnectionState.SERVER
        control = self.control_channel
        # Version negotiation
        message_id, payload = control.receive_request_raw()
        if payload == VERSION_NEGOTIATION_MESSAGE:
            control.send_response_raw(message_id, b"Ok")
        else:
            control.send_response_raw(
                message_id,
                f"Error: Unrecognised negotiation string {payload!r}".encode(),
            )

    @property
    def control_channel(self) -> "Channel":
        """Special channel for connection-level control commands."""
        return self.__control_channel

    def new_channel(self, *, role: str | None = None) -> "Channel":
        """Create a new logical channel on this connection."""
        if not self.channels:
            channel_id = ChannelId(0)
        elif self.__connection_state == ConnectionState.UNRESOLVED:
            raise ValueError(
                "Cannot create a new channel before handshake has been performed.",
            )
        else:
            channel_id = ChannelId(
                (self.__next_channel_id << 1)
                | int(self.__connection_state == ConnectionState.CLIENT)
            )
            self.__next_channel_id += 1

        result = Channel(connection=self, channel_id=channel_id, role=role)
        with self.__lock:
            self.channels[result.channel_id] = result
        return result

    def connect_channel(self, id: ChannelId, *, role: str | None = None) -> "Channel":
        """Connect to a channel created by the peer."""
        if self.__connection_state == ConnectionState.UNRESOLVED:
            raise ValueError(
                "Cannot create a new channel before handshake has been performed.",
            )

        if id in self.channels:
            raise ValueError(f"Channel already connected as {self.channels[id]}.")
        assert id & 1 != int(self.__connection_state == ConnectionState.CLIENT)

        result = Channel(connection=self, channel_id=id, role=role)
        with self.__lock:
            self.channels[result.channel_id] = result
        return result


class RequestError(Exception):
    """Error response from the peer."""

    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__(data.pop("error"))
        self.error_type = data.pop("type")
        self.data = data


def result_or_error(body: dict[str, Any]) -> Any:
    if "error" in body:
        raise RequestError(body)

    return body["result"]


class PendingRequest:
    """Future-like handle for an in-flight request."""

    def __init__(self, channel: "Channel", message_id: MessageId) -> None:
        self.__channel = channel
        self.__message_id = message_id
        self.__value: Any = not_set

    def get(self) -> Any:
        """Block until response arrives and return the result.

        We cache the decoded response so that if it contains an error,
        the same error is raised consistently on every call to get().
        """
        if self.__value is not_set:
            self.__value = cbor2.loads(
                self.__channel.receive_response_raw(self.__message_id)
            )
        return result_or_error(self.__value)


class Channel:
    """Non-thread-safe logical channel for request/response messaging."""

    def __init__(
        self,
        connection: Connection,
        channel_id: ChannelId,
        role: str | None = None,
    ) -> None:
        self.channel_id = channel_id
        self.connection = connection
        self.inbox: SimpleQueue[Any] = SimpleQueue()
        self.requests: deque[Packet] = deque()
        self.responses: dict[MessageId, bytes] = {}
        self.role = role
        assert channel_id != 0 or role == "Control"
        self.next_message_id = MessageId(1)
        self.__closed = False

    def __repr__(self):
        if self.role is None:
            return f"Channel({self.channel_id})"
        else:
            return f"Channel({self.channel_id}, role={self.role})"

    def close(self):
        """Close this channel and notify the peer."""
        if self.__closed or self.connection.channels.get(self.channel_id) is not self:
            self.__closed = True
            return
        self.__closed = True
        if self.connection._debug:
            self.connection.channels[self.channel_id] = DeadChannel(
                name=self.name,
                channel_id=self.channel_id,
            )
        if self.connection.live:
            self.connection.send_packet(
                Packet(
                    payload=CLOSE_CHANNEL_PAYLOAD,
                    message_id=CLOSE_CHANNEL_MESSAGE_ID,
                    channel_id=self.channel_id,
                    is_reply=False,
                ),
            )

    @property
    def name(self):
        if self.role is None and self.connection.name is None:
            return f"Channel {self.channel_id}"
        elif self.role is None:
            return f"{self.connection.name} channel [id={self.channel_id}]"
        else:
            return (
                f"{self.connection.name} channel [id={self.channel_id}] ({self.role})"
            )

    def __process_one_message(self, *, timeout=CHANNEL_TIMEOUT):
        """Route an incoming message to responses or requests queue."""
        if self.__closed:
            raise ConnectionError(f"{self.name} is closed")
        try:
            packet = self.inbox.get(timeout=timeout)
        except Empty:
            raise TimeoutError(
                f"Timed out after {timeout}s waiting for a message on {self.name}",
            ) from None
        if packet is SHUTDOWN:
            raise ConnectionError("Connection closed")

        if packet.is_reply:
            if packet.message_id in self.responses:
                raise ValueError(f"Got two responses for message ID {id}")
            else:
                self.responses[packet.message_id] = packet.payload
        else:
            self.requests.append(packet)

    def request(self, message: dict) -> PendingRequest:
        """Send a CBOR request and return a future for the response."""
        message_id = self.send_request(message)
        return PendingRequest(self, message_id)

    def handle_requests(self, handler, until=lambda: False):
        """Process incoming requests with handler until condition is met."""
        while not until():
            message_id, message = self.receive_request()
            try:
                result = handler(message)
                self.send_response_value(message_id, result)
            except BaseException as e:
                self.send_response_error(message_id, e)

    def send_request(self, message: dict) -> MessageId:
        """Send a CBOR-encoded request, return message ID."""
        return self.send_request_raw(cbor2.dumps(message))

    def send_request_raw(self, message: bytes) -> MessageId:
        """Send raw bytes as request, return message ID for response."""
        message_id = self.next_message_id
        self.next_message_id = MessageId(self.next_message_id + 1)
        self.connection.send_packet(
            Packet(
                payload=message,
                channel_id=self.channel_id,
                is_reply=False,
                message_id=message_id,
            ),
        )
        return message_id

    def receive_response(
        self, message_id: MessageId, timeout: float | None = CHANNEL_TIMEOUT
    ) -> Any:
        """Wait for and decode response to a request."""
        return result_or_error(
            cbor2.loads(self.receive_response_raw(message_id, timeout=timeout)),
        )

    def receive_response_raw(
        self,
        message_id: MessageId,
        timeout: float | None = CHANNEL_TIMEOUT,
    ) -> bytes:
        """Wait for raw response bytes to a request."""
        while message_id not in self.responses:
            self.__process_one_message(timeout=timeout)
        return self.responses.pop(message_id)

    def receive_request(
        self,
        timeout: float | None = CHANNEL_TIMEOUT,
    ) -> tuple[MessageId, Any]:
        """Receive and decode a request from the peer."""
        message_id, body = self.receive_request_raw(timeout=timeout)
        return message_id, cbor2.loads(body)

    def receive_request_raw(
        self,
        timeout: float | None = CHANNEL_TIMEOUT,
    ) -> tuple[MessageId, bytes]:
        """Receive raw request bytes and message ID for responding."""
        while not self.requests:
            self.__process_one_message(timeout=timeout)
        result = self.requests.popleft()
        return (result.message_id, result.payload)

    def send_response_raw(self, message_id: MessageId, message: bytes) -> None:
        """Send raw bytes as response to a request."""
        self.connection.send_packet(
            Packet(
                payload=message,
                channel_id=self.channel_id,
                is_reply=True,
                message_id=message_id,
            ),
        )

    def send_response_value(self, message_id: MessageId, message: Any) -> None:
        """Send a success response with the given value."""
        self.send_response_raw(message_id, cbor2.dumps({"result": message}))

    def send_response_error(
        self,
        message_id: MessageId,
        message: Exception | None = None,
        *,
        error: str | None = None,
        error_type: str | None = None,
    ) -> None:
        """Send an error response."""
        assert message is not None or (error is not None and error_type is not None)
        if error is None:
            assert message is not None
            error = str(message.args[0])
        if error_type is None:
            assert message is not None
            error_type = type(message).__name__
        response = {"error": error, "type": error_type}
        if message is not None:
            response["detail"] = "".join(traceback.format_exception(message))
        self.send_response_raw(
            message_id,
            cbor2.dumps(response),
        )
