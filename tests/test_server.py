"""Tests for server.py uncovered paths."""

import socket
import time
from threading import Thread

import pytest
from hypothesis import strategies as st
from hypothesis.errors import UnsatisfiedAssumption

from hegel.protocol import RequestError
from hegel.protocol.connection import Connection
from hegel.server import (
    FROM_SCHEMA_CACHE,
    cached_from_schema,
    run_server_on_connection,
)
from tests.client import (
    Client,
    ClientConnection,
    assume,
    collection,
    generate_from_schema,
    start_span,
    stop_span,
    target,
)
from tests.client.client import _request

try:
    ExceptionGroup
except NameError:  # pragma: no cover
    from exceptiongroup import ExceptionGroup


def test_start_and_stop_span(client):
    def test():
        start_span(1)
        generate_from_schema({"type": "integer", "min_value": 0, "max_value": 10})
        stop_span()

    client.run_test(test, test_cases=10)


def test_stop_span_with_discard(client):
    def test():
        start_span(1)
        generate_from_schema({"type": "integer", "min_value": 0, "max_value": 10})
        stop_span(discard=True)

    client.run_test(test, test_cases=10)


def test_unknown_command(client):
    with pytest.raises(ConnectionError):
        client._control.send_request({"command": "bogus"})


def test_unknown_command_on_data_channel(client):
    """Unknown command on data channel raises RequestError via handle_requests."""

    def test():
        with pytest.raises(RequestError, match="Unknown command"):
            _request({"command": "bogus_data_command"})

    client.run_test(test, test_cases=1)


def test_cache_eviction():
    # Fill the cache beyond its max size
    for i in range(FROM_SCHEMA_CACHE.max_size + 10):
        schema = {"type": "integer", "min_value": i, "max_value": i + 100}
        cached_from_schema(schema)

    assert len(FROM_SCHEMA_CACHE) <= FROM_SCHEMA_CACHE.max_size


def test_collection_with_no_max_size(client):
    def test():
        c = collection("test_unbounded", min_size=1)
        result = []
        while c.more():
            val = generate_from_schema(
                {"type": "integer", "min_value": 0, "max_value": 100},
            )
            result.append(val)
        assert len(result) >= 1

    client.run_test(test, test_cases=10)


def test_collection_reject_on_server(client):
    """Test collection_reject command is handled by the server.

    Tests that the server handles the collection_reject command by calling
    collection.reject() with the provided reason.
    """

    def test():
        # Explicitly use collection.reject() to trigger the server-side
        # collection_reject handler.
        c = collection("test_coll", min_size=1, max_size=5)
        result = []
        while c.more():
            val = generate_from_schema(
                {"type": "integer", "min_value": 0, "max_value": 100},
            )
            if val % 2 != 0:
                c.reject()
            else:
                result.append(val)

    client.run_test(test, test_cases=20)


def test_unsatisfied_assumption_in_handler(client, monkeypatch):
    class AlwaysRejectStrategy(st.SearchStrategy):
        def do_draw(self, data):
            raise UnsatisfiedAssumption

    reject = AlwaysRejectStrategy()
    monkeypatch.setattr("hegel.server.cached_from_schema", lambda _: reject)

    def test():
        generate_from_schema({"type": "integer"})

    client.run_test(test, test_cases=10)


def test_future_cancel_on_connection_error(monkeypatch):
    """Test that pending futures with ConnectionError are cancelled.

    Tests the except (ConnectionError, TimeoutError): f.cancel() branch
    in run_server_on_connection's cleanup. We patch _run_one to raise
    ConnectionError, ensuring f.result() deterministically hits that path.
    """
    server_socket, client_socket = socket.socketpair()

    def raise_connection_error(*args, **kwargs):
        raise ConnectionError("test disconnect")

    monkeypatch.setattr("hegel.server._run_one", raise_connection_error)
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket),),
        daemon=True,
    )
    thread.start()

    with ClientConnection(client_socket) as client_connection:
        client = Client(client_connection)
        channel = client_connection.new_channel()
        client._control.send_request(
            {
                "command": "run_test",
                "channel_id": channel.channel_id,
                "test_cases": 100,
            },
        )

    thread.join(timeout=10)


def test_exception_in_run_one_is_printed_and_reraised(monkeypatch):
    """Tests the except Exception handler in _run_one that prints traceback.

    When an unexpected exception occurs inside _run_one (e.g., during
    ConjectureRunner.run()), it's caught, the traceback is printed,
    and the exception is re-raised.
    """
    server_socket, client_socket = socket.socketpair()

    def raise_runtime_error(*args, **kwargs):
        raise RuntimeError("simulated runner failure")

    monkeypatch.setattr("hegel.server.ConjectureRunner.run", raise_runtime_error)
    thread = Thread(
        target=run_server_on_connection,
        args=(Connection(server_socket),),
        daemon=True,
    )
    thread.start()

    with ClientConnection(client_socket) as client_connection:
        client = Client(client_connection)
        channel = client_connection.new_channel()
        client._control.send_request(
            {
                "command": "run_test",
                "channel_id": channel.channel_id,
                "test_cases": 10,
            },
        )

    thread.join(timeout=10)


def test_base_exception_in_server():
    """Test that BaseException in server loop is caught and printed.

    Tests the except BaseException handler in run_server_on_connection's main
    loop, which catches non-ConnectionError exceptions and prints the traceback.
    We let the handshake complete normally, then patch receive_request to raise
    KeyboardInterrupt on the next call (the main loop).
    """
    server_socket, client_socket = socket.socketpair()
    server_conn = Connection(server_socket)

    original_receive = server_conn.control_channel.read_request
    call_count = 0

    def patched_receive(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise KeyboardInterrupt("simulated")
        return original_receive(*args, **kwargs)

    def server():
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(server_conn.control_channel, "read_request", patched_receive)
            run_server_on_connection(server_conn)

    thread = Thread(target=server, daemon=True)
    thread.start()

    with ClientConnection(client_socket) as client_conn:
        client_conn.send_handshake()
        time.sleep(0.3)
    thread.join(timeout=5)


def test_passing(client):
    def test():
        x = generate_from_schema({"type": "integer", "min_value": 0, "max_value": 100})
        assert x >= 0
        assert x <= 100

    client.run_test(test, test_cases=50)


def test_failing(client):
    def test():
        assert (
            generate_from_schema({"type": "integer", "min_value": 0, "max_value": 1000})
            <= 10
        )

    with pytest.raises(AssertionError):
        client.run_test(test, test_cases=100)


def test_assume(client):
    def test():
        x = generate_from_schema({"type": "integer", "min_value": 0, "max_value": 100})
        assume(x % 2 == 0)
        assert x % 2 == 0

    client.run_test(test, test_cases=100)


def test_multiple_tests_on_connection(client):
    def test1():
        x = generate_from_schema({"type": "integer"})
        assert isinstance(x, int)

    def test2():
        s = generate_from_schema({"type": "string", "min_size": 0, "max_size": 10})
        assert isinstance(s, str)

    client.run_test(test1, test_cases=20)
    client.run_test(test2, test_cases=20)


def test_target(client):
    def test():
        x = generate_from_schema({"type": "integer", "min_value": 0, "max_value": 100})
        target(float(x), "size")

    client.run_test(test, test_cases=50)


def test_pool_basic(client):
    """Tests new_pool, pool_add, pool_generate, and pool_consume commands."""

    def test():
        pool_id = _request({"command": "new_pool"})
        assert isinstance(pool_id, int)

        # Add some variables to the pool
        v1 = _request({"command": "pool_add", "pool_id": pool_id})
        v2 = _request({"command": "pool_add", "pool_id": pool_id})
        assert v1 != v2

        # Generate a variable from the pool
        v = _request({"command": "pool_generate", "pool_id": pool_id})
        assert v in (v1, v2)

        # Consume a variable from the pool
        _request({"command": "pool_consume", "pool_id": pool_id, "variable_id": v1})

    client.run_test(test, test_cases=10)


def test_pool_generate_with_consume(client):
    """Tests pool_generate with consume=True."""

    def test():
        pool_id = _request({"command": "new_pool"})
        _request({"command": "pool_add", "pool_id": pool_id})
        _request({"command": "pool_add", "pool_id": pool_id})

        # Generate and consume in one step
        v = _request({"command": "pool_generate", "pool_id": pool_id, "consume": True})
        assert isinstance(v, int)

    client.run_test(test, test_cases=10)


def test_pool_generate_from_empty_pool(client):
    """Tests that generating from an empty pool marks the test case invalid."""

    def test():
        pool_id = _request({"command": "new_pool"})
        # Pool is empty, generate should cause the test case to be marked invalid
        # which results in UnsatisfiedAssumption -> DataExhausted on the client side
        # or it just gets marked invalid and the server moves on
        _request({"command": "pool_generate", "pool_id": pool_id})

    client.run_test(test, test_cases=10)


def test_reproduce_failure(client):
    def test():
        assert (
            generate_from_schema({"type": "integer", "min_value": 0, "max_value": 1000})
            <= 10
        )

    with pytest.raises(AssertionError):
        client.run_test(test, test_cases=100)

    blob = client.last_result["failure_blobs"][0]
    assert isinstance(blob, bytes)

    with pytest.raises(AssertionError):
        client.run_test(test, failure_blob=blob)


def test_reproduce_failure_blob_no_longer_fails(client):
    """When a blob no longer reproduces, the client raises RuntimeError."""

    def failing_test():
        assert (
            generate_from_schema({"type": "integer", "min_value": 0, "max_value": 1000})
            <= 10
        )

    with pytest.raises(AssertionError):
        client.run_test(failing_test, test_cases=100)

    blob = client.last_result["failure_blobs"][0]

    # The blob was for failing_test, but we replay with a test that always passes.
    with pytest.raises(AssertionError, match="failure blob did not reproduce"):
        client.run_test(lambda: None, failure_blob=blob)


def test_reproduce_failure_result_not_in_passing_test(client):
    def test():
        x = generate_from_schema({"type": "integer", "min_value": 0, "max_value": 100})
        assert x >= 0

    client.run_test(test, test_cases=50)
    assert client.last_result["failure_blobs"] == []


def test_multiple_blobs(client):
    def test():
        x = generate_from_schema({"type": "integer", "min_value": 0, "max_value": 100})
        assert x <= 10

        y = generate_from_schema({"type": "integer", "min_value": -10, "max_value": -1})
        assert y >= 0

    with pytest.raises(ExceptionGroup):
        client.run_test(test, test_cases=50)
    assert len(client.last_result["failure_blobs"]) == 2


def test_pool_generate_with_mostly_removed_variables(client):
    """Tests the fallback path in Variables.generate when random picks hit removed variables.

    When all 3 random picks land on removed variables, the method falls back to
    using the last variable in the list (lines 82-90 of server.py).
    """

    def test():
        pool_id = _request({"command": "new_pool"})
        # Add many variables
        variables = []
        for _ in range(20):
            v = _request({"command": "pool_add", "pool_id": pool_id})
            variables.append(v)

        # Consume all except the last one. The last one won't be trimmed
        # because it's not at the end of the removed set after consume().
        # Actually, consume trims from the end of the list, so consuming
        # variables that are NOT at the end won't trigger trimming.
        for v in variables[:-1]:
            _request({"command": "pool_consume", "pool_id": pool_id, "variable_id": v})

        # Now generate - with 19/20 variables removed, the 3-attempt loop
        # will very likely fail to find a non-removed variable, triggering
        # the fallback path.
        result = _request({"command": "pool_generate", "pool_id": pool_id})
        assert result == variables[-1]

    client.run_test(test, test_cases=50)
