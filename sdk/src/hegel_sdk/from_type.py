import types
from collections.abc import Callable
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Union, get_args, get_origin

from hegel_sdk.client import Labels, start_span, stop_span
from hegel_sdk.generators import (
    BasicGenerator,
    Generator,
    binary,
    booleans,
    dicts,
    floats,
    integers,
    just,
    lists,
    one_of,
    optional,
    sampled_from,
    text,
    tuples,
)


def _build_dataclass_generator(
    dataclass_type: type,
    field_generators: dict[str, Generator],
) -> Generator:
    """Build a generator for a dataclass type from per-field generators."""
    all_basic = all(isinstance(g, BasicGenerator) for g in field_generators.values())

    if all_basic:
        properties = {}
        required = []
        transforms: dict[str, Callable | None] = {}

        for field in fields(dataclass_type):
            gen = field_generators[field.name]
            assert isinstance(gen, BasicGenerator)
            properties[field.name] = gen._raw_schema
            transforms[field.name] = gen._transform
            required.append(field.name)

        schema = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

        def make_instance(raw: Any) -> Any:
            kwargs = {}
            for field_name, raw_value in raw.items():
                transform = transforms.get(field_name)
                if transform is not None:
                    kwargs[field_name] = transform(raw_value)
                else:
                    kwargs[field_name] = raw_value
            return dataclass_type(**kwargs)

        return BasicGenerator(schema, make_instance)

    class _CompositeDataclassGenerator(Generator):
        def generate(self_inner) -> Any:
            start_span(Labels.FIXED_DICT)
            try:
                kwargs = {}
                for field in fields(dataclass_type):
                    kwargs[field.name] = field_generators[field.name].generate()
                return dataclass_type(**kwargs)
            finally:
                stop_span()

    return _CompositeDataclassGenerator()


class DataclassGenerator:
    """Builder for dataclass generators with per-field customization.

    Use with_field() to override specific field generators, then call
    build() to get the final Generator.
    """

    def __init__(self, dataclass_type: type):
        if not is_dataclass(dataclass_type):
            raise TypeError(f"{dataclass_type} is not a dataclass")
        self._type = dataclass_type
        self._field_generators: dict[str, Generator] = {}
        for field in fields(dataclass_type):
            self._field_generators[field.name] = from_type(field.type)

    def with_field(self, field_name: str, gen: Generator) -> "DataclassGenerator":
        """Override the generator for a specific field."""
        if field_name not in self._field_generators:
            raise ValueError(f"Unknown field: {field_name}")
        new_gen = DataclassGenerator.__new__(DataclassGenerator)
        new_gen._type = self._type
        new_gen._field_generators = dict(self._field_generators)
        new_gen._field_generators[field_name] = gen
        return new_gen

    def build(self) -> Generator:
        """Build the generator for the current field configuration."""
        return _build_dataclass_generator(self._type, self._field_generators)


def from_type(type_hint: Any) -> Generator:
    """Generate values matching the given type hint.

    Supports:
    - Primitive types: int, float, str, bool, type(None)
    - Container types: list, dict, tuple, set
    - Optional[T] and Union[T, None]
    - Dataclasses
    - Enums
    """
    # Handle None type
    if type_hint is type(None):
        return just(None)

    # Primitives
    if type_hint is int:
        return integers()
    if type_hint is float:
        return floats()
    if type_hint is str:
        return text()
    if type_hint is bool:
        return booleans()
    if type_hint is bytes:
        return binary()

    # Get origin for generic types
    origin = get_origin(type_hint)
    args = get_args(type_hint)

    # Optional[T] is Union[T, None] or T | None (types.UnionType in Python 3.10+)
    if origin is Union or isinstance(type_hint, types.UnionType):
        # Filter out NoneType
        non_none_args = [a for a in args if a is not type(None)]
        if len(non_none_args) == 1 and type(None) in args:
            # This is Optional[T]
            return optional(from_type(non_none_args[0]))
        else:
            # General Union
            return one_of(*[from_type(a) for a in args])

    # List[T]
    if origin is list:
        if args:
            return lists(from_type(args[0]))
        return lists(integers())  # Default to list[int]

    # Dict[K, V]
    if origin is dict:
        if len(args) >= 2:
            return dicts(from_type(args[0]), from_type(args[1]))
        return dicts(text(), integers())  # Default

    # Tuple[T, ...]
    if origin is tuple:
        if args:
            return tuples(*[from_type(a) for a in args])
        return tuples()

    # Set[T] - generate as list, convert to set
    if origin is set:
        if args:
            return lists(from_type(args[0])).map(set)
        return lists(integers()).map(set)

    # Check for Enum
    if isinstance(type_hint, type) and issubclass(type_hint, Enum):
        return sampled_from(list(type_hint))

    # Check for dataclass
    if is_dataclass(type_hint) and isinstance(type_hint, type):
        field_gens = {f.name: from_type(f.type) for f in fields(type_hint)}
        return _build_dataclass_generator(type_hint, field_gens)

    raise TypeError(f"Cannot generate values for type: {type_hint}")
