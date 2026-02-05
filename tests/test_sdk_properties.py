"""
Comprehensive property-based tests demonstrating the Python SDK.

These tests show the SDK being used with the @hegel decorator.
"""

import base64
import itertools
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from time import time

import pytest

from hegel.sdk import (
    assume,
    booleans,
    floats,
    generate_from_schema,
    hegel,
    integers,
    just,
    lists,
    one_of,
    target,
    text,
    tuples,
)

# =============================================================================
# Basic Property Tests
# =============================================================================


@hegel
def test_addition_is_commutative():
    """Classic property: a + b == b + a"""
    a = integers().generate()
    b = integers().generate()
    assert a + b == b + a


def test_can_run_passing_test_twice():
    test_addition_is_commutative()
    test_addition_is_commutative()


@hegel
def test_multiplication_distributes_over_addition():
    """Property: a * (b + c) == a * b + a * c"""
    a = integers(min_value=-100, max_value=100).generate()
    b = integers(min_value=-100, max_value=100).generate()
    c = integers(min_value=-100, max_value=100).generate()
    assert a * (b + c) == a * b + a * c


# =============================================================================
# List Properties
# =============================================================================


@hegel
def test_reverse_reverse_is_identity():
    """Property: reverse(reverse(xs)) == xs"""
    xs = lists(integers(), max_size=5).generate()
    assert list(reversed(list(reversed(xs)))) == xs


@hegel
def test_sorted_list_is_ordered():
    """Property: sorted list has each element <= next element"""
    xs = lists(integers(), min_size=1, max_size=5).generate()
    sorted_xs = sorted(xs)
    for i in range(len(sorted_xs) - 1):
        assert sorted_xs[i] <= sorted_xs[i + 1]


@hegel
def test_list_concatenation_length():
    """Property: len(xs + ys) == len(xs) + len(ys)"""
    xs = lists(integers(), max_size=5).generate()
    ys = lists(integers(), max_size=5).generate()
    assert len(xs + ys) == len(xs) + len(ys)


# =============================================================================
# String Properties
# =============================================================================


@hegel
def test_string_length_after_strip():
    """Property: len(s.strip()) <= len(s)"""
    s = text(max_size=20).generate()
    assert len(s.strip()) <= len(s)


# =============================================================================
# Using assume() for Filtering
# =============================================================================


@hegel(test_cases=50)
def test_division_with_assume():
    """Use assume() to filter out division by zero."""
    a = integers(min_value=-100, max_value=100).generate()
    b = integers(min_value=-100, max_value=100).generate()
    assume(b != 0)
    # Integer division properties
    assert a == (a // b) * b + (a % b)


# =============================================================================
# Finding Bugs (Tests Expected to Fail)
# =============================================================================


def test_finds_failing_case():
    """Verify that the framework finds failing cases and re-raises."""

    @hegel(test_cases=50)
    def failing_prop():
        x = integers(min_value=0, max_value=1000).generate()
        # This fails for x > 50
        assert x <= 50

    # The original AssertionError is re-raised directly
    with pytest.raises(AssertionError, match=r"assert \d+ <= 50"):
        failing_prop()


def test_finds_edge_case_in_list():
    """Verify shrinking finds minimal failing case - re-raises original exception."""

    @hegel(test_cases=50)
    def failing_prop():
        xs = lists(
            integers(min_value=0, max_value=100),
            min_size=1,
            max_size=5,
        ).generate()
        # Fails if any element > 10
        assert all(x <= 10 for x in xs)

    # Original exception is re-raised directly
    with pytest.raises(AssertionError):
        failing_prop()


# =============================================================================
# Using target() for Optimization
# =============================================================================


def test_target_guides_toward_larger_values():
    """Use target() to guide search toward edge cases."""
    max_seen = [0]

    @hegel(test_cases=100)
    def prop():
        x = floats(min_value=0, max_value=10000).generate()
        score = 1 - (float(x) - 101) ** 2
        target(score, "maximize_x")
        max_seen[0] = max(max_seen[0], score)
        assert x >= 0  # Always passes

    prop()
    # With targeting, we should see very close to the target maximum
    assert max_seen[0] > 0.99


# =============================================================================
# Complex Data Structures
# =============================================================================


@hegel
def test_nested_structure():
    """Test with nested data structures."""
    # List of tuples of (int, string)
    data = lists(
        tuples(integers(min_value=0, max_value=100), text(max_size=5)),
        max_size=5,
    ).generate()
    # Verify structure
    assert isinstance(data, list)
    for item in data:
        assert isinstance(item, list | tuple)
        assert len(item) == 2
        assert isinstance(item[0], int)
        assert isinstance(item[1], str)


@hegel
def test_one_of_generator():
    """Test one_of for union types."""
    value = one_of(
        integers(min_value=0, max_value=100),
        text(max_size=5),
        just(None),
    ).generate()
    # Value should be one of the types
    assert isinstance(value, int | str | type(None))


@hegel
def test_conditional_generation():
    """Test conditional/dependent generation."""
    use_string = booleans().generate()
    if use_string:
        value = text(min_size=1, max_size=5).generate()
        assert isinstance(value, str)
        assert len(value) >= 1
    else:
        value = integers(min_value=0, max_value=100).generate()
        assert isinstance(value, int)
        assert 0 <= value <= 100


# =============================================================================
# Real-World-ish Examples
# =============================================================================


@hegel
def test_json_encode_decode_roundtrip():
    """Property: JSON encode/decode is identity for simple values."""
    # Generate JSON-compatible values
    value = one_of(
        integers(min_value=-1000, max_value=1000),
        text(max_size=10),
        booleans(),
        just(None),
    ).generate()
    encoded = json.dumps(value)
    decoded = json.loads(encoded)
    assert decoded == value


@hegel
def test_base64_roundtrip():
    """Property: base64 encode/decode is identity."""
    # Generate bytes via the binary type (returns base64 string)
    b64_data = generate_from_schema({"type": "binary", "min_size": 0, "max_size": 50})
    # Decode and re-encode
    decoded = base64.b64decode(b64_data)
    reencoded = base64.b64encode(decoded).decode("ascii")
    assert reencoded == b64_data


@hegel
def test_date_parsing():
    """Test date parsing with generated dates."""
    date_str = generate_from_schema({"type": "date"})
    # Should be ISO format YYYY-MM-DD
    parsed = date.fromisoformat(date_str)
    assert parsed.isoformat() == date_str


# =============================================================================
# Combinators (map, filter, flat_map)
# =============================================================================


@hegel
def test_map_combinator():
    """Test the .map() combinator."""
    # Generate integers and double them
    doubled = integers(min_value=0, max_value=100).map(lambda x: x * 2).generate()
    assert doubled % 2 == 0  # Should always be even
    assert 0 <= doubled <= 200


@hegel
def test_filter_combinator():
    """Test the .filter() combinator."""
    # Generate only even integers
    even = integers(min_value=0, max_value=100).filter(lambda x: x % 2 == 0).generate()
    assert even % 2 == 0


@hegel(test_cases=20)
def test_flat_map_combinator():
    """Test the .flat_map() combinator for dependent generation."""
    # Generate a size, then a list of that size
    result = (
        integers(min_value=1, max_value=5)
        .flat_map(lambda n: lists(integers(), min_size=n, max_size=n))
        .generate()
    )
    assert isinstance(result, list)
    assert 1 <= len(result) <= 5


@hegel(test_cases=20)
def test_chained_combinators():
    """Test chaining multiple combinators."""
    # Generate positive integers, filter for even, then double
    result = (
        integers(min_value=1, max_value=50)
        .filter(lambda x: x % 2 == 0)
        .map(lambda x: x * 2)
        .generate()
    )
    assert result % 4 == 0  # Should be divisible by 4
    assert 4 <= result <= 200


ALL_SEQ_TESTS = [
    v for k, v in globals().items() if k.startswith("test_") and "concurrently" not in k
]


def test_tests_can_be_correctly_run_concurrently():
    def run_test(test_function):
        start = time()
        test_function()
        end = time()
        return [start, end]

    executor = ThreadPoolExecutor(max_workers=len(ALL_SEQ_TESTS))

    results = list(executor.map(run_test, ALL_SEQ_TESTS))
    results.sort()

    assert any(v1 > u2 for (_, v1), (u2, _) in itertools.pairwise(results))
