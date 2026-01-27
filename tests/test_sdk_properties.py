"""
Comprehensive property-based tests demonstrating the Python SDK.

These tests show the SDK being used like Hypothesis, with various
property patterns and edge case discovery.
"""

import socket
from threading import Thread

from hegel.hegeld import run_server_on_connection
from hegel.protocol import Connection
from hegel.sdk import (
    Client,
    assume,
    booleans,
    draw,
    integers,
    just,
    lists,
    one_of,
    sampled_from,
    target,
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
# Basic Property Tests
# =============================================================================


def test_addition_is_commutative():
    """Classic property: a + b == b + a"""

    def prop():
        a = integers().draw()
        b = integers().draw()
        assert a + b == b + a

    result = run_with_server(prop)
    assert result.passed


def test_multiplication_distributes_over_addition():
    """Property: a * (b + c) == a * b + a * c"""

    def prop():
        a = integers(min_value=-100, max_value=100).draw()
        b = integers(min_value=-100, max_value=100).draw()
        c = integers(min_value=-100, max_value=100).draw()
        assert a * (b + c) == a * b + a * c

    result = run_with_server(prop)
    assert result.passed


# =============================================================================
# List Properties
# =============================================================================


def test_reverse_reverse_is_identity():
    """Property: reverse(reverse(xs)) == xs"""

    def prop():
        xs = lists(integers(), max_size=3).draw()
        assert list(reversed(list(reversed(xs)))) == xs

    result = run_with_server(prop)
    assert result.passed


def test_sorted_list_is_ordered():
    """Property: sorted list has each element <= next element"""

    def prop():
        xs = lists(integers(), min_size=1, max_size=3).draw()
        sorted_xs = sorted(xs)
        for i in range(len(sorted_xs) - 1):
            assert sorted_xs[i] <= sorted_xs[i + 1]

    result = run_with_server(prop)
    assert result.passed


def test_list_concatenation_length():
    """Property: len(xs + ys) == len(xs) + len(ys)"""

    def prop():
        xs = lists(integers(), max_size=3).draw()
        ys = lists(integers(), max_size=3).draw()
        assert len(xs + ys) == len(xs) + len(ys)

    result = run_with_server(prop)
    assert result.passed


# =============================================================================
# String Properties
# =============================================================================


def test_string_length_after_strip():
    """Property: len(s.strip()) <= len(s)"""

    def prop():
        s = text(max_size=20).draw()
        assert len(s.strip()) <= len(s)

    result = run_with_server(prop)
    assert result.passed


# =============================================================================
# Using assume() for Filtering
# =============================================================================


def test_division_with_assume():
    """Use assume() to filter out division by zero."""

    def prop():
        a = integers(min_value=-100, max_value=100).draw()
        b = integers(min_value=-100, max_value=100).draw()
        assume(b != 0)
        # Integer division properties
        assert a == (a // b) * b + (a % b)

    result = run_with_server(prop, test_cases=20)
    assert result.passed
    assert result.invalid_examples > 0  # Some should have been filtered


# =============================================================================
# Finding Bugs (Tests Expected to Fail)
# =============================================================================


def test_finds_failing_case():
    """Verify that the framework finds failing cases."""

    def prop():
        x = integers(min_value=0, max_value=1000).draw()
        # This fails for x > 50
        assert x <= 50

    result = run_with_server(prop, test_cases=30)
    assert not result.passed
    assert result.failure is not None
    assert result.failure["exc_type"] == "AssertionError"


def test_finds_edge_case_in_list():
    """Verify shrinking finds minimal failing case."""

    def prop():
        xs = lists(integers(min_value=0, max_value=100), min_size=1, max_size=3).draw()
        # Fails if any element > 10
        assert all(x <= 10 for x in xs)

    result = run_with_server(prop, test_cases=30)
    assert not result.passed
    assert result.failure is not None


# =============================================================================
# Using target() for Optimization
# =============================================================================


def test_target_guides_toward_larger_values():
    """Use target() to guide search toward edge cases."""
    max_seen = [0]

    def prop():
        x = integers(min_value=0, max_value=10000).draw()
        target(float(x), "maximize_x")
        max_seen[0] = max(max_seen[0], x)
        assert x >= 0  # Always passes

    result = run_with_server(prop, test_cases=20)
    assert result.passed
    # With targeting, we should see some large values
    assert max_seen[0] > 100


# =============================================================================
# Complex Data Structures
# =============================================================================


def test_nested_structure():
    """Test with nested data structures."""

    def prop():
        # List of tuples of (int, string)
        data = lists(
            tuples(integers(min_value=0, max_value=100), text(max_size=3)), max_size=3
        ).draw()
        # Verify structure
        assert isinstance(data, list)
        for item in data:
            assert isinstance(item, (list, tuple))
            assert len(item) == 2
            assert isinstance(item[0], int)
            assert isinstance(item[1], str)

    result = run_with_server(prop)
    assert result.passed


def test_one_of_strategy():
    """Test one_of for union types."""

    def prop():
        value = one_of(
            integers(min_value=0, max_value=100),
            text(max_size=5),
            just(None),
        ).draw()
        # Value should be one of the types
        assert isinstance(value, (int, str, type(None)))

    result = run_with_server(prop)
    assert result.passed


def test_conditional_generation():
    """Test conditional/dependent generation."""

    def prop():
        use_string = booleans().draw()
        if use_string:
            value = text(min_size=1, max_size=5).draw()
            assert isinstance(value, str)
            assert len(value) >= 1
        else:
            value = integers(min_value=0, max_value=100).draw()
            assert isinstance(value, int)
            assert 0 <= value <= 100

    result = run_with_server(prop)
    assert result.passed


# =============================================================================
# Real-World-ish Examples
# =============================================================================


def test_json_encode_decode_roundtrip():
    """Property: JSON encode/decode is identity for simple values."""
    import json

    def prop():
        # Generate JSON-compatible values
        value = one_of(
            integers(min_value=-1000, max_value=1000),
            text(max_size=10),
            booleans(),
            just(None),
        ).draw()
        encoded = json.dumps(value)
        decoded = json.loads(encoded)
        assert decoded == value

    result = run_with_server(prop)
    assert result.passed


def test_base64_roundtrip():
    """Property: base64 encode/decode is identity."""
    import base64

    def prop():
        # Generate bytes via the binary type (returns base64 string)
        b64_data = draw({"type": "binary", "min_size": 0, "max_size": 50})
        # Decode and re-encode
        decoded = base64.b64decode(b64_data)
        reencoded = base64.b64encode(decoded).decode("ascii")
        assert reencoded == b64_data

    result = run_with_server(prop)
    assert result.passed


def test_date_parsing():
    """Test date parsing with generated dates."""
    from datetime import date

    def prop():
        date_str = draw({"type": "date"})
        # Should be ISO format YYYY-MM-DD
        parsed = date.fromisoformat(date_str)
        assert parsed.isoformat() == date_str

    result = run_with_server(prop)
    assert result.passed
