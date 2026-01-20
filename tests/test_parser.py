import pytest

from hegel.parser import from_schema


def test_null():
    assert from_schema({"type": "null"}).example() is None


def test_boolean():
    assert from_schema({"type": "boolean"}).example() in [True, False]


def test_integer():
    v = from_schema({"type": "integer", "minimum": 0, "maximum": 10}).example()
    assert isinstance(v, int)
    assert 0 <= v <= 10


def test_number():
    v = from_schema({"type": "number", "minimum": 0.0, "maximum": 1.0}).example()
    assert isinstance(v, float)
    assert 0.0 <= v <= 1.0


def test_number_exclusive():
    v = from_schema(
        {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "excludeMinimum": True,
            "excludeMaximum": True,
        }
    ).example()
    assert 0.0 < v < 1.0


def test_string():
    v = from_schema({"type": "string", "minLength": 1, "maxLength": 5}).example()
    assert isinstance(v, str)
    assert 1 <= len(v) <= 5


def test_string_pattern():
    v = from_schema({"type": "string", "pattern": r"^[a-z]+$"}).example()
    assert v.isalpha() and v.islower()


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
    import re

    v = from_schema({"type": "date"}).example()
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", v)


def test_time():
    import re

    v = from_schema({"type": "time"}).example()
    assert re.match(r"^\d{2}:\d{2}:\d{2}", v)


def test_datetime():
    v = from_schema({"type": "datetime"}).example()
    assert "T" in v


def test_const():
    assert from_schema({"const": 42}).example() == 42
    assert from_schema({"const": "hello"}).example() == "hello"


def test_enum():
    v = from_schema({"enum": [1, 2, 3]}).example()
    assert v in [1, 2, 3]


def test_anyOf():
    v = from_schema({"anyOf": [{"type": "integer"}, {"type": "string"}]}).example()
    assert isinstance(v, (int, str))


def test_oneOf():
    v = from_schema({"oneOf": [{"type": "boolean"}, {"type": "null"}]}).example()
    assert v is None or isinstance(v, bool)


def test_list():
    schema = {"type": "list", "elements": {"type": "integer"}}
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
        "keys": {"type": "string", "minLength": 1},
        "values": {"type": "integer"},
    }
    v = from_schema(schema).example()
    assert isinstance(v, dict)
    assert all(isinstance(k, str) and len(k) >= 1 for k in v)
    assert all(isinstance(x, int) for x in v.values())


def test_dict_size():
    schema = {
        "type": "dict",
        "keys": {"type": "string"},
        "values": {"type": "integer"},
        "min_size": 1,
        "max_size": 3,
    }
    v = from_schema(schema).example()
    assert 1 <= len(v) <= 3


def test_dict_default_keys():
    schema = {"type": "dict", "values": {"type": "integer"}}
    v = from_schema(schema).example()
    assert all(isinstance(k, str) for k in v)


def test_tuple():
    schema = {
        "type": "tuple",
        "elements": [{"type": "integer"}, {"type": "string"}, {"type": "boolean"}],
    }
    v = from_schema(schema).example()
    assert len(v) == 3
    assert isinstance(v[0], int)
    assert isinstance(v[1], str)
    assert isinstance(v[2], bool)


def test_tuple_empty():
    schema = {"type": "tuple", "elements": []}
    assert from_schema(schema).example() == []


def test_nested_dict_of_lists():
    schema = {
        "type": "dict",
        "keys": {"type": "string", "minLength": 1},
        "values": {
            "type": "list",
            "elements": {"type": "integer"},
            "max_size": 5,
        },
        "min_size": 1,
        "max_size": 2,
    }
    v = from_schema(schema).example()
    assert 1 <= len(v) <= 2
    for key, val in v.items():
        assert isinstance(key, str)
        assert isinstance(val, list)
        assert len(val) <= 5


def test_empty_schema():
    v = from_schema({}).example()
    assert v is None or isinstance(v, (bool, int, float, str))


def test_unsupported_type():
    with pytest.raises(ValueError, match="Unsupported schema"):
        from_schema({"type": "unknown"}).example()
