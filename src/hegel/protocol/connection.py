from __future__ import annotations

import contextlib
import os
import socket
import sys
from collections.abc import Callable
from enum import Enum
from threading import Lock
from time import sleep
from typing import TYPE_CHECKING, Any

import cbor2

from hegel.protocol.packet import (
    CLOSE_CHANNEL_MESSAGE_ID,
    CLOSE_CHANNEL_PAYLOAD,
    Packet,
    read_packet,
    write_packet,
)
from hegel.protocol.utils import SHUTDOWN, ChannelId

if TYPE_CHECKING:
    from hegel.protocol.channel import Channel

PROTOCOL_VERSION = 0.2
HANDSHAKE_STRING = b"hegel_handshake_start"


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


class ConnectionState(Enum):
    UNRESOLVED = 0
    CLIENT = 1
    SERVER = 2


class Connection:
    """Thread-safe multiplexed socket connection to a Hegel peer."""

    def __init__(
        self,
        socket: socket.socket,
        *,
        name: str | None = None,
        debug: bool | None = None,
    ):
        self.name = name
        self._debug = _is_protocol_debug() if debug is None else debug

        self.channels: dict[ChannelId, Channel] = {}
        self.running = True

        self.__writer_lock = Lock()
        self.__reader_lock = Lock()
        self.__socket = socket
        self.__next_channel_id = 1
        self._state = ConnectionState.UNRESOLVED

        # special channel for connection-level commands
        self.control_channel = self.new_channel(role="Control")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _debug_print(self, *args):
        if not self._debug:
            return

        print(*args, file=sys.stderr)

    def _debug_packet(self, packet: Packet, *, direction: str) -> None:
        if not self._debug:
            return

        try:
            payload_repr: Any = packet.payload.decode("ascii")
        except UnicodeDecodeError:
            try:
                payload_repr = cbor2.loads(packet.payload)
            except Exception:
                payload_repr = packet.payload

        channel = self.channels[packet.channel_id]
        self._debug_print(
            f"[{self.name or '?'}] {direction} ch={channel}"
            f" message_id={packet.message_id}"
            f" {'reply' if packet.is_reply else 'request'}: {payload_repr!r}",
        )

    def run_reader(self, until: Callable[[], bool]) -> None:
        if until():
            return

        acquired = False
        try:
            while True:
                acquired = self.__reader_lock.acquire(blocking=False)
                if acquired:
                    break
                if until():
                    return
                # Very short sleep to avoid busy waiting
                sleep(0.001)

            while self.running and not until():
                try:
                    packet = read_packet(self.__socket, timeout=0.1)
                except TimeoutError:
                    continue

                channel = self.channels[packet.channel_id]
                self._debug_packet(packet, direction="RECEIVE")
                if packet.payload == CLOSE_CHANNEL_PAYLOAD:
                    assert packet.message_id == CLOSE_CHANNEL_MESSAGE_ID
                    self._debug_print(f"Received close for {channel}")
                    channel.closed = True
                    channel.unprocessed_packets.put(SHUTDOWN)
                elif not channel.closed:
                    channel.unprocessed_packets.put(packet)
        finally:
            if acquired:
                self.__reader_lock.release()

    def write_packet(self, packet: Packet) -> None:
        """Write a packet to the socket. Thread-safe."""
        with self.__writer_lock:
            self._debug_packet(packet, direction="SEND")
            write_packet(self.__socket, packet)

    def close(self) -> None:
        """Close the connection and clean up resources."""
        if not self.running:
            return

        self.running = False
        with contextlib.suppress(OSError):
            self.__socket.shutdown(socket.SHUT_RDWR)
        self.__socket.close()

        for v in self.channels.values():
            if not v.closed:
                v.unprocessed_packets.put(SHUTDOWN)

    def send_handshake(self) -> str:
        """Initiate handshake as a client.

        Returns the server protocol version.
        """
        assert self._state is ConnectionState.UNRESOLVED

        self._state = ConnectionState.CLIENT
        packet = self.control_channel.write_request(HANDSHAKE_STRING)
        reply = self.control_channel.read_reply(packet.message_id)
        payload = reply.payload.decode("utf-8")
        assert payload.startswith("Hegel/")
        return payload.removeprefix("Hegel/")

    def receive_handshake(self):
        """Accept handshake as a server."""
        assert self._state is ConnectionState.UNRESOLVED

        self._state = ConnectionState.SERVER
        packet = self.control_channel.read_request()
        assert packet.payload == HANDSHAKE_STRING
        self.control_channel.write_reply_bytes(
            packet.message_id, f"Hegel/{PROTOCOL_VERSION}".encode()
        )

    def new_channel(self, *, role: str | None = None) -> Channel:
        """Create a new logical channel on this connection."""
        from hegel.protocol.channel import Channel

        if not self.channels:
            channel_id = ChannelId(0)
        else:
            assert self._state is not ConnectionState.UNRESOLVED
            channel_id = ChannelId(
                (self.__next_channel_id << 1)
                | int(self._state is ConnectionState.CLIENT)
            )
            self.__next_channel_id += 1

        channel = Channel(connection=self, channel_id=channel_id, role=role)
        with self.__writer_lock:
            self.channels[channel.channel_id] = channel
        return channel

    def connect_channel(
        self, channel_id: ChannelId, *, role: str | None = None
    ) -> Channel:
        """Connect to a channel created by the peer."""
        from hegel.protocol.channel import Channel

        assert self._state is not ConnectionState.UNRESOLVED

        assert channel_id not in self.channels
        assert channel_id & 1 != int(self._state is ConnectionState.CLIENT)

        channel = Channel(connection=self, channel_id=channel_id, role=role)
        with self.__writer_lock:
            self.channels[channel.channel_id] = channel
        return channel
