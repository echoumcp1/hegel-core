"""
Tests for the @hegel decorator and generator functions.
"""

import socket
from threading import Thread

from hegel.hegeld import run_server_on_connection
from hegel.protocol import Connection
from hegel.sdk import (
    Client,
    SchemaGenerator,
    Verbosity,
    booleans,
    floats,
    integers,
    just,
    lists,
    one_of,
    optional,
    run_hegel_test,
    sampled_from,
    text,
    tuples,
)


def run_with_server(test_fn, test_cases=10):
    """Helper to run a test function against a server."""
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
        return client.run_test("test", test_fn, test_cases=test_cases)
    finally:
        client_connection.close()
        thread.join(timeout=5)


# =============================================================================
# Test generator functions with .generate() method
# =============================================================================


def test_integers_generate():
    """Test integers().generate() pattern."""

    def prop():
        a = integers().generate()
        b = integers().generate()
        assert a + b == b + a

    result = run_with_server(prop)
    assert result.passed


def test_text_generate():
    """Test text().generate() pattern."""

    def prop():
        s = text(max_size=20).generate()
        assert len(s.strip()) <= len(s)

    result = run_with_server(prop)
    assert result.passed


def test_lists_generate():
    """Test lists().generate() pattern."""

    def prop():
        xs = lists(integers(), max_size=5).generate()
        assert list(reversed(list(reversed(xs)))) == xs

    result = run_with_server(prop)
    assert result.passed


def test_one_of_generate():
    """Test one_of().generate() pattern."""

    def prop():
        value = one_of(
            integers(min_value=0, max_value=100),
            text(max_size=5),
            just(None),
        ).generate()
        assert isinstance(value, (int, str, type(None)))

    result = run_with_server(prop)
    assert result.passed


def test_optional_generate():
    """Test optional().generate() pattern."""

    def prop():
        value = optional(integers()).generate()
        assert value is None or isinstance(value, int)

    result = run_with_server(prop)
    assert result.passed


def test_tuples_generate():
    """Test tuples().generate() pattern."""

    def prop():
        t = tuples(integers(), text(max_size=5)).generate()
        assert len(t) == 2
        assert isinstance(t[0], int)
        assert isinstance(t[1], str)

    result = run_with_server(prop)
    assert result.passed


def test_sampled_from_generate():
    """Test sampled_from().generate() pattern."""

    def prop():
        color = sampled_from(["red", "green", "blue"]).generate()
        assert color in ["red", "green", "blue"]

    result = run_with_server(prop)
    assert result.passed


def test_booleans_generate():
    """Test booleans().generate() pattern."""

    def prop():
        b = booleans().generate()
        assert isinstance(b, bool)

    result = run_with_server(prop)
    assert result.passed


def test_floats_generate():
    """Test floats().generate() pattern."""

    def prop():
        f = floats(min_value=0.0, max_value=1.0).generate()
        assert 0.0 <= f <= 1.0

    result = run_with_server(prop)
    assert result.passed


# =============================================================================
# Test Generator class
# =============================================================================


def test_schema_generator_class():
    """Test creating SchemaGenerator directly from schema."""

    def prop():
        g = SchemaGenerator({"type": "integer", "minimum": 0, "maximum": 10})
        x = g.generate()
        assert 0 <= x <= 10

    result = run_with_server(prop)
    assert result.passed


# =============================================================================
# Test Verbosity enum
# =============================================================================


def test_verbosity_values():
    """Test that Verbosity enum has expected values."""
    assert Verbosity.QUIET.value == "quiet"
    assert Verbosity.NORMAL.value == "normal"
    assert Verbosity.VERBOSE.value == "verbose"
    assert Verbosity.DEBUG.value == "debug"


# =============================================================================
# Test run_hegel_test function
# =============================================================================


def test_run_hegel_test_passing():
    """Test run_hegel_test with a passing property."""

    def prop():
        a = integers().generate()
        b = integers().generate()
        assert a + b == b + a

    result = run_hegel_test(prop, test_cases=10, verbosity=Verbosity.QUIET)
    assert result.passed
    assert result.examples_run > 0


def test_run_hegel_test_failing():
    """Test run_hegel_test with a failing property - re-raises original exception."""

    def prop():
        x = integers(min_value=0, max_value=1000).generate()
        assert x <= 50  # Fails for x > 50

    with pytest.raises(AssertionError) as excinfo:
        run_hegel_test(prop, test_cases=30, verbosity=Verbosity.QUIET)
        assert False, "Expected AssertionError"

    e = excinfo.value
    # Original exception is re-raised - check it has the actual assertion
    assert "50" in str(e) or "51" in str(e)


# =============================================================================
# Test combinators (map, filter, flat_map)
# =============================================================================


def test_map_combinator():
    """Test the .map() combinator."""

    def prop():
        # Generate integers and double them
        doubled = integers(min_value=0, max_value=100).map(lambda x: x * 2).generate()
        assert doubled % 2 == 0  # Should always be even
        assert 0 <= doubled <= 200

    result = run_with_server(prop)
    assert result.passed


def test_filter_combinator():
    """Test the .filter() combinator."""

    def prop():
        # Generate only even integers
        even = (
            integers(min_value=0, max_value=100).filter(lambda x: x % 2 == 0).generate()
        )
        assert even % 2 == 0

    result = run_with_server(prop)
    assert result.passed


def test_flat_map_combinator():
    """Test the .flat_map() combinator for dependent generation."""

    def prop():
        # Generate a size, then a list of that size
        result = (
            integers(min_value=1, max_value=5)
            .flat_map(lambda n: lists(integers(), min_size=n, max_size=n))
            .generate()
        )
        assert isinstance(result, list)
        assert 1 <= len(result) <= 5

    result = run_with_server(prop)
    assert result.passed


def test_chained_combinators():
    """Test chaining multiple combinators."""

    def prop():
        # Generate positive integers, filter for even, then double
        result = (
            integers(min_value=1, max_value=50)
            .filter(lambda x: x % 2 == 0)
            .map(lambda x: x * 2)
            .generate()
        )
        assert result % 4 == 0  # Should be divisible by 4
        assert 4 <= result <= 200

    result = run_with_server(prop)
    assert result.passed
