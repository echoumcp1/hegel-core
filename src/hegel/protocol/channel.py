from __future__ import annotations

from collections import deque
from queue import Empty, SimpleQueue
from time import time
from typing import TYPE_CHECKING, Any

import cbor2

from hegel.protocol.packet import (
    CLOSE_CHANNEL_MESSAGE_ID,
    CLOSE_CHANNEL_PAYLOAD,
    Packet,
)
from hegel.protocol.utils import (
    CHANNEL_TIMEOUT,
    SHUTDOWN,
    ChannelId,
    MessageId,
    RequestError,
)

if TYPE_CHECKING:
    from hegel.protocol.connection import Connection


class PendingRequest:
    """Future-like handle for an in-flight request."""

    def __init__(self, channel: Channel, message_id: MessageId) -> None:
        self.__channel = channel
        self.__message_id = message_id
        self._closed = False

    def get(self) -> Any:
        """Block until reply arrives and return the result."""
        if self._closed:
            raise ValueError("Cannot .get() more than once")
        self._closed = True
        reply = self.__channel.read_reply(self.__message_id)
        payload = cbor2.loads(reply.payload)
        if "error" in payload:
            raise RequestError(payload["error"], error_type=payload["type"])
        return payload["result"]


class Channel:
    def __init__(
        self,
        connection: Connection,
        channel_id: ChannelId,
        role: str | None = None,
    ) -> None:
        assert channel_id > 0 or role == "Control"

        self.connection = connection
        self.channel_id = channel_id
        self.role = role

        self.unprocessed_packets: SimpleQueue[Any] = SimpleQueue()
        self.requests: deque[Packet] = deque()
        self.replies: dict[MessageId, Packet] = {}

        self.next_message_id = MessageId(1)
        self.closed = False

    def __repr__(self):
        if self.role is None and self.connection.name is None:
            return f"Channel {self.channel_id}"
        if self.role is None:
            return f"{self.connection.name} channel [id={self.channel_id}]"
        return f"{self.connection.name} channel [id={self.channel_id}] ({self.role})"

    def close(self):
        """Close this channel. Writes a close channel notification packet to the socket."""
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

    def __read_one_packet(self, timeout: float | None = CHANNEL_TIMEOUT) -> None:
        """Read one packet from the socket."""
        start = time()
        self.connection.run_reader(
            until=lambda: self.closed
            or (timeout is not None and time() - start > timeout)
            or not self.unprocessed_packets.empty()
        )

        try:
            packet = self.unprocessed_packets.get_nowait()
        except Empty:
            if self.closed:
                raise ConnectionError(f"{self!r} is closed") from None
            raise TimeoutError(
                f"Timed out after {timeout}s waiting for a message on {self!r}",
            ) from None
        if packet is SHUTDOWN:
            raise ConnectionError("Connection closed")

        if packet.is_reply:
            assert packet.message_id not in self.replies
            self.replies[packet.message_id] = packet
        else:
            self.requests.append(packet)

    def send_request(self, payload: dict) -> PendingRequest:
        """Send a CBOR request and return a future for the reply."""
        packet = self.write_request(cbor2.dumps(payload))
        return PendingRequest(self, packet.message_id)

    def handle_requests(self, handler, *, until=lambda: False):
        """Process incoming requests until `until` is met."""
        while not until():
            packet = self.read_request(timeout=CHANNEL_TIMEOUT)
            message = cbor2.loads(packet.payload)
            try:
                result = handler(message)
            except BaseException as e:
                self.write_reply_error(
                    packet.message_id,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                if not isinstance(e, Exception):
                    raise
                continue
            self.write_reply(packet.message_id, result)

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

    def write_reply_bytes(self, message_id: MessageId, payload: bytes) -> None:
        """Write a reply packet to the socket."""
        assert isinstance(payload, bytes)
        self.connection.write_packet(
            Packet(
                payload=payload,
                channel_id=self.channel_id,
                is_reply=True,
                message_id=message_id,
            ),
        )

    def read_reply(
        self, message_id: MessageId, *, timeout: float | None = CHANNEL_TIMEOUT
    ) -> Packet:
        """Wait to receive a reply to ``message_id``, and return it."""
        while message_id not in self.replies:
            self.__read_one_packet(timeout=timeout)
        return self.replies.pop(message_id)

    def read_request(self, *, timeout: float | None = CHANNEL_TIMEOUT) -> Packet:
        """Wait to receive a request, and return it."""
        while not self.requests:
            self.__read_one_packet(timeout=timeout)
        return self.requests.popleft()
