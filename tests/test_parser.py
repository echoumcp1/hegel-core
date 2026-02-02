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


def test_null():
    assert from_schema({"type": "null"}).example() is None


def test_boolean():
    assert from_schema({"type": "boolean"}).example() in [True, False]


def test_integer():
    v = from_schema({"type": "integer", "minimum": 0, "maximum": 10}).example()
    assert isinstance(v, int)
    assert 0 <= v <= 10


def test_number():
    v = from_schema(
        {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "allow_nan": False,
            "allow_infinity": False,
            "exclude_minimum": False,
            "exclude_maximum": False,
            "width": 64,
        }
    ).example()
    assert isinstance(v, float)
    assert 0.0 <= v <= 1.0


def test_number_exclusive():
    v = from_schema(
        {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "exclude_minimum": True,
            "exclude_maximum": True,
            "allow_nan": False,
            "allow_infinity": False,
            "width": 64,
        },
    ).example()
    assert 0.0 < v < 1.0


def test_string():
    v = from_schema({"type": "string", "min_size": 1, "max_size": 5}).example()
    assert isinstance(v, str)
    assert 1 <= len(v) <= 5


def test_string_pattern():
    v = from_schema(
        {"type": "regex", "pattern": r"^[a-z]+$", "fullmatch": True},
    ).example()
    assert v.isalpha()
    assert v.islower()


def test_email():
    v = from_schema({"type": "email"}).example()
    assert "@" in v


def test_ipv4():
    v = from_schema({"type": "ipv4"}).example()
    parts = v.split(".")
    assert len(parts) == 4
    assert all(0 <= int(p) <= 255 for p in parts)


def test_ipv6():
    v = from_schema({"type": "ipv6"}).example()
    assert ":" in v


def test_date():
    v = from_schema({"type": "date"}).example()
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", v)


def test_time():
    v = from_schema({"type": "time"}).example()
    assert re.match(r"^\d{2}:\d{2}:\d{2}", v)


def test_datetime():
    v = from_schema({"type": "datetime"}).example()
    assert "T" in v


def test_const():
    assert from_schema({"const": 42}).example() == 42
    assert from_schema({"const": "hello"}).example() == "hello"


def test_sampled_from():
    v = from_schema({"sampled_from": [1, 2, 3]}).example()
    assert v in [1, 2, 3]


def test_one_of():
    v = from_schema({"one_of": [{"type": "boolean"}, {"type": "null"}]}).example()
    assert v is None or isinstance(v, bool)


def test_list():
    schema = {
        "type": "list",
        "elements": {"type": "integer", "minimum": -100, "maximum": 100},
        "min_size": 0,
    }
    v = from_schema(schema).example()
    assert isinstance(v, list)
    assert all(isinstance(x, int) for x in v)


def test_list_size():
    schema = {
        "type": "list",
        "elements": {"type": "integer"},
        "min_size": 2,
        "max_size": 5,
    }
    v = from_schema(schema).example()
    assert 2 <= len(v) <= 5


def test_set():
    schema = {
        "type": "set",
        "elements": {"type": "integer", "minimum": 0, "maximum": 100},
        "min_size": 5,
        "max_size": 10,
    }
    v = from_schema(schema).example()
    assert len(v) == len(set(v))
    assert 5 <= len(v) <= 10


def test_dict():
    schema = {
        "type": "dict",
        "keys": {"type": "string", "min_size": 1},
        "values": {"type": "integer", "minimum": -100, "maximum": 100},
        "min_size": 0,
    }
    v = from_schema(schema).example()
    # Wire format is [[key, value], ...]
    assert isinstance(v, list)
    assert all(isinstance(kv, tuple) and len(kv) == 2 for kv in v)
    assert all(isinstance(k, str) and len(k) >= 1 for k, _ in v)
    assert all(isinstance(val, int) for _, val in v)


def test_dict_size():
    schema = {
        "type": "dict",
        "keys": {"type": "string", "min_size": 0},
        "values": {"type": "integer", "minimum": -100, "maximum": 100},
        "min_size": 1,
        "max_size": 3,
    }
    v = from_schema(schema).example()
    assert 1 <= len(v) <= 3


def test_dict_default_keys():
    # Test that we can generate dicts with string keys
    schema = {
        "type": "dict",
        "keys": {"type": "string", "min_size": 0},
        "values": {"type": "integer", "minimum": -100, "maximum": 100},
        "min_size": 0,
    }
    v = from_schema(schema).example()
    # Wire format is [[key, value], ...]
    assert isinstance(v, list)
    assert all(isinstance(k, str) for k, _ in v)


def test_tuple():
    schema = {
        "type": "tuple",
        "elements": [
            {"type": "integer", "minimum": -100, "maximum": 100},
            {"type": "string", "min_size": 0},
            {"type": "boolean"},
        ],
    }
    v = from_schema(schema).example()
    assert isinstance(v, tuple)
    assert len(v) == 3
    assert isinstance(v[0], int)
    assert isinstance(v[1], str)
    assert isinstance(v[2], bool)


def test_tuple_empty():
    schema = {"type": "tuple", "elements": []}
    assert from_schema(schema).example() == ()


def test_set_of_tuples():
    schema = {
        "type": "set",
        "elements": {
            "type": "tuple",
            "elements": [
                {"type": "integer", "minimum": -100, "maximum": 100},
                {"type": "integer", "minimum": -100, "maximum": 100},
            ],
        },
        "min_size": 0,
    }
    v = from_schema(schema).example()
    assert isinstance(v, set)
    assert all(isinstance(elem, tuple) for elem in v)
    json.dumps(v, cls=HegelEncoder)


def test_nested_dict_of_lists():
    schema = {
        "type": "dict",
        "keys": {"type": "string", "min_size": 1},
        "values": {
            "type": "list",
            "elements": {"type": "integer", "minimum": -100, "maximum": 100},
            "min_size": 0,
            "max_size": 5,
        },
        "min_size": 1,
        "max_size": 2,
    }
    v = from_schema(schema).example()
    # Wire format is [[key, value], ...]
    assert isinstance(v, list)
    assert 1 <= len(v) <= 2
    for key, val in v:
        assert isinstance(key, str)
        assert isinstance(val, list)
        assert len(val) <= 5


def test_empty_schema():
    with pytest.raises(ValueError, match="Unsupported schema"):
        from_schema({}).example()


def test_unsupported_type():
    with pytest.raises(ValueError, match="Unsupported schema"):
        from_schema({"type": "unknown"}).example()
