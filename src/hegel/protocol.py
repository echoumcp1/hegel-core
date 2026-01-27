
import cbor2
import socket
import struct
import zlib
from dataclasses import dataclass
from queue import SimpleQueue
import traceback
from collections import deque
from threading import Thread, Lock, current_thread
from typing import Any

VERSION = "0.2"

VERSION_NEGOTIATION_MESSAGE = f"Hegel Version {VERSION}".encode('utf-8')
VERSION_NEGOTIATION_OK = b"Ok"

# HEGL
MAGIC = 0x4845474c

# 5 unsigned 32-bit integers, big-endian:
# magic cookie, checksum, channel, message ID, payload length
HEADER_FORMAT = ">5I"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
TERMINATOR = 0x0A # '\n'

# If this is set in the ID, this is a reply to a previous message
REPLY_BIT = 1 << 31

Id = int

@dataclass(frozen=True, slots=True)
class Packet:
    channel: Id
    message_id: Id
    is_reply: bool
    payload: bytes


class PartialPacket(ConnectionError):
    pass



def recv_exact(sock: socket.socket, n: int) -> bytes:
    """Receive exactly n bytes from socket."""
    assert n >= 0
    if n == 0:
        return b''
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            if data:
                raise ConnectionError("Connection closed while reading data")
            else:
                raise PartialPacket("Connection closed partway through reading packet.")
        data.extend(chunk)
    return bytes(data)


def read_packet(sock: socket.socket) -> Packet:
    """Read and parse a single packet from the socket."""
    # Read fixed header
    header = recv_exact(sock, HEADER_SIZE)
    magic, checksum, channel, message_id, length = struct.unpack(HEADER_FORMAT, header)

    is_reply = message_id & REPLY_BIT != 0

    if is_reply:
        message_id ^= REPLY_BIT

    # Validate magic number
    if magic != MAGIC:
        raise ValueError(f"Invalid magic number: expected 0x{MAGIC:08X}, got 0x{magic:08X}")

    # Read payload
    payload = recv_exact(sock, length)

    # Read terminator
    terminator = recv_exact(sock, 1)[0]
    if terminator != TERMINATOR:
        raise ValueError(f"Invalid terminator: expected 0x{TERMINATOR:02X}, got 0x{terminator:02X}")

    # Verify checksum (CRC32-ISO over channel + message_id + length + payload)
    checksummed_data = header[8:] + payload
    computed_crc = zlib.crc32(checksummed_data) & 0xFFFFFFFF
    if computed_crc != checksum:
        raise ValueError(f"Checksum mismatch: expected 0x{checksum:08X}, got 0x{computed_crc:08X}")

    return Packet(channel=channel, message_id=message_id, payload=payload, is_reply=is_reply)


def write_packet(sock: socket.socket, packet: Packet) -> None:
    """Write a packet to a socket."""
    magic = MAGIC
    channel = packet.channel
    message_id = packet.message_id
    if packet.is_reply:
        message_id |= REPLY_BIT
    length = len(packet.payload)

    buffer = bytearray(struct.pack('>3I', channel, message_id, length))
    buffer.extend(packet.payload)
    checksum = zlib.crc32(buffer) & 0xFFFFFFFF
    buffer[:0] = struct.pack('>2I', magic, checksum)
    buffer.append(TERMINATOR)
    sock.send(buffer)


SHUTDOWN = object()


class Connection:
    """A connection is a single real socket connection to
    the Hegel server. It is designed to be thread safe. In
    order to actually interact with the server, you will need
    to use a *Channel*, which is a non-thread-safe logical
      connection supporting sending and receiving objects."""\

    def __init__(self, socket, name=None):
        """Connect to a given endpoint running the Hegel protocol
        on the other end."""
        self.name = name
        self.__socket = socket
        self.__next_channel_id = 0
        self.channels = {}
        self.__control_channel = self.new_channel()
        self.__running = True 
        self.__lock = Lock()
        self.__threads = [
            Thread(target=self.run_reader, daemon=True),
        ]
        for t in self.__threads:
            t.start()


    @classmethod
    def create_server(cls, address, **kwargs):
        return cls(socket.create_server(address, **kwargs))

    def run_reader(self):
        try:
            while self.__running:
                packet = read_packet(self.__socket)
                self.channels[packet.channel].inbox.put(packet)
        except ConnectionError:
            pass
        except Exception:
            traceback.print_exc()
        finally:
            self.close()

    def send_packet(self, packet: Packet):
        with self.__lock:
            write_packet(self.__socket, packet) 

    def close(self):
        self.__running = False
        try:
            self.__socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass  # Already shut down or not connected
        self.__socket.close()
        current = current_thread()
        for t in self.__threads:
            if t is not current:
                t.join(timeout=0.1)
        for v in self.channels.values():
            v.inbox.put(SHUTDOWN)
        assert self.__socket._closed

    @property
    def control_channel(self) -> "Channel":
        """Special channel for sending control
        commands affecting the entire connection."""
        return self.__control_channel

    def new_channel(self) -> "Channel":
        """Creates a new channel."""
        channel_id = self.__next_channel_id
        self.__next_channel_id += 1
        result = Channel(connection=self, channel_id=channel_id)
        self.channels[result.channel_id] = result
        return result

    def connect_channel(self, id: Id) -> "Channel":
        """Creates the other half of a channel that has already
        been created on the other side of this connection. Errors
        if the other side does not exist or has already been connected
        to."""
        result = Channel(connection=self, channel_id=id)
        self.channels[result.channel_id] = result
        return result


NOT_SET = object()

class RequestError(Exception):
    def __init__(self, data):
        super().__init__(data.pop('error'))
        self.error_type = data.pop('type')
        self.data = data


class PendingRequest:
    def __init__(self, channel, id):
        self.__channel = channel
        self.__id = id
        self.__value = NOT_SET

    def get(self):
        if self.__value is NOT_SET:
            self.__value = cbor2.loads(self.__channel.receive_response(self.__id))
        if 'error' in self.__value:
            raise RequestError(self.__value)
        else:
            return self.__value['result']


class Channel:
    def __init__(self, connection, channel_id):
        self.channel_id = channel_id
        self.connection = connection
        self.inbox = SimpleQueue()
        self.requests = deque()
        self.responses = {}
        self.next_message_id = 1
        self.__closed = False

    def close(self):
        self.__closed = True
        self.connection.channels.pop(self.channel_id)

    def __process_one_message(self):
        """Process one message that has been sent to us, and put it in the
        right place (either a response to a message we've already sent, or
        a request)"""
        if self.__closed:
            raise ConnectionError("Channel closed")
        packet = self.inbox.get()
        if packet is SHUTDOWN:
            raise ConnectionError("Connection closed")
        
        if packet.is_reply:
            if packet.message_id in self.responses:
                raise ValueError(f"Got two responses for message ID {id}")
            else:
                self.responses[packet.message_id] = packet.payload
        else:
            self.requests.append(packet)

    @property
    def name(self):
        return f"{self.__connection.name} channel {self.channel_id}"

    def request(self, message: Any) -> PendingRequest:
        """Takes an arbitrary object, serializes it as CBOR, and
        returns a future-like object for getting its response,
        which will also be interpreted as CBOR and may wrap either
        a result or an error."""
        id = self.send_request(cbor2.dumps(message))
        return PendingRequest(self, id)

    def handle_requests(self, handler, until=lambda: False):
        while not until():
            id, payload = self.receive_request()
            try:
                message = cbor2.loads(payload)
                result = handler(message)
                self.send_response(
                    id, cbor2.dumps({'result': result})
                )
            except Exception as e:
                self.send_response(
                    id, cbor2.dumps({'error': e.args[0], 'args': e.args, 'type': e.type.__name__})
                )


    def send_request(self, message: bytes) -> Id:
        """Sends a message and returns an Id that can be used
        too wait for a response."""
        id = self.next_message_id
        self.next_message_id += 1
        self.connection.send_packet(Packet(
            payload=message,
            channel=self.channel_id,
            is_reply=False,
            message_id=id,
        ))
        return id

    def receive_response(self, id: Id) -> bytes:
        """Waits for a response to a previously sent message."""
        while id not in self.responses:
            self.__process_one_message()
        return self.responses.pop(id)

    def receive_request(self) -> tuple[Id, bytes]:
        """Receives a request from the other side, along with
        an Id to respond on."""
        while not self.requests:
            self.__process_one_message()
        result = self.requests.popleft()
        return (result.message_id, result.payload)

    def send_response(self, id: Id, message: bytes):
        """Sends a response to a previously received message."""
        self.connection.send_packet(Packet(
            payload=message,
            channel=self.channel_id,
            is_reply=True,
            message_id=id,
        ))