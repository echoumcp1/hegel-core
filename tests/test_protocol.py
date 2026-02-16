import socket
from threading import Thread

import pytest
from client import (
    assume,
    generate_from_schema as draw,
    target,
)
from hypothesis import given, strategies as st

from hegel.protocol import Connection, Packet, read_packet, write_packet


@given(
    st.builds(
        Packet,
        message_id=st.integers(0, 1 << 31 - 1),
        channel_id=st.integers(0, 1 << 32 - 1),
        is_reply=st.booleans(),
        payload=st.binary(),
    ),
)
def test_roundtrip_packets(packet):
    reader, writer = socket.socketpair()
    try:
        write_packet(writer, packet)
        roundtripped = read_packet(reader)

        assert roundtripped == packet
    finally:
        reader.close()
        writer.close()


def test_request_handling():
    def add_server(connection):
        connection.receive_handshake()
        # Server creates a channel (even id since server is not client)
        handler_channel = connection.new_channel(role="Handler")

        @handler_channel.handle_requests
        def _(message):
            return {"sum": message["x"] + message["y"]}

    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=add_server,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    try:
        thread.start()
        client_connection = Connection(client_socket, name="Client")
        client_connection.send_handshake()

        # Server creates channel with id=2 (first non-control,
        # __next_channel_id=1, id = (1 << 1) | 0 = 2)
        send_channel = client_connection.connect_channel(2)
        assert send_channel.request({"x": 2, "y": 3}).get() == {"sum": 5}
    finally:
        client_connection.close()


def test_simple_passing_test(client):
    """Test that a simple passing property test works."""

    def my_test():
        x = draw({"type": "integer", "minimum": 0, "maximum": 100})
        assert x >= 0
        assert x <= 100

    # Passing test completes without error
    client.run_test("test_simple", my_test, test_cases=50)


def test_failing_test_with_shrinking(client):
    """Test that a failing test is detected and raises."""

    def my_test():
        x = draw({"type": "integer", "minimum": 0, "maximum": 1000})
        # This will fail for x > 10
        assert x <= 10

    with pytest.raises(AssertionError):
        client.run_test("test_fail", my_test, test_cases=100)


def test_assume_causes_invalid(client):
    """Test that assume(False) marks test cases as invalid."""

    def my_test():
        x = draw({"type": "integer", "minimum": 0, "maximum": 100})
        # Reject odd numbers
        assume(x % 2 == 0)
        # Only even numbers should get here
        assert x % 2 == 0

    # Should pass - assume filters invalid cases
    client.run_test("test_assume", my_test, test_cases=100)


def test_multiple_tests_same_connection(client):
    """Test running multiple tests on the same connection."""

    def test1():
        x = draw({"type": "integer"})
        assert isinstance(x, int)

    def test2():
        s = draw({"type": "string", "min_size": 0, "max_size": 10})
        assert isinstance(s, str)

    client.run_test("test1", test1, test_cases=20)
    client.run_test("test2", test2, test_cases=20)


def test_target_observations(client):
    """Test that target() records observations."""

    def my_test():
        x = draw({"type": "integer", "minimum": 0, "maximum": 100})
        # Guide toward larger values
        target(float(x), "size")
        assert x >= 0

    client.run_test("test_target", my_test, test_cases=50)
