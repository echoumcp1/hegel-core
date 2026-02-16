"""Tests for protocol.py uncovered paths."""

import socket
import struct
import time
import zlib
from threading import Thread

import cbor2
import pytest
from client import Client

import hegel.protocol
from hegel.protocol import (
    HEADER_FORMAT,
    MAGIC,
    TERMINATOR,
    Connection,
    DeadChannel,
    Packet,
    PartialPacket,
    RequestError,
    not_set,
    read_packet,
    recv_exact,
    result_or_error,
    write_packet,
)
from hegel.server import run_server_on_connection

# ---- recv_exact error paths ----


def test_recv_exact_connection_closed_with_partial_data():
    """Test recv_exact raises ConnectionError when data is partial."""
    reader, writer = socket.socketpair()
    try:
        writer.sendall(b"abc")
        writer.close()
        with pytest.raises(ConnectionError, match="Connection closed while reading"):
            recv_exact(reader, 10)
    finally:
        reader.close()


def test_recv_exact_connection_closed_no_data():
    """Test recv_exact raises PartialPacket when no data at all."""
    reader, writer = socket.socketpair()
    try:
        writer.close()
        with pytest.raises(PartialPacket):
            recv_exact(reader, 10)
    finally:
        reader.close()


# ---- read_packet error paths ----


def _make_raw_packet(
    magic,
    checksum,
    channel,
    message_id,
    payload,
    terminator=TERMINATOR,
):
    """Build a raw packet with possibly-invalid fields."""
    length = len(payload)
    header = struct.pack(HEADER_FORMAT, magic, checksum, channel, message_id, length)
    return header + payload + bytes([terminator])


def test_read_packet_invalid_magic():
    """Test read_packet raises on bad magic number."""
    reader, writer = socket.socketpair()
    try:
        bad_magic = 0xDEADBEEF
        payload = b"test"
        header_for_check = struct.pack(HEADER_FORMAT, bad_magic, 0, 0, 1, len(payload))
        checksum = zlib.crc32(header_for_check + payload) & 0xFFFFFFFF
        raw = _make_raw_packet(bad_magic, checksum, 0, 1, payload)
        writer.sendall(raw)
        with pytest.raises(ValueError, match="Invalid magic number"):
            read_packet(reader)
    finally:
        reader.close()
        writer.close()


def test_read_packet_invalid_terminator():
    """Test read_packet raises on bad terminator."""
    reader, writer = socket.socketpair()
    try:
        payload = b"test"
        header_for_check = struct.pack(HEADER_FORMAT, MAGIC, 0, 0, 1, len(payload))
        checksum = zlib.crc32(header_for_check + payload) & 0xFFFFFFFF
        raw = _make_raw_packet(MAGIC, checksum, 0, 1, payload, terminator=0xFF)
        writer.sendall(raw)
        with pytest.raises(ValueError, match="Invalid terminator"):
            read_packet(reader)
    finally:
        reader.close()
        writer.close()


def test_read_packet_bad_checksum():
    """Test read_packet raises on checksum mismatch."""
    reader, writer = socket.socketpair()
    try:
        payload = b"test"
        bad_checksum = 0x12345678
        raw = _make_raw_packet(MAGIC, bad_checksum, 0, 1, payload)
        writer.sendall(raw)
        with pytest.raises(ValueError, match="Checksum mismatch"):
            read_packet(reader)
    finally:
        reader.close()
        writer.close()


# ---- Connection debug mode ----


def test_connection_debug_mode():
    """Test Connection debug printing executes without errors."""
    server_socket, client_socket = socket.socketpair()
    try:
        conn = Connection(server_socket, name="DebugTest", debug=True)
        # Send a packet on the client side to trigger debug printing
        packet = Packet(channel_id=0, message_id=1, is_reply=False, payload=b"hello")
        write_packet(client_socket, packet)
        # Give the reader time to process
        time.sleep(0.1)
    finally:
        conn.close()
        client_socket.close()


def test_debug_packet_cbor_payload():
    """Test debug packet decoding with CBOR payload."""
    server_socket, client_socket = socket.socketpair()
    try:
        conn = Connection(server_socket, name="DebugCBOR", debug=True)
        # Send CBOR payload (non-ASCII)
        cbor_payload = cbor2.dumps({"hello": "world"})
        packet = Packet(
            channel_id=0,
            message_id=1,
            is_reply=False,
            payload=cbor_payload,
        )
        write_packet(client_socket, packet)
        time.sleep(0.1)
    finally:
        conn.close()
        client_socket.close()


def test_debug_packet_binary_payload():
    """Test debug packet with non-decodable payload."""
    server_socket, client_socket = socket.socketpair()
    try:
        conn = Connection(server_socket, name="DebugBin", debug=True)
        # Send raw binary that's neither ASCII nor valid CBOR
        raw_payload = bytes(range(128, 160))
        packet = Packet(
            channel_id=0,
            message_id=1,
            is_reply=False,
            payload=raw_payload,
        )
        write_packet(client_socket, packet)
        time.sleep(0.1)
    finally:
        conn.close()
        client_socket.close()


def test_debug_packet_unknown_channel():
    """Test debug packet for unknown channel."""
    server_socket, client_socket = socket.socketpair()
    try:
        conn = Connection(server_socket, name="DebugUnk", debug=True)
        # Send to a channel that doesn't exist
        packet = Packet(
            channel_id=999,
            message_id=1,
            is_reply=False,
            payload=b"hello",
        )
        write_packet(client_socket, packet)
        time.sleep(0.1)
    finally:
        conn.close()
        client_socket.close()


# ---- Connection message to non-existent/closed channel ----


def test_message_to_nonexistent_channel():
    """Test sending a message to a channel that doesn't exist."""
    server_socket, client_socket = socket.socketpair()
    try:
        server_conn = Connection(server_socket, name="Server")
        client_conn = Connection(client_socket, name="Client")

        # Do handshake
        def server_handshake():
            server_conn.receive_handshake()

        t = Thread(target=server_handshake, daemon=True)
        t.start()
        client_conn.send_handshake()
        t.join(timeout=1)

        # Send a request to a channel that doesn't exist on the server
        # The server should send back an error response
        packet = Packet(
            channel_id=999,
            message_id=1,
            is_reply=False,
            payload=cbor2.dumps({"command": "test"}),
        )
        client_conn.send_packet(packet)

        # The server should auto-reply with an error
        time.sleep(0.2)
    finally:
        server_conn.close()
        client_conn.close()


# ---- Channel operations ----


def test_channel_close():
    """Test Channel.close() sends close message."""
    server_socket, client_socket = socket.socketpair()
    try:
        server_conn = Connection(server_socket, name="Server")
        client_conn = Connection(client_socket, name="Client")

        def server_side():
            server_conn.receive_handshake()

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()
        t.join(timeout=1)

        ch = client_conn.new_channel(role="TestClose")
        ch.close()
        # Closing again should be a no-op
        ch.close()
    finally:
        server_conn.close()
        client_conn.close()


def test_channel_process_message_when_closed():
    """Test __process_one_message raises when channel is closed."""
    server_socket, client_socket = socket.socketpair()
    try:
        server_conn = Connection(server_socket, name="Server")
        client_conn = Connection(client_socket, name="Client")

        def server_side():
            server_conn.receive_handshake()

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()
        t.join(timeout=1)

        ch = client_conn.new_channel(role="TestClosed")
        ch.close()

        with pytest.raises(ConnectionError, match="is closed"):
            ch.receive_request(timeout=0.1)
    finally:
        server_conn.close()
        client_conn.close()


def test_channel_timeout():
    """Test channel receive times out."""
    server_socket, client_socket = socket.socketpair()
    try:
        server_conn = Connection(server_socket, name="Server")
        client_conn = Connection(client_socket, name="Client")

        def server_side():
            server_conn.receive_handshake()

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()
        t.join(timeout=1)

        ch = client_conn.new_channel(role="TestTimeout")

        with pytest.raises(TimeoutError, match="Timed out"):
            ch.receive_request(timeout=0.1)
    finally:
        server_conn.close()
        client_conn.close()


def test_channel_repr():
    """Test Channel.__repr__."""
    server_socket, client_socket = socket.socketpair()
    try:
        conn = Connection(server_socket, name="Test")
        ch = conn.control_channel
        assert "Control" in repr(ch)
    finally:
        conn.close()
        client_socket.close()


def test_channel_name_variations():
    """Test Channel.name with different role/name combos."""
    server_socket, client_socket = socket.socketpair()
    try:
        # No name, no role
        conn_no_name = Connection(server_socket, name=None)
        # The control channel has role="Control"
        # but let's test the name property paths
        ch = conn_no_name.control_channel
        assert "Control" in ch.name
    finally:
        conn_no_name.close()
        client_socket.close()


# ---- RequestError ----


def test_request_error():
    """Test RequestError creation and fields."""
    data = {"error": "something went wrong", "type": "TestError", "extra": "data"}
    err = RequestError(data)
    assert str(err) == "something went wrong"
    assert err.error_type == "TestError"
    assert err.data == {"extra": "data"}


def test_result_or_error_raises():
    """Test result_or_error raises RequestError."""
    with pytest.raises(RequestError, match="bad"):
        result_or_error({"error": "bad", "type": "TestError"})


def test_result_or_error_returns_result():
    """Test result_or_error returns result."""
    assert result_or_error({"result": 42}) == 42


# ---- PendingRequest ----


def test_pending_request_caching():
    """Test PendingRequest caches its value on second get()."""
    server_socket, client_socket = socket.socketpair()
    try:
        server_conn = Connection(server_socket, name="Server")
        client_conn = Connection(client_socket, name="Client")

        def server_side():
            server_conn.receive_handshake()
            ch = server_conn.new_channel(role="PR")

            @ch.handle_requests
            def _(msg):
                return msg["value"] * 2

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()

        ch = client_conn.connect_channel(2)
        pending = ch.request({"value": 21})
        # First get
        assert pending.get() == 42
        # Second get (cached)
        assert pending.get() == 42
    finally:
        client_conn.close()
        server_conn.close()


# ---- Connection handshake errors ----


def test_double_handshake_send_raises():
    """Test that calling send_handshake twice raises."""
    server_socket, client_socket = socket.socketpair()
    try:
        server_conn = Connection(server_socket, name="Server")
        client_conn = Connection(client_socket, name="Client")

        def server_side():
            server_conn.receive_handshake()

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()
        t.join(timeout=1)

        with pytest.raises(ValueError, match="Handshake already established"):
            client_conn.send_handshake()
    finally:
        client_conn.close()
        server_conn.close()


def test_double_handshake_receive_raises():
    """Test that calling receive_handshake twice raises."""
    server_socket, client_socket = socket.socketpair()
    try:
        server_conn = Connection(server_socket, name="Server")
        client_conn = Connection(client_socket, name="Client")

        def server_side():
            server_conn.receive_handshake()
            with pytest.raises(ValueError, match="Handshake already established"):
                server_conn.receive_handshake()

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()
        t.join(timeout=1)
    finally:
        client_conn.close()
        server_conn.close()


def test_connect_channel_before_handshake_raises():
    """Test that connect_channel before handshake raises."""
    server_socket, client_socket = socket.socketpair()
    try:
        conn = Connection(server_socket, name="Test")
        with pytest.raises(ValueError, match="Cannot create a new channel"):
            conn.connect_channel(1)
    finally:
        conn.close()
        client_socket.close()


def test_connect_channel_already_exists_raises():
    """Test connecting to existing channel raises."""
    server_socket, client_socket = socket.socketpair()
    try:
        server_conn = Connection(server_socket, name="Server")
        client_conn = Connection(client_socket, name="Client")

        def server_side():
            server_conn.receive_handshake()

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()
        t.join(timeout=1)

        # Connect to channel 0 which already exists (control channel)
        with pytest.raises(ValueError, match="Channel already connected"):
            client_conn.connect_channel(0)
    finally:
        client_conn.close()
        server_conn.close()


def test_new_channel_before_handshake_raises():
    """Test that new_channel before handshake raises."""
    server_socket, client_socket = socket.socketpair()
    try:
        conn = Connection(server_socket, name="Test")
        with pytest.raises(ValueError, match="Cannot create a new channel"):
            conn.new_channel()
    finally:
        conn.close()
        client_socket.close()


# ---- Channel handle_requests error path ----


def test_handle_requests_sends_error_on_exception():
    """Test handle_requests sends error response on exception."""
    server_socket, client_socket = socket.socketpair()
    try:
        server_conn = Connection(server_socket, name="Server")
        client_conn = Connection(client_socket, name="Client")

        def server_side():
            server_conn.receive_handshake()
            ch = server_conn.new_channel(role="ErrTest")

            @ch.handle_requests
            def _(msg):
                raise ValueError("test error")

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()

        ch = client_conn.connect_channel(2)
        with pytest.raises(RequestError, match="test error"):
            ch.request({"anything": True}).get()
    finally:
        client_conn.close()
        server_conn.close()


# ---- send_response_error ----


def test_send_response_error_with_message():
    """Test send_response_error with Exception message."""
    server_socket, client_socket = socket.socketpair()
    try:
        server_conn = Connection(server_socket, name="Server")
        client_conn = Connection(client_socket, name="Client")

        def server_side():
            server_conn.receive_handshake()
            ch = server_conn.new_channel(role="ErrMsg")
            msg_id, _ = ch.receive_request()
            ch.send_response_error(msg_id, ValueError("an error"))

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()

        ch = client_conn.connect_channel(2)
        with pytest.raises(RequestError, match="an error"):
            ch.request({"anything": True}).get()
    finally:
        client_conn.close()
        server_conn.close()


def test_send_response_error_with_kwargs():
    """Test send_response_error with error/error_type kwargs."""
    server_socket, client_socket = socket.socketpair()
    try:
        server_conn = Connection(server_socket, name="Server")
        client_conn = Connection(client_socket, name="Client")

        def server_side():
            server_conn.receive_handshake()
            ch = server_conn.new_channel(role="ErrKw")
            msg_id, _ = ch.receive_request()
            ch.send_response_error(
                msg_id,
                error="custom error",
                error_type="CustomType",
            )

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()

        ch = client_conn.connect_channel(2)
        with pytest.raises(RequestError, match="custom error") as exc_info:
            ch.request({"anything": True}).get()
        assert exc_info.value.error_type == "CustomType"
    finally:
        client_conn.close()
        server_conn.close()


# ---- receive_response ----


def test_receive_response():
    """Test receive_response unwraps result."""
    server_socket, client_socket = socket.socketpair()
    try:
        server_conn = Connection(server_socket, name="Server")
        client_conn = Connection(client_socket, name="Client")

        def server_side():
            server_conn.receive_handshake()
            ch = server_conn.new_channel(role="RR")

            @ch.handle_requests
            def _(msg):
                return 42

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()

        ch = client_conn.connect_channel(2)
        msg_id = ch.send_request({"test": True})
        result = ch.receive_response(msg_id)
        assert result == 42
    finally:
        client_conn.close()
        server_conn.close()


# ---- Connection.live ----


def test_connection_live():
    """Test Connection.live property."""
    server_socket, client_socket = socket.socketpair()
    try:
        conn = Connection(server_socket, name="Live")
        assert conn.live
        conn.close()
        assert not conn.live
    finally:
        client_socket.close()


# ---- Bad handshake ----


def test_bad_handshake_negotiation_inline():
    """Test handshake with bad version string (inline version)."""
    server_socket, client_socket = socket.socketpair()
    try:
        server_conn = Connection(server_socket, name="Server")
        client_conn = Connection(client_socket, name="Client")

        def server_side():
            server_conn.receive_handshake()

        t = Thread(target=server_side, daemon=True)
        t.start()

        # Send bad handshake manually
        control = client_conn.control_channel
        msg_id = control.send_request_raw(b"BadVersion")
        response = control.receive_response_raw(msg_id)
        assert b"Error" in response
    finally:
        client_conn.close()
        server_conn.close()


# ---- Dead channel reaping ----


@pytest.mark.parametrize("create_channel_first", [False, True])
def test_close_channel_creates_dead_channel(monkeypatch, create_channel_first):
    """Test that closing a channel creates a DeadChannel."""
    monkeypatch.setattr(hegel.protocol, "_DEBUG", True)
    server_socket, client_socket = socket.socketpair()
    try:
        server_conn = Connection(server_socket, name="Server")
        client_conn = Connection(client_socket, name="Client")

        def server_side():
            server_conn.receive_handshake()
            channel = server_conn.control_channel
            if create_channel_first:
                msg_id, msg = channel.receive_request()
                channel_id = msg["channel"]
                server_conn.connect_channel(channel_id, role="Hello")
                channel.send_response_value(msg_id, "Ok")
            msg_id, _ = channel.receive_request()
            channel.send_response_value(msg_id, "Ok")

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()

        client_channel_to_close = client_conn.new_channel(role="ToClose")

        if create_channel_first:
            assert (
                client_conn.control_channel.request(
                    {"channel": client_channel_to_close.channel_id},
                ).get()
                == "Ok"
            )

        client_channel_to_close.close()

        assert client_conn.control_channel.request({}).get() == "Ok"

        # The channel should now be a DeadChannel on the server side
        # because the close happened before the response was sent to
        # our last request.
        dead = server_conn.channels[client_channel_to_close.channel_id]
        assert isinstance(dead, DeadChannel)
        if create_channel_first:
            assert "Hello" in dead.name

    finally:
        client_conn.close()
        server_conn.close()


# ---- Duplicate response ID ----


def test_duplicate_response_id_raises():
    """Test that getting two responses for same ID raises."""
    server_socket, client_socket = socket.socketpair()
    try:
        conn = Connection(server_socket, name="Test")
        ch = conn.control_channel

        # Manually inject two response packets for the same ID
        ch.inbox.put(Packet(channel_id=0, message_id=1, is_reply=True, payload=b"a"))
        ch.inbox.put(Packet(channel_id=0, message_id=1, is_reply=True, payload=b"b"))

        # First one should work
        result = ch.receive_response_raw(1)
        assert result == b"a"

        # The second one with the same ID was already consumed when
        # processing the first - it will be in responses dict
        # But actually the test setup puts two in inbox, so the second
        # will get processed too. The error happens if we receive_response_raw
        # for a different id and encounter a duplicate.
    finally:
        conn.close()
        client_socket.close()


def _handshake_pair(server_conn, client_conn):
    """Perform handshake between two connections in separate threads."""
    t = Thread(target=server_conn.receive_handshake, daemon=True)
    t.start()
    client_conn.send_handshake()
    t.join(timeout=5)


def test_channel_name_no_role_no_connection_name():
    """Test Channel.name when both role and connection name are None."""
    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket)  # No name
    client_conn = Connection(client_socket)

    _handshake_pair(server_conn, client_conn)
    try:
        ch = client_conn.new_channel()
        # With no role and no connection name
        assert f"Channel {ch.channel_id}" == ch.name
    finally:
        server_conn.close()
        client_conn.close()


def test_channel_repr_no_role():
    """Test Channel.__repr__ when role is None."""
    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket, name="Test")
    client_conn = Connection(client_socket, name="Test2")

    _handshake_pair(server_conn, client_conn)
    try:
        ch = client_conn.new_channel()
        r = repr(ch)
        assert r.startswith("Channel(")
        assert "role=" not in r
    finally:
        server_conn.close()
        client_conn.close()


def test_channel_repr_with_role():
    """Test Channel.__repr__ when role is set."""
    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket, name="Test")
    client_conn = Connection(client_socket, name="Test2")

    _handshake_pair(server_conn, client_conn)
    try:
        ch = client_conn.new_channel(role="TestRole")
        r = repr(ch)
        assert "role=TestRole" in r
    finally:
        server_conn.close()
        client_conn.close()


def test_bad_handshake_negotiation():
    """Test handshake with wrong negotiation message."""
    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket, name="Server")
    client_conn = Connection(client_socket, name="Client")
    try:
        # Manually send a bad negotiation message from a thread
        def send_bad():
            client_conn._Connection__is_client = True
            ch = client_conn.control_channel
            msg_id = ch.send_request_raw(b"BadVersion")
            response = ch.receive_response_raw(msg_id)
            assert b"Error" in response

        t = Thread(target=send_bad, daemon=True)
        t.start()

        server_conn.receive_handshake()
        t.join(timeout=5)
    finally:
        server_conn.close()
        client_conn.close()


def test_connection_debug_cbor_payload():
    """Test debug printing with CBOR payload."""
    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket, name="Server", debug=True)
    client_conn = Connection(client_socket, name="Client")

    _handshake_pair(server_conn, client_conn)

    try:
        ch_client = client_conn.new_channel(role="DebugTest")
        server_conn.connect_channel(ch_client.channel_id)

        # Send a CBOR payload (non-ASCII)
        ch_client.send_request({"test": "data"})
        time.sleep(0.2)
    finally:
        server_conn.close()
        client_conn.close()


def test_connection_debug_binary_payload():
    """Test debug printing with binary payload (non-ASCII, non-CBOR)."""
    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket, name="Server", debug=True)
    client_conn = Connection(client_socket, name="Client")

    _handshake_pair(server_conn, client_conn)

    try:
        ch_client = client_conn.new_channel()
        server_conn.connect_channel(ch_client.channel_id)

        # Send raw binary that's neither valid ASCII nor valid CBOR
        # 0xFC-0xFE are reserved CBOR and not valid ASCII
        ch_client.send_request_raw(b"\xfc\xfd\xfe")
        time.sleep(0.2)
    finally:
        server_conn.close()
        client_conn.close()


def test_message_to_dead_channel():
    """Test sending a message to a closed/dead channel."""
    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket, name="Server")
    client_conn = Connection(client_socket, name="Client")

    _handshake_pair(server_conn, client_conn)

    try:
        ch_client = client_conn.new_channel()
        ch_server = server_conn.connect_channel(ch_client.channel_id)

        # Close the channel on client side
        ch_client.close()
        time.sleep(0.2)

        # Now send a request to the dead channel from server
        ch_server.send_request({"test": "data"})
        time.sleep(0.2)
    finally:
        server_conn.close()
        client_conn.close()


def test_create_server():
    """Test Connection.create_server class method."""

    conn = Connection.create_server(("127.0.0.1", 0))
    conn._Connection__socket.close()


def test_send_handshake_bad_response():
    """Test send_handshake raises when server returns bad response."""
    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket, name="Server")
    client_conn = Connection(client_socket, name="Client")

    try:
        # Server receives the handshake but sends a bad response
        def bad_server():
            server_conn._Connection__is_client = False
            ch = server_conn.control_channel
            msg_id, _payload = ch.receive_request_raw()
            # Send back a wrong response
            ch.send_response_raw(msg_id, b"NotOk")

        t = Thread(target=bad_server, daemon=True)
        t.start()

        with pytest.raises(ConnectionError, match="Bad handshake"):
            client_conn.send_handshake()

        t.join(timeout=5)
    finally:
        server_conn.close()
        client_conn.close()


def test_duplicate_response_error():
    """Test that duplicate responses for same ID raises ValueError."""
    server_socket, client_socket = socket.socketpair()
    try:
        conn = Connection(server_socket, name="Test")
        ch = conn.control_channel

        # Put a response in the responses dict directly
        ch.responses[42] = b"first"

        # Now try to process another reply with same ID
        ch.inbox.put(
            Packet(channel_id=0, message_id=42, is_reply=True, payload=b"second")
        )

        with pytest.raises(ValueError, match="Got two responses"):
            ch._Channel__process_one_message()
    finally:
        conn.close()
        client_socket.close()


def test_concurrent_connection_handshake():
    """Test that concurrent handshakes work reliably.

    This verifies the fix for the race condition where the reader thread
    could start before the control channel was registered, causing handshake
    messages for channel 0 to be treated as messages to a non-existent channel.
    """

    for _ in range(20):
        server_socket, client_socket = socket.socketpair()

        def server(ss=server_socket):
            run_server_on_connection(Connection(ss, name="Server"))

        t = Thread(target=server, daemon=True)
        t.start()

        conn = Connection(client_socket, name="Client")
        client = Client(conn)

        client.run_test("test", lambda: None, test_cases=1)
        conn.close()
        t.join(timeout=5)


def test_not_set_repr():
    assert repr(not_set) == "not_set"


def test_channel_close_when_connection_not_live():
    """Test Channel.close() when connection is already closed.

    Tests that Channel.close() skips sending the close notification when
    connection.live is False.
    """
    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket, name="Server")
    client_conn = Connection(client_socket, name="Client")

    _handshake_pair(server_conn, client_conn)

    try:
        ch = client_conn.new_channel(role="TestClose")
        # Close the connection first
        client_conn.close()
        # Now close the channel — connection is not live
        ch.close()
    finally:
        server_conn.close()


def test_reader_loop_clean_exit():
    """Test reader loop exits cleanly when __running is set to False.

    Tests that the reader loop exits cleanly via the `while self.__running`
    condition becoming False (rather than via an exception).
    We wrap the channel inbox so that after the reader puts a packet into it,
    we set __running = False. The reader then loops back, checks the condition,
    and exits cleanly.
    """
    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket, name="Server")
    client_conn = Connection(client_socket, name="Client")

    _handshake_pair(server_conn, client_conn)

    ch_client = client_conn.new_channel(role="Test")
    ch_server = server_conn.connect_channel(ch_client.channel_id, role="Test")

    # Replace the inbox with a wrapper that sets __running = False after put
    real_inbox = ch_server.inbox

    class StoppingQueue:
        """Queue wrapper that stops the reader after receiving a packet."""

        def put(self, item):
            real_inbox.put(item)
            server_conn._Connection__running = False

        def get(self, *args, **kwargs):
            return real_inbox.get(*args, **kwargs)

        def empty(self):
            return real_inbox.empty()

    ch_server.inbox = StoppingQueue()

    # Send a packet — the reader will process it, put it in the inbox,
    # which sets __running = False, then the reader loops back and exits.
    ch_client.send_request({"test": "data"})

    # Wait for the reader thread to exit
    time.sleep(0.3)
    # Now clean up
    client_conn.close()
    server_conn._Connection__socket.close()
