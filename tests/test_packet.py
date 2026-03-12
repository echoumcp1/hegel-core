import socket
import struct
import zlib

import pytest
from hypothesis import given, strategies as st

from hegel.protocol import ProtocolError
from hegel.protocol.utils import ConnectionClosedError
from hegel.protocol.packet import (
    PACKET_HEADER_FORMAT,
    PACKET_MAGIC,
    PACKET_TERMINATOR,
    Packet,
    read_exact,
    read_packet,
    write_packet,
)


def packets():
    return st.builds(
        Packet,
        message_id=st.integers(0, 1 << 31 - 1),
        channel_id=st.integers(0, 1 << 32 - 1),
    )


def _make_packet(
    *,
    magic=PACKET_MAGIC,
    checksum=None,
    channel_id=0,
    message_id=1,
    payload=b"payload",
    terminator=PACKET_TERMINATOR,
):
    length = len(payload)
    header_for_check = struct.pack(
        PACKET_HEADER_FORMAT, magic, 0, channel_id, message_id, length
    )
    if checksum is None:
        checksum = zlib.crc32(header_for_check + payload) & 0xFFFFFFFF
    header = struct.pack(
        PACKET_HEADER_FORMAT, magic, checksum, channel_id, message_id, length
    )
    return header + payload + bytes([terminator])


@given(packets())
def test_packet_roundtrip(packet):
    reader, writer = socket.socketpair()
    try:
        write_packet(writer, packet)
        assert read_packet(reader) == packet
    finally:
        reader.close()
        writer.close()


def test_read_exact_connection_closed_with_partial_data(socket_pair):
    reader, writer = socket_pair
    writer.sendall(b"abc")
    writer.close()
    with pytest.raises(ProtocolError, match="Connection closed during socket read"):
        read_exact(reader, n=10)


def test_read_exact_connection_closed_no_data(socket_pair):
    reader, writer = socket_pair
    writer.close()
    with pytest.raises(ConnectionClosedError, match="Connection closed"):
        read_exact(reader, n=10)


def test_read_packet_invalid_magic(socket_pair):
    reader, writer = socket_pair
    raw = _make_packet(magic=0xDEADBEEF)
    writer.sendall(raw)
    with pytest.raises(AssertionError):
        read_packet(reader)


def test_read_packet_invalid_terminator(socket_pair):
    reader, writer = socket_pair
    raw = _make_packet(terminator=0xFF)
    writer.sendall(raw)
    with pytest.raises(AssertionError):
        read_packet(reader)


def test_read_packet_bad_checksum(socket_pair):
    reader, writer = socket_pair
    raw = _make_packet(checksum=0x12345678)
    writer.sendall(raw)
    with pytest.raises(AssertionError):
        read_packet(reader)
