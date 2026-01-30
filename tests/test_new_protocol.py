import socket
from threading import Thread

import pytest
from hypothesis import given, settings, strategies as st

from hegel.hegeld import run_server_on_connection
from hegel.protocol import Connection, Packet, read_packet, write_packet
from hegel.sdk import (
    Client,
    assume,
    booleans,
    generate_from_schema as draw,
    integers,
    lists,
    target,
    text,
)


@settings(max_examples=1000)
@given(
    st.builds(
        Packet,
        message_id=st.integers(0, 1 << 31 - 1),
        channel=st.integers(0, 1 << 32 - 1),
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


def test_basic_connection_can_negotiate_version_without_error():
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    try:
        thread.start()
        client_connection = Connection(client_socket, name="Client")

        # Creating the client does version negotiation, and will error
        # if that doesn't work, but we don't actually test any follow
        # on messages here.
        Client(client_connection)
    finally:
        client_connection.close()

    thread.join(timeout=1)


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


def test_simple_passing_test():
    """Test that a simple passing property test works."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    try:
        client_connection = Connection(client_socket, name="Client")
        client = Client(client_connection)

        def my_test():
            x = draw({"type": "integer", "minimum": 0, "maximum": 100})
            assert x >= 0
            assert x <= 100

        # Passing test completes without error
        client.run_test("test_simple", my_test, test_cases=50)

    finally:
        client_connection.close()

    thread.join(timeout=5)


def test_failing_test_with_shrinking():
    """Test that a failing test is detected and raises."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    try:
        client_connection = Connection(client_socket, name="Client")
        client = Client(client_connection)

        def my_test():
            x = draw({"type": "integer", "minimum": 0, "maximum": 1000})
            # This will fail for x > 10
            assert x <= 10

        with pytest.raises(AssertionError):
            client.run_test("test_fail", my_test, test_cases=100)

    finally:
        client_connection.close()

    thread.join(timeout=5)


def test_assume_causes_invalid():
    """Test that assume(False) marks test cases as invalid."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    try:
        client_connection = Connection(client_socket, name="Client")
        client = Client(client_connection)

        def my_test():
            x = draw({"type": "integer", "minimum": 0, "maximum": 100})
            # Reject odd numbers
            assume(x % 2 == 0)
            # Only even numbers should get here
            assert x % 2 == 0

        # Should pass - assume filters invalid cases
        client.run_test("test_assume", my_test, test_cases=100)

    finally:
        client_connection.close()

    thread.join(timeout=5)


def test_strategy_helpers():
    """Test the convenience strategy builders."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    try:
        client_connection = Connection(client_socket, name="Client")
        client = Client(client_connection)

        def my_test():
            # Test integers helper
            n = integers(min_value=0, max_value=10).generate()
            assert 0 <= n <= 10

            # Test text helper
            s = text(min_size=1, max_size=5).generate()
            assert 1 <= len(s) <= 5

            # Test booleans helper
            b = booleans().generate()
            assert isinstance(b, bool)

        client.run_test("test_helpers", my_test, test_cases=50)

    finally:
        client_connection.close()

    thread.join(timeout=5)


def test_multiple_tests_same_connection():
    """Test running multiple tests on the same connection."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    try:
        client_connection = Connection(client_socket, name="Client")
        client = Client(client_connection)

        def test1():
            x = draw({"type": "integer"})
            assert isinstance(x, int)

        def test2():
            s = draw({"type": "string", "max_size": 10})
            assert isinstance(s, str)

        client.run_test("test1", test1, test_cases=20)
        client.run_test("test2", test2, test_cases=20)

    finally:
        client_connection.close()

    thread.join(timeout=5)


def test_target_observations():
    """Test that target() records observations."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    try:
        client_connection = Connection(client_socket, name="Client")
        client = Client(client_connection)

        def my_test():
            x = draw({"type": "integer", "minimum": 0, "maximum": 100})
            # Guide toward larger values
            target(float(x), "size")
            assert x >= 0

        client.run_test("test_target", my_test, test_cases=50)

    finally:
        client_connection.close()

    thread.join(timeout=5)


def test_lists_of_integers():
    """Test generating lists."""
    server_socket, client_socket = socket.socketpair()
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket, name="Server"),),
        daemon=True,
    )
    thread.start()

    try:
        client_connection = Connection(client_socket, name="Client")
        client = Client(client_connection)

        def my_test():
            xs = lists(integers(min_value=0, max_value=10), max_size=3).generate()
            assert isinstance(xs, list)
            assert len(xs) <= 3
            for x in xs:
                assert 0 <= x <= 10

        client.run_test("test_lists", my_test, test_cases=10)

    finally:
        client_connection.close()

    thread.join(timeout=5)
