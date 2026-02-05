import re

import pytest
from hypothesis import given, settings as Settings, strategies as st
from hypothesis._settings import local_settings
from hypothesis.control import _current_build_context

from hegel.parser import from_schema


def assert_all_examples(strategy, predicate, settings=None):
    """Asserts that all examples of the given strategy match the predicate."""
    if context := _current_build_context.value:
        with local_settings(Settings(parent=settings)):
            for _ in range(20):
                s = context.data.draw(strategy)
                msg = f"Found {s!r} using strategy {strategy} which does not match"
                assert predicate(s), msg
    else:

        @given(strategy)
        @Settings(parent=settings, database=None)
        def assert_examples(s):
            msg = f"Found {s!r} using strategy {strategy} which does not match"
            assert predicate(s), msg

        assert_examples()


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


def test_null():
    assert_all_examples(from_schema({"type": "null"}), lambda x: x is None)


def test_boolean():
    assert_all_examples(from_schema({"type": "boolean"}), lambda x: x in [True, False])


def test_integer():
    assert_all_examples(
        from_schema({"type": "integer", "minimum": 0, "maximum": 10}),
        lambda x: isinstance(x, int) and 0 <= x <= 10,
    )


def test_number():
    assert_all_examples(
        from_schema(
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
        ),
        lambda x: isinstance(x, float) and 0.0 <= x <= 1.0,
    )


def test_number_exclusive():
    assert_all_examples(
        from_schema(
            {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "exclude_minimum": True,
                "exclude_maximum": True,
                "allow_nan": False,
                "allow_infinity": False,
                "width": 64,
            }
        ),
        lambda x: 0.0 < x < 1.0,
    )


def test_string():
    assert_all_examples(
        from_schema({"type": "string", "min_size": 1, "max_size": 5}),
        lambda x: isinstance(x, str) and 1 <= len(x) <= 5,
    )


def test_string_pattern():
    assert_all_examples(
        from_schema({"type": "regex", "pattern": r"^[a-z]+$", "fullmatch": True}),
        lambda x: x.isalpha() and x.islower(),
    )


def test_email():
    assert_all_examples(from_schema({"type": "email"}), lambda x: "@" in x)


def test_ipv4():
    def check(x):
        parts = x.split(".")
        return len(parts) == 4 and all(0 <= int(p) <= 255 for p in parts)

    assert_all_examples(from_schema({"type": "ipv4"}), check)


def test_ipv6():
    assert_all_examples(from_schema({"type": "ipv6"}), lambda x: ":" in x)


def test_date():
    assert_all_examples(
        from_schema({"type": "date"}),
        lambda x: re.match(r"^\d{4}-\d{2}-\d{2}$", x),
    )


def test_time():
    assert_all_examples(
        from_schema({"type": "time"}),
        lambda x: re.match(r"^\d{2}:\d{2}:\d{2}", x),
    )


def test_datetime():
    assert_all_examples(from_schema({"type": "datetime"}), lambda x: "T" in x)


def test_const_int():
    assert_all_examples(from_schema({"const": 42}), lambda x: x == 42)


def test_const_str():
    assert_all_examples(from_schema({"const": "hello"}), lambda x: x == "hello")


def test_sampled_from():
    assert_all_examples(
        from_schema({"sampled_from": [1, 2, 3]}), lambda x: x in [1, 2, 3]
    )


def test_one_of():
    assert_all_examples(
        from_schema({"one_of": [{"type": "boolean"}, {"type": "null"}]}),
        lambda x: x is None or isinstance(x, bool),
    )


def test_list():
    assert_all_examples(
        from_schema({"type": "list", "elements": {"type": "integer"}, "min_size": 0}),
        lambda x: isinstance(x, list) and all(isinstance(i, int) for i in x),
    )


def test_list_size():
    assert_all_examples(
        from_schema(
            {
                "type": "list",
                "elements": {"type": "integer"},
                "min_size": 2,
                "max_size": 5,
            }
        ),
        lambda x: 2 <= len(x) <= 5,
    )


def test_set():
    assert_all_examples(
        from_schema(
            {
                "type": "set",
                "elements": {"type": "integer", "minimum": 0, "maximum": 100},
                "min_size": 5,
                "max_size": 10,
            }
        ),
        lambda x: len(x) == len(set(x)) and 5 <= len(x) <= 10,
    )


def test_dict():
    def check(x):
        return (
            isinstance(x, list)
            and all(isinstance(kv, tuple) and len(kv) == 2 for kv in x)
            and all(isinstance(k, str) and len(k) >= 1 for k, _ in x)
            and all(isinstance(val, int) for _, val in x)
        )

    assert_all_examples(
        from_schema(
            {
                "type": "dict",
                "keys": {"type": "string", "min_size": 1},
                "values": {"type": "integer"},
                "min_size": 0,
            }
        ),
        check,
    )


def test_dict_size():
    assert_all_examples(
        from_schema(
            {
                "type": "dict",
                "keys": {"type": "string", "min_size": 0},
                "values": {"type": "integer"},
                "min_size": 1,
                "max_size": 3,
            }
        ),
        lambda x: 1 <= len(x) <= 3,
    )


def test_dict_default_keys():
    def check(x):
        return isinstance(x, list) and all(isinstance(k, str) for k, _ in x)

    assert_all_examples(
        from_schema(
            {
                "type": "dict",
                "keys": {"type": "string", "min_size": 0},
                "values": {"type": "integer"},
                "min_size": 0,
            }
        ),
        check,
    )


def test_tuple():
    def check(x):
        return (
            isinstance(x, tuple)
            and len(x) == 3
            and isinstance(x[0], int)
            and isinstance(x[1], str)
            and isinstance(x[2], bool)
        )

    assert_all_examples(
        from_schema(
            {
                "type": "tuple",
                "elements": [
                    {"type": "integer"},
                    {"type": "string", "min_size": 0},
                    {"type": "boolean"},
                ],
            }
        ),
        check,
    )


def test_tuple_empty():
    assert_all_examples(
        from_schema({"type": "tuple", "elements": []}), lambda x: x == ()
    )


def test_set_of_tuples():
    def check(x):
        return isinstance(x, set) and all(isinstance(elem, tuple) for elem in x)

    assert_all_examples(
        from_schema(
            {
                "type": "set",
                "elements": {
                    "type": "tuple",
                    "elements": [{"type": "integer"}, {"type": "integer"}],
                },
                "min_size": 0,
            }
        ),
        check,
    )


def test_nested_dict_of_lists():
    def check(x):
        if not isinstance(x, list) or not 1 <= len(x) <= 2:
            return False
        for key, val in x:
            if not isinstance(key, str) or not isinstance(val, list) or len(val) > 5:
                return False
        return True

    assert_all_examples(
        from_schema(
            {
                "type": "dict",
                "keys": {"type": "string", "min_size": 1},
                "values": {
                    "type": "list",
                    "elements": {"type": "integer"},
                    "min_size": 0,
                    "max_size": 5,
                },
                "min_size": 1,
                "max_size": 2,
            }
        ),
        check,
    )


def test_empty_schema():
    with pytest.raises(ValueError, match="Unsupported schema"):
        from_schema({})


def test_unsupported_type():
    with pytest.raises(ValueError, match="Unsupported schema"):
        from_schema({"type": "unknown"})
