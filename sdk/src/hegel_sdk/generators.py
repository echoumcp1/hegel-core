from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, TypeVar

from hegel_sdk.client import (
    Labels,
    assume,
    collection,
    generate_from_schema,
    start_span,
    stop_span,
)

T = TypeVar("T")
U = TypeVar("U")


class Generator(ABC):
    """Base class for all generators.

    Generators produce values of type T and optionally carry a schema
    that describes the values they generate. Generators with a schema
    can be optimized into a single server request.
    """

    @abstractmethod
    def generate(self) -> Any:
        """Generate a value."""

    def map(self, f: Callable[[Any], Any]) -> "Generator":
        """Transform generated values using a function.

        The resulting generator has no schema since the transformation
        may invalidate the schema's semantics.
        """
        return MappedGenerator(self, f)

    def flat_map(self, f: Callable[[Any], "Generator"]) -> "FlatMappedGenerator":
        """Generate a value, then use it to create another generator.

        This is useful for dependent generation where the second value
        depends on the first.
        """
        return FlatMappedGenerator(self, f)

    def filter(
        self,
        predicate: Callable[[Any], bool],
    ) -> "FilteredGenerator":
        """Filter generated values using a predicate.

        If 3 consecutive values fail the predicate, calls assume(false).
        """
        return FilteredGenerator(self, predicate)


class BasicGenerator(Generator):
    """A generator with a schema and an optional client-side transform.

    When map() is called on a BasicGenerator, the schema is preserved
    and the transform functions are composed. This avoids falling back
    to compositional generation for mapped values.
    """

    def __init__(
        self,
        raw_schema: dict,
        transform: Callable[[Any], Any] | None = None,
    ):
        self._raw_schema = raw_schema
        self._transform = transform

    def generate(self) -> Any:
        raw = generate_from_schema(self._raw_schema)
        if self._transform is not None:
            return self._transform(raw)
        return raw

    def schema(self) -> dict | None:
        return self._raw_schema

    def map(self, f: Callable[[Any], Any]) -> "BasicGenerator":
        """Transform values while preserving the schema."""
        current_transform = self._transform
        if current_transform is not None:
            # Capture current_transform in a variable that mypy knows is not None
            ct: Callable[[Any], Any] = current_transform

            def composed(raw: Any) -> Any:
                return f(ct(raw))

            return BasicGenerator(self._raw_schema, composed)
        else:
            return BasicGenerator(self._raw_schema, f)


class MappedGenerator(Generator):
    """A generator that transforms values from another generator."""

    def __init__(self, source: Generator, f: Callable[[Any], Any]):
        self._source = source
        self._f = f

    def generate(self) -> Any:
        start_span(Labels.MAPPED)
        try:
            value = self._source.generate()
            return self._f(value)
        finally:
            stop_span(discard=False)


class FlatMappedGenerator(Generator):
    """A generator for dependent generation."""

    def __init__(self, source: Generator, f: Callable[[Any], Generator]):
        self._source = source
        self._f = f

    def generate(self) -> Any:
        start_span(Labels.FLAT_MAP)
        try:
            first = self._source.generate()
            second_gen = self._f(first)
            return second_gen.generate()
        finally:
            stop_span(discard=False)


class FilteredGenerator(Generator):
    _MAX_ATTEMPTS = 3

    def __init__(
        self,
        source: Generator,
        predicate: Callable[[Any], bool],
    ):
        self._source = source
        self._predicate = predicate

    def generate(self) -> Any:
        for _ in range(self._MAX_ATTEMPTS):
            start_span(Labels.FILTER)
            value = self._source.generate()
            if self._predicate(value):
                stop_span(discard=False)
                return value
            stop_span(discard=True)
        # Too many failed attempts - reject this test case
        assume(condition=False)
        raise AssertionError("unreachable")


# =============================================================================
# Generator factory functions
# =============================================================================


def integers(min_value: int | None = None, max_value: int | None = None) -> Generator:
    """Generator for integers."""
    schema: dict = {"type": "integer"}
    if min_value is not None:
        schema["minimum"] = min_value
    if max_value is not None:
        schema["maximum"] = max_value
    return BasicGenerator(schema)


def floats(
    min_value: float | None = None,
    max_value: float | None = None,
    *,
    allow_nan: bool = False,
    allow_infinity: bool = False,
) -> Generator:
    """Generator for floating-point numbers."""
    schema: dict = {"type": "number"}
    if min_value is not None:
        schema["minimum"] = min_value
    if max_value is not None:
        schema["maximum"] = max_value
    schema["allow_nan"] = allow_nan
    schema["allow_infinity"] = allow_infinity
    schema["exclude_minimum"] = False
    schema["exclude_maximum"] = False
    schema["width"] = 64
    return BasicGenerator(schema)


def booleans(p: float = 0.5) -> Generator:
    """Generator for booleans."""
    return BasicGenerator({"type": "boolean", "p": p})


def text(min_size: int = 0, max_size: int | None = None) -> Generator:
    """Generator for text strings."""
    schema: dict = {"type": "string", "min_size": min_size}
    if max_size is not None:
        schema["max_size"] = max_size
    return BasicGenerator(schema)


def binary(min_size: int = 0, max_size: int | None = None) -> Generator:
    """Generator for binary data (returned as base64)."""
    schema: dict = {"type": "binary", "min_size": min_size}
    if max_size is not None:
        schema["max_size"] = max_size
    return BasicGenerator(schema)


def lists(
    elements: Generator,
    min_size: int = 0,
    max_size: int | None = None,
) -> Generator:
    """Generator for lists."""
    if isinstance(elements, BasicGenerator):
        # Element is BasicGenerator - compose into BasicGenerator
        raw_schema: dict = {
            "type": "list",
            "elements": elements._raw_schema,
            "min_size": min_size,
        }
        if max_size is not None:
            raw_schema["max_size"] = max_size
        transform = elements._transform
        if transform is not None:
            # Capture transform in a variable that mypy knows is not None
            t: Callable[[Any], Any] = transform

            def list_transform(raw_list: list) -> list:
                return [t(x) for x in raw_list]

            return BasicGenerator(raw_schema, list_transform)
        else:
            return BasicGenerator(raw_schema)
    else:
        # Element is not BasicGenerator - fall back to compositional
        return CompositeListGenerator(elements, min_size, max_size)


class CompositeListGenerator(Generator):
    """A list generator for elements without a schema."""

    def __init__(self, elements: Generator, min_size: int, max_size: int | None):
        self._elements = elements
        self._min_size = min_size
        self._max_size = max_size
        self._collection = collection(
            name="composite_list",
            min_size=min_size,
            max_size=max_size,
        )

    def generate(self) -> list:
        start_span(Labels.LIST)
        try:
            result = []
            while self._collection.more():
                result.append(self._elements.generate())
            return result
        finally:
            stop_span()


def tuples(*elements: Generator) -> Generator:
    """Generator for tuples."""
    # Only if ALL elements are BasicGenerators can we compose into BasicGenerator
    if all(isinstance(e, BasicGenerator) for e in elements):
        # Cast to list of BasicGenerator for type safety
        basic_elements: list[BasicGenerator] = [
            e for e in elements if isinstance(e, BasicGenerator)
        ]
        raw_schemas = [e._raw_schema for e in basic_elements]
        transforms: list[Callable[[Any], Any] | None] = [
            e._transform for e in basic_elements
        ]
        combined_schema = {"type": "tuple", "elements": raw_schemas}

        # Check if all transforms are identity (None)
        if all(t is None for t in transforms):
            return BasicGenerator(combined_schema)
        else:
            # Apply transforms to each element
            def apply_transforms(
                raw_tuple: list, ts: list[Callable[[Any], Any] | None] = transforms
            ) -> tuple:
                return tuple(
                    t(r) if t is not None else r
                    for t, r in zip(ts, raw_tuple, strict=True)
                )

            return BasicGenerator(combined_schema, apply_transforms)
    else:
        # At least one element is not a BasicGenerator - fall back to compositional
        return CompositeTupleGenerator(list(elements))


class CompositeTupleGenerator(Generator):
    """A tuple generator for elements without schemas."""

    def __init__(self, elements: list[Generator]):
        self._elements = elements

    def generate(self) -> tuple:
        start_span(Labels.TUPLE)
        try:
            result = []
            for elem in self._elements:
                result.append(elem.generate())
            return tuple(result)
        finally:
            stop_span(discard=False)


def just(value: Any) -> BasicGenerator:
    """Generator that always returns the same value."""
    return BasicGenerator({"const": None}, lambda _: value)


def sampled_from(values: list) -> BasicGenerator:
    """Generator that samples uniformly from a list of values.

    Works with any type, including non-JSON-serializable objects.
    Returns the original objects (identity preserved).
    """
    elements = list(values)
    if not elements:
        raise ValueError("sampled_from requires at least one element")
    schema = {
        "type": "integer",
        "minimum": 0,
        "maximum": len(elements) - 1,
    }
    return BasicGenerator(schema, lambda idx: elements[idx])


def one_of(*generators: Generator) -> Generator:
    """Generator that picks from one of several generators.

    When all generators are BasicGenerators, composes into a single schema.
    Uses tagged tuples [tag, value] when transforms are present so the
    correct transform can be applied.
    """
    all_basic = all(isinstance(g, BasicGenerator) for g in generators)

    if all_basic:
        # Cast to list of BasicGenerator for type safety
        basic_generators: list[BasicGenerator] = [
            g for g in generators if isinstance(g, BasicGenerator)
        ]

        # Check if all have identity transforms - can use simpler one_of
        all_identity = all(g._transform is None for g in basic_generators)
        if all_identity:
            schemas = [g._raw_schema for g in basic_generators]
            return BasicGenerator({"one_of": schemas})

        # Use one_of of tagged tuples: each branch is [const_tag, value]
        # This lets us know which transform to apply
        tagged_schemas = [
            {"type": "tuple", "elements": [{"const": i}, g._raw_schema]}
            for i, g in enumerate(basic_generators)
        ]
        transforms: list[Callable[[Any], Any] | None] = [
            g._transform for g in basic_generators
        ]

        def apply_tagged_transform(
            tagged: list, ts: list[Callable[[Any], Any] | None] = transforms
        ) -> Any:
            tag, value = tagged
            transform = ts[tag]
            if transform is not None:
                return transform(value)
            return value

        return BasicGenerator({"one_of": tagged_schemas}, apply_tagged_transform)

    # Not all BasicGenerator - use compositional
    return CompositeOneOfGenerator(list(generators))


class CompositeOneOfGenerator(Generator):
    """A one_of generator for generators without schemas."""

    def __init__(self, generators: list[Generator]):
        self._generators = generators

    def generate(self) -> Any:
        start_span(Labels.ONE_OF)
        try:
            # Pick which generator to use
            index = generate_from_schema(
                {"type": "integer", "minimum": 0, "maximum": len(self._generators) - 1},
            )
            return self._generators[index].generate()
        finally:
            stop_span(discard=False)


def optional(element: Generator) -> Generator:
    """Generator for optional values (None or a value)."""
    return one_of(just(None), element)


def dicts(
    keys: Generator,
    values: Generator,
    min_size: int = 0,
    max_size: int | None = None,
) -> Generator:
    """Generator for dictionaries."""
    if isinstance(keys, BasicGenerator) and isinstance(values, BasicGenerator):
        # Both are BasicGenerator - compose into BasicGenerator
        basic_keys: BasicGenerator = keys
        basic_values: BasicGenerator = values
        raw_schema: dict = {
            "type": "dict",
            "keys": basic_keys._raw_schema,
            "values": basic_values._raw_schema,
            "min_size": min_size,
        }
        if max_size is not None:
            raw_schema["max_size"] = max_size

        key_transform = basic_keys._transform
        value_transform = basic_values._transform

        # Build appropriate transform for the dict
        if key_transform is None and value_transform is None:
            # Both identity - just convert pairs to dict
            def items_to_dict(items: list) -> dict:
                return dict(items)

            return BasicGenerator(raw_schema, items_to_dict)
        else:
            # Apply transforms to keys and/or values
            def transform_dict(
                items: list,
                kt: Callable[[Any], Any] | None = key_transform,
                vt: Callable[[Any], Any] | None = value_transform,
            ) -> dict:
                result = {}
                for k, v in items:
                    key = kt(k) if kt is not None else k
                    val = vt(v) if vt is not None else v
                    result[key] = val
                return result

            return BasicGenerator(raw_schema, transform_dict)
    else:
        return CompositeDictGenerator(keys, values, min_size, max_size)


class CompositeDictGenerator(Generator):
    """A dict generator for elements without schemas."""

    def __init__(
        self,
        keys: Generator,
        values: Generator,
        min_size: int,
        max_size: int | None,
    ):
        self._keys = keys
        self._values = values
        self._min_size = min_size
        self._max_size = max_size

    def generate(self) -> dict:
        start_span(Labels.MAP)
        try:
            max_sz = (
                self._max_size if self._max_size is not None else self._min_size + 10
            )
            size = generate_from_schema(
                {"type": "integer", "minimum": self._min_size, "maximum": max_sz},
            )
            result = {}
            for _ in range(size):
                start_span(Labels.MAP_ENTRY)
                key = self._keys.generate()
                value = self._values.generate()
                result[key] = value
                stop_span()
            return result
        finally:
            stop_span()
