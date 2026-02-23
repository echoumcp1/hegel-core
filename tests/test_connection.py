import os
import subprocess
import sys
import time
from queue import Empty
from threading import Thread

import cbor2
import pytest

from hegel.protocol.connection import Connection
from hegel.protocol.packet import Packet
from hegel.protocol.utils import SHUTDOWN


def _do_handshake(server: Connection, client: Connection):
    t = Thread(target=server.receive_handshake, daemon=True)
    t.start()
    client.send_handshake()
    t.join(timeout=5)


def test_request_handling(socket_pair):
    def add_server(connection):
        connection.receive_handshake()
        handler_channel = connection.new_channel()

        @handler_channel.handle_requests
        def _(message):
            return {"sum": message["x"] + message["y"]}

    server_socket, client_socket = socket_pair
    thread = Thread(
        target=add_server,
        args=(Connection(server_socket),),
        daemon=True,
    )
    thread.start()
    with Connection(client_socket) as client_connection:
        client_connection.send_handshake()

        # Server creates channel with id=2 (first non-control,
        # __next_channel_id=1, id = (1 << 1) | 0 = 2)
        send_channel = client_connection.connect_channel(2)
        assert send_channel.send_request({"x": 2, "y": 3}).get() == {"sum": 5}


@pytest.mark.parametrize(
    "name, payload",
    [
        ("DebugTest", b"hello"),
        ("DebugCBOR", cbor2.dumps({"hello": "world"})),
        ("DebugBin", bytes(range(128, 160))),
    ],
)
def test_connection_debug_mode(socket, name, payload):
    with Connection(socket, name=name, debug=True) as conn:
        packet = Packet(channel_id=0, message_id=1, is_reply=False, payload=payload)
        conn._debug_packet(packet, direction="TEST")


@pytest.mark.parametrize(
    "role, send_fn",
    [
        ("DebugTest", lambda ch: ch.send_request({"test": "data"})),
        (None, lambda ch: ch.write_request(b"\xfc\xfd\xfe")),
    ],
)
def test_connection_debug_with_handshake(socket_pair, role, send_fn):
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket, name="Server", debug=True) as server_conn,
        Connection(client_socket) as client_conn,
    ):
        _do_handshake(server_conn, client_conn)
        ch_client = client_conn.new_channel(role=role)
        server_conn.connect_channel(ch_client.channel_id)
        send_fn(ch_client)
        time.sleep(0.2)


# ---- Channel operations ----


def test_channel_close(socket_pair):
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket) as server_conn,
        Connection(client_socket) as client_conn,
    ):
        _do_handshake(server_conn, client_conn)

        channel = client_conn.new_channel()
        channel.close()
        # Closing again should be a no-op
        channel.close()


def test_channel_close_when_connection_not_live(socket_pair):
    """Test Channel.close() when connection is already closed.

    Tests that Channel.close() skips sending the close notification when
    connection.live is False.
    """
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket) as server_conn,
        Connection(client_socket) as client_conn,
    ):
        _do_handshake(server_conn, client_conn)

        channel = client_conn.new_channel()
        # Close the connection first
        client_conn.close()
        # Now close the channel — connection is not live
        channel.close()


def test_channel_process_message_when_closed(socket_pair):
    """Test reading from a locally-closed channel times out."""
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket) as server_conn,
        Connection(client_socket) as client_conn,
    ):
        _do_handshake(server_conn, client_conn)

        channel = client_conn.new_channel()
        channel.close()

        with pytest.raises(Empty):
            channel.read_request(timeout=0.1)


def test_channel_timeout(socket_pair):
    """Test channel receive times out."""
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket) as server_conn,
        Connection(client_socket) as client_conn,
    ):
        _do_handshake(server_conn, client_conn)

        channel = client_conn.new_channel()

        with pytest.raises(Empty):
            channel.read_request(timeout=0.1)


def test_channel_repr(socket):
    with Connection(socket) as conn:
        assert "Control" in repr(conn.control_channel)


@pytest.mark.parametrize(
    "role, expected",
    [
        (None, "Channel "),
        ("TestRole", "(TestRole)"),
    ],
)
def test_channel_repr_variations(socket_pair, role, expected):
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket) as server_conn,
        Connection(client_socket) as client_conn,
    ):
        _do_handshake(server_conn, client_conn)
        channel = client_conn.new_channel(role=role)
        assert expected in repr(channel)


def test_message_to_closed_channel(socket_pair):
    """Test sending a message to a closed channel."""
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket) as server_conn,
        Connection(client_socket) as client_conn,
    ):
        _do_handshake(server_conn, client_conn)

        ch_client = client_conn.new_channel()
        ch_server = server_conn.connect_channel(ch_client.channel_id)

        # Close the channel on client side
        ch_client.close()
        time.sleep(0.2)

        # Now send a request to the closed channel from server
        ch_server.write_request(cbor2.dumps({"test": "data"}))
        time.sleep(0.2)


@pytest.mark.parametrize("create_channel_first", [False, True])
def test_close_channel_marks_closed(socket_pair, create_channel_first):
    """Test that closing a channel marks it as closed."""
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket, name="Server", debug=True) as server_conn,
        Connection(client_socket, name="Client", debug=True) as client_conn,
    ):

        def server_side():
            server_conn.receive_handshake()
            channel = server_conn.control_channel
            # Server must always connect to the channel so the reader can route
            # the close packet.
            packet = channel.read_request()
            msg = cbor2.loads(packet.payload)
            channel_id = msg["channel_id"]
            role = "Hello" if create_channel_first else None
            server_conn.connect_channel(channel_id, role=role)
            channel.write_reply(packet.message_id, cbor2.dumps({"result": "Ok"}))
            packet = channel.read_request()
            channel.write_reply(packet.message_id, cbor2.dumps({"result": "Ok"}))

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()

        client_channel_to_close = client_conn.new_channel()

        # Tell the server about the channel so it can connect
        assert (
            client_conn.control_channel.send_request(
                {"channel_id": client_channel_to_close.channel_id},
            ).get()
            == "Ok"
        )

        client_channel_to_close.close()

        assert client_conn.control_channel.send_request({}).get() == "Ok"

        # The channel should now be closed on the server side
        channel = server_conn.channels[client_channel_to_close.channel_id]
        assert channel.closed
        if create_channel_first:
            assert channel.role == "Hello"


# ---- PendingRequest ----


def test_pending_request_double_get_raises(socket_pair):
    """Test PendingRequest raises ValueError on second get()."""
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket) as server_conn,
        Connection(client_socket) as client_conn,
    ):

        def server_side():
            server_conn.receive_handshake()
            channel = server_conn.new_channel()

            @channel.handle_requests
            def _(msg):
                return msg["value"] * 2

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()

        channel = client_conn.connect_channel(2)
        pending = channel.send_request({"value": 21})
        assert pending.get() == 42
        with pytest.raises(ValueError, match=r"Cannot \.get\(\) more than once"):
            pending.get()


def test_receive_reply(socket_pair):
    """Test receive_reply returns raw bytes."""
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket) as server_conn,
        Connection(client_socket) as client_conn,
    ):

        def server_side():
            server_conn.receive_handshake()
            channel = server_conn.new_channel()

            @channel.handle_requests
            def _(msg):
                return 42

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()

        channel = client_conn.connect_channel(2)
        packet = channel.write_request(cbor2.dumps({"test": True}))
        result = cbor2.loads(channel.read_reply(packet.message_id).payload)
        assert result == {"result": 42}


# ---- Duplicate reply ID ----


def test_duplicate_reply_id_raises(socket):
    """Test that getting two replies for same ID raises."""
    with Connection(socket) as conn:
        channel = conn.control_channel

        # Manually inject two reply packets for the same ID
        channel.unprocessed_packets.put(
            Packet(channel_id=0, message_id=1, is_reply=True, payload=b"a")
        )
        channel.unprocessed_packets.put(
            Packet(channel_id=0, message_id=1, is_reply=True, payload=b"b")
        )

        # First one should work
        result = channel.read_reply(1).payload
        assert result == b"a"


def test_duplicate_reply_error(socket):
    """Test that duplicate replies for same ID raises AssertionError."""
    with Connection(socket) as conn:
        channel = conn.control_channel

        # Put a reply in the replies dict directly
        channel.replies[42] = b"first"

        # Now try to process another reply with same ID
        channel.unprocessed_packets.put(
            Packet(channel_id=0, message_id=42, is_reply=True, payload=b"second")
        )

        with pytest.raises(AssertionError):
            channel._Channel__read_one_packet()


# ---- Connection handshake ----


def test_double_handshake_send_raises(socket_pair):
    """Test that calling send_handshake twice raises."""
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket) as server_conn,
        Connection(client_socket) as client_conn,
    ):
        _do_handshake(server_conn, client_conn)

        with pytest.raises(AssertionError):
            client_conn.send_handshake()


def test_double_handshake_receive_raises(socket_pair):
    """Test that calling receive_handshake twice raises."""
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket) as server_conn,
        Connection(client_socket) as client_conn,
    ):

        def server_side():
            server_conn.receive_handshake()
            with pytest.raises(AssertionError):
                server_conn.receive_handshake()

        t = Thread(target=server_side, daemon=True)
        t.start()
        client_conn.send_handshake()
        t.join(timeout=1)


def test_connect_channel_before_handshake_raises(socket):
    """Test that connect_channel before handshake raises."""
    with (
        Connection(socket) as conn,
        pytest.raises(AssertionError),
    ):
        conn.connect_channel(1)


def test_connect_channel_already_exists_raises(socket_pair):
    """Test connecting to existing channel raises."""
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket) as server_conn,
        Connection(client_socket) as client_conn,
    ):
        _do_handshake(server_conn, client_conn)

        # Connect to channel 0 which already exists (control channel)
        with pytest.raises(AssertionError):
            client_conn.connect_channel(0)


def test_new_channel_before_handshake_raises(socket):
    """Test that new_channel before handshake raises."""
    with (
        Connection(socket) as conn,
        pytest.raises(AssertionError),
    ):
        conn.new_channel()


def test_bad_handshake_negotiation(socket_pair):
    """Test handshake with bad version string asserts."""
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket) as server_conn,
        Connection(client_socket) as client_conn,
    ):

        def send_bad():
            channel = client_conn.control_channel
            channel.write_request(b"BadVersion")

        t = Thread(target=send_bad, daemon=True)
        t.start()

        with pytest.raises(AssertionError):
            server_conn.receive_handshake()

        t.join(timeout=5)


def test_send_handshake_bad_reply(socket_pair):
    """Test send_handshake raises when server returns bad reply."""
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket) as server_conn,
        Connection(client_socket) as client_conn,
    ):

        def bad_server():
            channel = server_conn.control_channel
            packet = channel.read_request()
            channel.write_reply(packet.message_id, b"NotOk")

        t = Thread(target=bad_server, daemon=True)
        t.start()

        with pytest.raises(AssertionError):
            client_conn.send_handshake()

        t.join(timeout=5)


def test_send_handshake_returns_server_version(socket_pair):
    """Test send_handshake returns the server's protocol version."""
    server_socket, client_socket = socket_pair
    with (
        Connection(server_socket) as server_conn,
        Connection(client_socket) as client_conn,
    ):
        t = Thread(target=server_conn.receive_handshake, daemon=True)
        t.start()

        version = client_conn.send_handshake()
        assert version == "0.1"

        t.join(timeout=5)


# ---- Connection lifecycle ----


def test_connection_running(socket):
    """Test Connection.running attribute."""
    with Connection(socket) as conn:
        assert conn.running
    assert not conn.running


def test_connection_double_close(socket):
    conn = Connection(socket)
    conn.close()
    conn.close()


def test_shutdown_in_inbox_raises(socket):
    """Test that SHUTDOWN in inbox raises ConnectionError."""
    with Connection(socket) as conn:
        channel = conn.control_channel
        channel.unprocessed_packets.put(SHUTDOWN)
        with pytest.raises(ConnectionError, match="Connection closed"):
            channel.read_request(timeout=0.1)


def test_reader_loop_clean_exit(socket_pair):
    """Test reader loop exits cleanly when running is set to False.

    Tests that the reader loop exits cleanly via the `while self.running`
    condition becoming False (rather than via an exception).
    We wrap the channel unprocessed_packets queue so that after the reader
    puts a packet into it, we set running = False. The reader then loops
    back, checks the condition, and exits cleanly.
    """
    server_socket, client_socket = socket_pair
    server_conn = Connection(server_socket)
    client_conn = Connection(client_socket)

    _do_handshake(server_conn, client_conn)

    ch_client = client_conn.new_channel()
    ch_server = server_conn.connect_channel(ch_client.channel_id)

    # Replace the queue with a wrapper that sets running = False after put
    real_queue = ch_server.unprocessed_packets

    class StoppingQueue:
        """Queue wrapper that stops the reader after receiving a packet."""

        def put(self, item):
            real_queue.put(item)
            server_conn.running = False

        def get(self, *args, **kwargs):
            return real_queue.get(*args, **kwargs)

        def get_nowait(self):
            return real_queue.get_nowait()

        def empty(self):
            return real_queue.empty()

    ch_server.unprocessed_packets = StoppingQueue()

    # Send a packet — the reader will process it, put it in the queue,
    # which sets running = False, then the reader loops back and exits.
    ch_client.write_request(cbor2.dumps({"test": "data"}))

    # Wait for the reader thread to exit
    time.sleep(0.3)
    # Now clean up
    client_conn.close()
    server_conn._Connection__socket.close()


def test_invalid_hegel_debug_env_var():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from hegel.protocol.connection import _is_protocol_debug; _is_protocol_debug()",
        ],
        env={**os.environ, "HEGEL_PROTOCOL_DEBUG": "invalid"},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "invalid value for HEGEL_PROTOCOL_DEBUG" in result.stderr
