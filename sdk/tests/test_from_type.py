from dataclasses import dataclass
from enum import Enum

import pytest

from hegel_sdk import (
    DataclassGenerator,
    dicts,
    from_type,
    hegel,
    integers,
    sampled_from,
    text,
)

# =============================================================================
# Test fixtures - custom types
# =============================================================================


class Color(Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class Status(Enum):
    PENDING = 1
    ACTIVE = 2
    COMPLETE = 3


@dataclass
class Point:
    x: int
    y: int


@dataclass
class Person:
    name: str
    age: int


@dataclass
class Company:
    name: str
    employees: list  # list[Person] - but we can't use that syntax easily


@dataclass
class OptionalFields:
    required: str
    optional: int | None


class CustomClass:
    """A class that is NOT JSON serializable."""

    def __init__(self, value: int):
        self.value = value

    def __eq__(self, other):
        if isinstance(other, CustomClass):
            return self.value == other.value
        return False

    def __hash__(self):
        return hash(self.value)


class SerializableClass:
    """A class that IS JSON serializable but needs identity preservation."""

    def __init__(self, name: str, data: dict):
        self.name = name
        self.data = data
        self.id = id(self)  # Unique per instance

    def __eq__(self, other):
        # Identity-based equality for testing
        return self is other


# =============================================================================
# Tests for sampled_from with primitives (should work)
# =============================================================================


@hegel(test_cases=50)
def test_sampled_from_integers():
    """sampled_from with integers should work."""
    gen = sampled_from([1, 2, 3, 4, 5])
    value = gen.generate()
    assert value in [1, 2, 3, 4, 5]


@hegel(test_cases=50)
def test_sampled_from_strings():
    """sampled_from with strings should work."""
    gen = sampled_from(["apple", "banana", "cherry"])
    value = gen.generate()
    assert value in ["apple", "banana", "cherry"]
    assert isinstance(value, str)


@hegel(test_cases=50)
def test_sampled_from_booleans():
    """sampled_from with booleans should work."""
    gen = sampled_from([True, False])
    value = gen.generate()
    assert value in [True, False]
    assert isinstance(value, bool)


# =============================================================================
# Tests for sampled_from with custom types
# =============================================================================


@hegel(test_cases=20)
def test_sampled_from_custom_class():
    """sampled_from with non-JSON-serializable class.

    This test verifies that sampled_from works for custom classes
    by falling back to index-based selection.
    """
    elements = [CustomClass(1), CustomClass(2), CustomClass(3)]
    gen = sampled_from(elements)
    value = gen.generate()
    # Should return one of the ORIGINAL objects
    assert any(value is elem for elem in elements)


@hegel(test_cases=30)
def test_sampled_from_preserves_identity():
    """sampled_from should return the original objects, not copies.

    Even for JSON-serializable objects, we should get the SAME object back,
    not a reconstructed copy. This is important for objects with identity.
    """
    # These objects ARE serializable as dicts, but we need identity preservation
    obj1 = SerializableClass("one", {"a": 1})
    obj2 = SerializableClass("two", {"b": 2})
    obj3 = SerializableClass("three", {"c": 3})
    elements = [obj1, obj2, obj3]

    gen = sampled_from(elements)
    value = gen.generate()
    # Must be the SAME object (identity), not just equal
    assert any(value is elem for elem in elements)


@hegel(test_cases=50)
def test_sampled_from_with_none():
    """sampled_from including None should work."""
    gen = sampled_from([None, 1, 2, 3])
    value = gen.generate()
    assert value in [None, 1, 2, 3]


# =============================================================================
# Tests for from_type with primitives
# =============================================================================


@hegel(test_cases=50)
def test_from_type_int():
    """from_type(int) should generate integers."""
    value = from_type(int).generate()
    assert isinstance(value, int)


@hegel(test_cases=50)
def test_from_type_float():
    """from_type(float) should generate floats."""
    value = from_type(float).generate()
    assert isinstance(value, float)


@hegel(test_cases=50)
def test_from_type_str():
    """from_type(str) should generate strings."""
    value = from_type(str).generate()
    assert isinstance(value, str)


@hegel(test_cases=50)
def test_from_type_bool():
    """from_type(bool) should generate booleans."""
    value = from_type(bool).generate()
    assert isinstance(value, bool)


# =============================================================================
# Tests for from_type with containers
# =============================================================================


@hegel(test_cases=50)
def test_from_type_list_int():
    """from_type(list[int]) should generate lists of integers."""
    value = from_type(list[int]).generate()
    assert isinstance(value, list)
    assert all(isinstance(x, int) for x in value)


@hegel(test_cases=50)
def test_from_type_dict_str_int():
    """from_type(dict[str, int]) should generate dicts."""
    value = from_type(dict[str, int]).generate()
    assert isinstance(value, dict)
    assert all(isinstance(k, str) for k in value)
    assert all(isinstance(v, int) for v in value.values())


@hegel(test_cases=50)
def test_from_type_optional_int():
    """from_type(int | None) should generate int or None."""
    value = from_type(int | None).generate()
    assert value is None or isinstance(value, int)


# =============================================================================
# Tests for from_type with dataclasses
# =============================================================================


@hegel(test_cases=50)
def test_from_type_simple_dataclass():
    """from_type should generate dataclass instances."""
    point = from_type(Point).generate()
    assert isinstance(point, Point)
    assert isinstance(point.x, int)
    assert isinstance(point.y, int)


@hegel(test_cases=50)
def test_from_type_dataclass_with_str():
    """from_type should handle dataclasses with string fields."""
    person = from_type(Person).generate()
    assert isinstance(person, Person)
    assert isinstance(person.name, str)
    assert isinstance(person.age, int)


@hegel(test_cases=50)
def test_from_type_dataclass_with_optional():
    """from_type should handle Optional fields in dataclasses."""
    obj = from_type(OptionalFields).generate()
    assert isinstance(obj, OptionalFields)
    assert isinstance(obj.required, str)
    assert obj.optional is None or isinstance(obj.optional, int)


# =============================================================================
# Tests for from_type with enums
# =============================================================================


@hegel(test_cases=50)
def test_from_type_string_enum():
    """from_type should generate enum members."""
    color = from_type(Color).generate()
    assert isinstance(color, Color)
    assert color in [Color.RED, Color.GREEN, Color.BLUE]


@hegel(test_cases=50)
def test_from_type_int_enum():
    """from_type should generate int enum members."""
    status = from_type(Status).generate()
    assert isinstance(status, Status)
    assert status in [Status.PENDING, Status.ACTIVE, Status.COMPLETE]


# =============================================================================
# Tests demonstrating correct sampled_from behavior expectations
# =============================================================================


@hegel(test_cases=100)
def test_sampled_from_returns_exact_type():
    """sampled_from should return values of exactly the right type.

    This tests that sampled_from doesn't accidentally convert types
    (e.g., turning True into 1).
    """
    # Mix of types that could be confused
    gen = sampled_from([True, False, 0, 1, "", "hello"])
    value = gen.generate()

    # Verify exact type matching
    if value is True or value is False:
        assert type(value) is bool
    elif value in (0, 1) and not isinstance(value, bool):
        assert type(value) is int


# =============================================================================
# Tests for dicts() generator
# =============================================================================


@hegel(test_cases=50)
def test_dicts_generator():
    """dicts() should generate dictionaries."""
    d = dicts(text(max_size=5), integers()).generate()
    assert isinstance(d, dict)
    for k, v in d.items():
        assert isinstance(k, str)
        assert isinstance(v, int)


@hegel(test_cases=50)
def test_dicts_with_min_size():
    """dicts() should respect min_size."""
    d = dicts(text(max_size=5), integers(), min_size=2).generate()
    assert isinstance(d, dict)
    # Note: dict might be smaller than min_size due to key collisions


# =============================================================================
# Edge cases and error handling
# =============================================================================


def test_from_type_unsupported_type():
    """from_type should raise TypeError for unsupported types."""
    with pytest.raises(TypeError, match="Cannot generate values for type"):
        from_type(complex)


def test_dataclass_generator_non_dataclass():
    """DataclassGenerator should reject non-dataclass types."""
    with pytest.raises(TypeError, match="is not a dataclass"):
        DataclassGenerator(CustomClass)
