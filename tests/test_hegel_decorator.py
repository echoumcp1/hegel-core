"""
Tests for the @hegel decorator and gen namespace.

These tests demonstrate the new Rust-style API:
- gen.integers().generate() instead of integers().draw()
- @hegel decorator for automatic hegeld spawning
"""

import socket
from threading import Thread

from hegel.hegeld import run_server_on_connection
from hegel.protocol import Connection
from hegel.sdk import (
    Client,
    Generator,
    Verbosity,
    gen,
    run_hegel_test,
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
# Test gen namespace with .generate() method
# =============================================================================


def test_gen_integers_generate():
    """Test gen.integers().generate() pattern."""

    def prop():
        a = gen.integers().generate()
        b = gen.integers().generate()
        assert a + b == b + a

    result = run_with_server(prop)
    assert result.passed


def test_gen_text_generate():
    """Test gen.text().generate() pattern."""

    def prop():
        s = gen.text(max_size=20).generate()
        assert len(s.strip()) <= len(s)

    result = run_with_server(prop)
    assert result.passed


def test_gen_lists_generate():
    """Test gen.lists().generate() pattern."""

    def prop():
        xs = gen.lists(gen.integers(), max_size=5).generate()
        assert list(reversed(list(reversed(xs)))) == xs

    result = run_with_server(prop)
    assert result.passed


def test_gen_vecs_alias():
    """Test that gen.vecs is an alias for gen.lists (Rust naming)."""

    def prop():
        xs = gen.vecs(gen.integers(), max_size=3).generate()
        assert isinstance(xs, list)

    result = run_with_server(prop)
    assert result.passed


def test_gen_one_of_generate():
    """Test gen.one_of().generate() pattern."""

    def prop():
        value = gen.one_of(
            gen.integers(min_value=0, max_value=100),
            gen.text(max_size=5),
            gen.just(None),
        ).generate()
        assert isinstance(value, (int, str, type(None)))

    result = run_with_server(prop)
    assert result.passed


def test_gen_optional_generate():
    """Test gen.optional().generate() pattern."""

    def prop():
        value = gen.optional(gen.integers()).generate()
        assert value is None or isinstance(value, int)

    result = run_with_server(prop)
    assert result.passed


def test_gen_tuples_generate():
    """Test gen.tuples().generate() pattern."""

    def prop():
        t = gen.tuples(gen.integers(), gen.text(max_size=5)).generate()
        assert len(t) == 2
        assert isinstance(t[0], int)
        assert isinstance(t[1], str)

    result = run_with_server(prop)
    assert result.passed


def test_gen_sampled_from_generate():
    """Test gen.sampled_from().generate() pattern."""

    def prop():
        color = gen.sampled_from(["red", "green", "blue"]).generate()
        assert color in ["red", "green", "blue"]

    result = run_with_server(prop)
    assert result.passed


def test_gen_booleans_generate():
    """Test gen.booleans().generate() pattern."""

    def prop():
        b = gen.booleans().generate()
        assert isinstance(b, bool)

    result = run_with_server(prop)
    assert result.passed


def test_gen_floats_generate():
    """Test gen.floats().generate() pattern."""

    def prop():
        f = gen.floats(min_value=0.0, max_value=1.0).generate()
        assert 0.0 <= f <= 1.0

    result = run_with_server(prop)
    assert result.passed


# =============================================================================
# Test Generator class
# =============================================================================


def test_generator_class():
    """Test creating Generator directly from schema."""

    def prop():
        g = Generator({"type": "integer", "minimum": 0, "maximum": 10})
        x = g.generate()
        assert 0 <= x <= 10

    result = run_with_server(prop)
    assert result.passed


def test_generator_draw_alias():
    """Test that .draw() is an alias for .generate() (backwards compat)."""

    def prop():
        g = gen.integers(min_value=0, max_value=10)
        x = g.draw()  # Using .draw() instead of .generate()
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
        a = gen.integers().generate()
        b = gen.integers().generate()
        assert a + b == b + a

    result = run_hegel_test(prop, test_cases=10, verbosity=Verbosity.QUIET)
    assert result.passed
    assert result.examples_run > 0


def test_run_hegel_test_failing():
    """Test run_hegel_test with a failing property."""

    def prop():
        x = gen.integers(min_value=0, max_value=1000).generate()
        assert x <= 50  # Fails for x > 50

    try:
        run_hegel_test(prop, test_cases=30, verbosity=Verbosity.QUIET)
        assert False, "Expected AssertionError"
    except AssertionError as e:
        assert "Property test failed" in str(e)
