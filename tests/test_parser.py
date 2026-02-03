import json
import re

import pytest
from hypothesis import given, settings, strategies as st

from hegel.parser import from_schema
from hegel.runner import HegelEncoder


def primitive_hashable_schemas():
    return (
        st.just({"type": "null"})
        | st.just({"type": "boolean"})
        | st.builds(
            lambda min_val, max_val: {
                "type": "integer",
                "minimum": min_val,
                "maximum": max_val,
            },
            min_val=st.integers(min_value=-1000, max_value=0),
            max_val=st.integers(min_value=0, max_value=1000),
        )
        | st.builds(
            lambda max_size: {"type": "string", "min_size": 0, "max_size": max_size},
            max_size=st.integers(min_value=0, max_value=10),
        )
        | st.just({"type": "email"})
        | st.just({"type": "ipv4"})
        | st.just({"type": "ipv6"})
        | st.just({"type": "date"})
        | st.just({"type": "time"})
        | st.just({"type": "datetime"})
    )


def hashable_schemas():
    return st.recursive(
        primitive_hashable_schemas(),
        lambda inner: st.builds(
            lambda elements: {"type": "tuple", "elements": elements},
            elements=st.lists(inner, min_size=0, max_size=3),
        ),
    )


# Strategy that generates arbitrary valid schemas for from_schema
def schemas():
    return st.recursive(
        # Base cases: simple schemas with no nested schemas
        hashable_schemas()
        | st.builds(
            lambda min_val, max_val: {
                "type": "number",
                "minimum": min_val,
                "maximum": max_val,
                "allow_nan": False,
                "allow_infinity": False,
                "exclude_minimum": False,
                "exclude_maximum": False,
                "width": 64,
            },
            min_val=st.floats(min_value=-1000, max_value=0, allow_nan=False),
            max_val=st.floats(min_value=0, max_value=1000, allow_nan=False),
        )
        # const with JSON-serializable values
        | st.builds(
            lambda v: {"const": v},
            v=st.none() | st.booleans() | st.integers() | st.text(max_size=5),
        )
        # sampled_from with JSON-serializable values
        | st.builds(
            lambda vs: {"sampled_from": vs},
            vs=st.lists(
                st.none() | st.booleans() | st.integers(),
                min_size=1,
                max_size=5,
                unique=True,
            ),
        ),
        # Recursive cases: schemas that contain other schemas
        lambda inner: (
            # list
            st.builds(
                lambda elements, max_size: {
                    "type": "list",
                    "elements": elements,
                    "min_size": 0,
                    "max_size": max_size,
                },
                elements=inner,
                max_size=st.integers(min_value=0, max_value=3),
            )
            # set - only hashable elements
            | st.builds(
                lambda elements, max_size: {
                    "type": "set",
                    "elements": elements,
                    "min_size": 0,
                    "max_size": max_size,
                },
                elements=hashable_schemas(),
                max_size=st.integers(min_value=0, max_value=3),
            )
            # dict (keys must be strings for JSON)
            | st.builds(
                lambda values, max_size: {
                    "type": "dict",
                    "keys": {"type": "string", "min_size": 1, "max_size": 5},
                    "values": values,
                    "min_size": 0,
                    "max_size": max_size,
                },
                values=inner,
                max_size=st.integers(min_value=0, max_value=3),
            )
            # tuple
            | st.builds(
                lambda elements: {"type": "tuple", "elements": elements},
                elements=st.lists(inner, min_size=0, max_size=3),
            )
            # one_of
            | st.builds(
                lambda options: {"one_of": options},
                options=st.lists(inner, min_size=1, max_size=3),
            )
        ),
        max_leaves=10,
    )


@given(st.data())
@settings(max_examples=10)
def test_from_schema_output_is_json_serializable(data):
    schema = data.draw(schemas())
    strategy = from_schema(schema)
    v = data.draw(strategy)
    json.dumps(v, cls=HegelEncoder)


def schema_test(schema):
    def accept(test):
        return (
            settings(database=None, max_examples=1)(
                given(from_schema(schema))(test)
            )
        )
    return accept


@schema_test({"type": "null"})
def test_null(example):
    assert example is None


@schema_test({"type": "boolean"})
def test_boolean(example):
    assert example in [True, False]


@schema_test({"type": "integer", "minimum": 0, "maximum": 10})
def test_integer(example):
    assert isinstance(example, int)
    assert 0 <= example <= 10


@schema_test({"type": "number", "minimum": 0.0, "maximum": 1.0})
def test_number(example):
    assert isinstance(example, float)
    assert 0.0 <= example <= 1.0


@schema_test({
    "type": "number",
    "minimum": 0.0,
    "maximum": 1.0,
    "exclude_minimum": True,
    "exclude_maximum": True,
})
def test_number_exclusive(example):
    assert 0.0 < example < 1.0


@schema_test({"type": "string", "min_size": 1, "max_size": 5})
def test_string(example):
    assert isinstance(example, str)
    assert 1 <= len(example) <= 5


@schema_test({"type": "regex", "pattern": r"^[a-z]+$", "fullmatch": True})
def test_string_pattern(example):
    assert example.isalpha()
    assert example.islower()


@schema_test({"type": "email"})
def test_email(example):
    assert "@" in example


@schema_test({"type": "ipv4"})
def test_ipv4(example):
    parts = example.split(".")
    assert len(parts) == 4
    assert all(0 <= int(p) <= 255 for p in parts)


@schema_test({"type": "ipv6"})
def test_ipv6(example):
    assert ":" in example


@schema_test({"type": "date"})
def test_date(example):
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", example)


@schema_test({"type": "time"})
def test_time(example):
    assert re.match(r"^\d{2}:\d{2}:\d{2}", example)


@schema_test({"type": "datetime"})
def test_datetime(example):
    assert "T" in example


@schema_test({"const": 42})
def test_const_int(example):
    assert example == 42


@schema_test({"const": "hello"})
def test_const_str(example):
    assert example == "hello"


@schema_test({"sampled_from": [1, 2, 3]})
def test_sampled_from(example):
    assert example in [1, 2, 3]


@schema_test({"one_of": [{"type": "boolean"}, {"type": "null"}]})
def test_one_of(example):
    assert example is None or isinstance(example, bool)


@schema_test({"type": "list", "elements": {"type": "integer"}})
def test_list(example):
    assert isinstance(example, list)
    assert all(isinstance(x, int) for x in example)


@schema_test({
    "type": "list",
    "elements": {"type": "integer"},
    "min_size": 2,
    "max_size": 5,
})
def test_list_size(example):
    assert 2 <= len(example) <= 5


@schema_test({
    "type": "set",
    "elements": {"type": "integer", "minimum": 0, "maximum": 100},
    "min_size": 5,
    "max_size": 10,
})
def test_set(example):
    assert len(example) == len(set(example))
    assert 5 <= len(example) <= 10


@schema_test({
    "type": "dict",
    "keys": {"type": "string", "min_size": 1},
    "values": {"type": "integer"},
})
def test_dict(example):
    # Wire format is [[key, value], ...]
    assert isinstance(example, list)
    assert all(isinstance(kv, tuple) and len(kv) == 2 for kv in example)
    assert all(isinstance(k, str) and len(k) >= 1 for k, _ in example)
    assert all(isinstance(val, int) for _, val in example)


@schema_test({
    "type": "dict",
    "keys": {"type": "string"},
    "values": {"type": "integer"},
    "min_size": 1,
    "max_size": 3,
})
def test_dict_size(example):
    assert 1 <= len(example) <= 3


@schema_test({"type": "dict", "values": {"type": "integer"}})
def test_dict_default_keys(example):
    # Wire format is [[key, value], ...], keys default to strings
    assert isinstance(example, list)
    assert all(isinstance(k, str) for k, _ in example)


@schema_test({
    "type": "tuple",
    "elements": [{"type": "integer"}, {"type": "string"}, {"type": "boolean"}],
})
def test_tuple(example):
    assert isinstance(example, tuple)
    assert len(example) == 3
    assert isinstance(example[0], int)
    assert isinstance(example[1], str)
    assert isinstance(example[2], bool)


@schema_test({"type": "tuple", "elements": []})
def test_tuple_empty(example):
    assert example == ()


@schema_test({
    "type": "set",
    "elements": {
        "type": "tuple",
        "elements": [{"type": "integer"}, {"type": "integer"}],
    },
})
def test_set_of_tuples(example):
    assert isinstance(example, set)
    assert all(isinstance(elem, tuple) for elem in example)
    json.dumps(example, cls=HegelEncoder)


@schema_test({
    "type": "dict",
    "keys": {"type": "string", "min_size": 1},
    "values": {
        "type": "list",
        "elements": {"type": "integer"},
        "max_size": 5,
    },
    "min_size": 1,
    "max_size": 2,
})
def test_nested_dict_of_lists(example):
    # Wire format is [[key, value], ...]
    assert isinstance(example, list)
    assert 1 <= len(example) <= 2
    for key, val in example:
        assert isinstance(key, str)
        assert isinstance(val, list)
        assert len(val) <= 5


def test_empty_schema():
    with pytest.raises(ValueError, match="Unsupported schema"):
        from_schema({})


def test_unsupported_type():
    with pytest.raises(ValueError, match="Unsupported schema"):
        from_schema({"type": "unknown"})
