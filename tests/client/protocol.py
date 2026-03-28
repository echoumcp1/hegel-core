import contextlib
import socket
from collections import defaultdict, deque
from typing import Any

import cbor2
from cbor2 import CBORTag

from hegel.protocol.connection import HANDSHAKE_STRING
from hegel.protocol.packet import (
    CLOSE_CHANNEL_MESSAGE_ID,
    CLOSE_CHANNEL_PAYLOAD,
    Packet,
    read_packet,
    write_packet,
)
from hegel.protocol.utils import (
    ChannelId,
    MessageId,
    ProtocolError,
    RequestError,
)


def _decode_hook(_decoder: object, tag: CBORTag) -> object:
    if tag.tag == 6:
        return tag.value.decode("utf-8", "surrogatepass")
    return tag


class ClientChannel:
    def __init__(
        self,
        connection: "ClientConnection",
        channel_id: ChannelId,
    ) -> None:
        self.connection = connection
        self.channel_id = channel_id

        self.requests: deque[Packet] = deque()
        self.replies: dict[MessageId, Packet] = {}

        self.next_message_id = MessageId(1)
        self.closed = False

    def close(self):
        """Close this channel."""
        if self.closed:
            return

        self.closed = True
        if self.connection.running:
            self.connection.write_packet(
                Packet(
                    payload=CLOSE_CHANNEL_PAYLOAD,
                    message_id=CLOSE_CHANNEL_MESSAGE_ID,
                    channel_id=self.channel_id,
                    is_reply=False,
                ),
            )

    def _receive_one(self) -> None:
        """Read packets from the socket until one for this channel arrives."""
        packet = self.connection.receive_packet_for_channel(self.channel_id)
        if packet.is_reply:
            assert packet.message_id not in self.replies
            self.replies[packet.message_id] = packet
        else:
            self.requests.append(packet)

    def send_request(self, payload: dict) -> Any:
        """Send a CBOR request and block until reply arrives. Returns the result."""
        packet = self.write_request(cbor2.dumps(payload))
        reply = self.read_reply(packet.message_id)
        result = cbor2.loads(reply.payload, tag_hook=_decode_hook)
        if "error" in result:
            raise RequestError(result["error"], error_type=result["type"])
        return result["result"]

    def write_request(self, payload: bytes) -> Packet:
        """Write a request packet to the socket. Returns the packet."""
        assert isinstance(payload, bytes)
        packet = Packet(
            payload=payload,
            channel_id=self.channel_id,
            is_reply=False,
            message_id=self.next_message_id,
        )
        self.connection.write_packet(packet)
        self.next_message_id = MessageId(self.next_message_id + 1)
        return packet

    def write_reply_bytes(self, message_id: MessageId, payload: bytes) -> None:
        self.connection.write_packet(
            Packet(
                payload=payload,
                channel_id=self.channel_id,
                is_reply=True,
                message_id=message_id,
            ),
        )

    def write_reply(self, message_id: MessageId, value: Any) -> None:
        self.write_reply_bytes(message_id, cbor2.dumps({"result": value}))

    def write_reply_error(
        self,
        message_id: MessageId,
        *,
        error: str,
        error_type: str,
    ) -> None:
        self.write_reply_bytes(
            message_id, cbor2.dumps({"error": error, "type": error_type})
        )

    def read_reply(self, message_id: MessageId) -> Packet:
        """Wait to receive a reply to ``message_id``, and return it."""
        while message_id not in self.replies:
            self._receive_one()
        return self.replies.pop(message_id)

    def read_request(self) -> Packet:
        """Wait to receive a request, and return it."""
        while not self.requests:
            self._receive_one()
        return self.requests.popleft()


class ClientConnection:
    """Client-side multiplexed socket connection to a Hegel server."""

    def __init__(self, socket: socket.socket):
        self.channels: dict[ChannelId, ClientChannel] = {}
        self.running = True

        self._pending_packets: defaultdict[ChannelId, deque[Packet]] = defaultdict(
            deque
        )
        self._socket = socket
        self._next_channel_id = 1

        # special channel for connection-level commands
        self.control_channel = self._make_channel(ChannelId(0))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def receive_packet_for_channel(self, channel_id: ChannelId) -> Packet:
        """Read packets from the socket until one for ``channel_id`` arrives.

        Packets for other channels are stashed in per-channel pending queues.
        Close-channel packets mark the target channel as closed.
        """
        # Check pending first
        pending = self._pending_packets.get(channel_id)
        if pending:
            return pending.popleft()

        # Read from socket until we get one for our channel
        while True:
            try:
                packet = read_packet(self._socket)
            except (OSError, ProtocolError, AssertionError):
                self.running = False
                raise ConnectionError("Connection closed") from None

            if packet.payload == CLOSE_CHANNEL_PAYLOAD:
                assert packet.message_id == CLOSE_CHANNEL_MESSAGE_ID
                channel = self.channels[packet.channel_id]
                channel.closed = True
                continue

            if packet.channel_id == channel_id:
                return packet

            # Stash for another channel
            self._pending_packets[packet.channel_id].append(packet)

    def write_packet(self, packet: Packet) -> None:
        """Write a packet to the socket."""
        write_packet(self._socket, packet)

    def close(self) -> None:
        """Close the connection."""
        if not self.running:
            return

        self.running = False
        with contextlib.suppress(OSError):
            self._socket.shutdown(socket.SHUT_RDWR)
        self._socket.close()

    def send_handshake(self) -> str:
        """Initiate handshake as a client. Returns the server protocol version."""
        packet = self.control_channel.write_request(HANDSHAKE_STRING)
        reply = self.control_channel.read_reply(packet.message_id)
        payload = reply.payload.decode("utf-8")
        assert payload.startswith("Hegel/")
        return payload.removeprefix("Hegel/")

    def _make_channel(self, channel_id: ChannelId) -> ClientChannel:
        """Create and register a channel."""
        channel = ClientChannel(connection=self, channel_id=channel_id)
        self.channels[channel.channel_id] = channel
        return channel

    def new_channel(self) -> ClientChannel:
        """Create a new logical channel on this connection (odd IDs for client)."""
        channel_id = ChannelId((self._next_channel_id << 1) | 1)
        self._next_channel_id += 1
        return self._make_channel(channel_id)

    def connect_channel(self, channel_id: ChannelId) -> ClientChannel:
        """Connect to a channel created by the server (even IDs)."""
        assert channel_id not in self.channels
        assert channel_id & 1 == 0  # server channels are even
        return self._make_channel(channel_id)
