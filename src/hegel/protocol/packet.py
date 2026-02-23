import socket
import struct
import zlib
from dataclasses import dataclass

from hegel.protocol.utils import ChannelId, MessageId, ProtocolError

# 5 unsigned 32-bit integers, big-endian:
# magic cookie, checksum, channel, message ID, payload length
PACKET_HEADER_FORMAT = ">5I"
# defined as HEGL in hex
PACKET_MAGIC = 0x4845474C
PACKET_TERMINATOR = 0x0A  # '\n'

# Special payload that is sent on a channel when it is shut down. The shutdown
# is not acked and is handled specifially.
# Chosen to be invalid CBOR as per https://www.rfc-editor.org/rfc/rfc8949.html
# It is currently also not the prefix of any valid CBOR (this is a reserved)
# tag byte) but even if it became valid in future this would not be a problem.
CLOSE_CHANNEL_PAYLOAD = bytes([0b11111110])
CLOSE_CHANNEL_MESSAGE_ID = MessageId((1 << 31) - 1)

# If this is set in the message id, this packet is a reply to a previous packet
REPLY_BIT = 1 << 31


@dataclass(frozen=True, slots=True)
class Packet:
    """A message sent to (or read from) a socket."""

    channel_id: ChannelId
    message_id: MessageId
    is_reply: bool
    payload: bytes


def read_exact(sock: socket.socket, *, n: int) -> bytes:
    """Reads exactly n bytes from the socket."""
    assert n >= 0
    if n == 0:
        return b""

    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if chunk:
            data.extend(chunk)
            continue

        raise ProtocolError(
            f"Connection closed during socket read (bytes read so far: {data!r})"
        )
    return bytes(data)


def read_packet(sock: socket.socket, *, timeout: float | None = None) -> Packet:
    """Reads a packet from the socket."""
    sock.settimeout(timeout)
    header = read_exact(sock, n=struct.calcsize(PACKET_HEADER_FORMAT))
    sock.settimeout(None)
    magic, checksum, channel, message_id, length = struct.unpack(PACKET_HEADER_FORMAT, header)
    assert magic == PACKET_MAGIC

    is_reply = (message_id & REPLY_BIT) != 0
    if is_reply:
        message_id ^= REPLY_BIT

    payload = read_exact(sock, n=length)
    terminator = read_exact(sock, n=1)[0]
    assert terminator == PACKET_TERMINATOR

    # checksum is defined as crc(header + payload), where the header's checksum has
    # been zeroed
    zeroed_header = header[:4] + b"\x00\x00\x00\x00" + header[8:]
    assert zlib.crc32(zeroed_header + payload) == checksum

    return Packet(
        channel_id=channel,
        message_id=message_id,
        payload=payload,
        is_reply=is_reply,
    )


def write_packet(sock: socket.socket, packet: Packet) -> None:
    """Writes a packet to the socket."""
    message_id: int = packet.message_id
    if packet.is_reply:
        message_id |= REPLY_BIT

    # checksum is defined as crc(header + payload), where the header's checksum has
    # been zeroed
    zeroed_header = struct.pack(
        ">5I", PACKET_MAGIC, 0, packet.channel_id, message_id, len(packet.payload)
    )
    checksum = zlib.crc32(zeroed_header + packet.payload)

    zeroed_header = struct.pack(
        ">5I", PACKET_MAGIC, checksum, packet.channel_id, message_id, len(packet.payload)
    )
    sock.sendall(zeroed_header + packet.payload + bytes([PACKET_TERMINATOR]))
