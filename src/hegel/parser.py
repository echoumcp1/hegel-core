import base64
from typing import Any

from hypothesis import strategies as st
from hypothesis.internal.conjecture.data import ConjectureData
from hypothesis.provisional import domains, urls
from hypothesis.strategies import SearchStrategy


class BooleansStrategy(SearchStrategy[bool]):
    """Hypothesis strategy for booleans with configurable probability."""

    def __init__(self, p: float):
        super().__init__()
        self.p = p

    def do_draw(self, data: ConjectureData) -> bool:
        return data.draw_boolean(p=self.p)


def from_schema(schema: dict[str, Any]) -> SearchStrategy[Any]:
    """Convert a JSON schema to a Hypothesis strategy."""
    if "const" in schema:
        return st.just(schema["const"])
    if "sampled_from" in schema:
        return st.sampled_from(schema["sampled_from"])
    if "one_of" in schema:
        return st.one_of([from_schema(s) for s in schema["one_of"]])

    schema_type = schema.get("type")

    if schema_type == "null":
        return st.none()
    if schema_type == "boolean":
        return BooleansStrategy(schema.get("p", 0.5))
    if schema_type == "integer":
        return st.integers(
            min_value=schema.get("minimum"),
            max_value=schema.get("maximum"),
        )
    if schema_type == "number":
        return st.floats(
            schema.get("minimum"),
            schema.get("maximum"),
            allow_nan=schema["allow_nan"],
            allow_infinity=schema["allow_infinity"],
            width=schema["width"],
            exclude_min=schema["exclude_minimum"],
            exclude_max=schema["exclude_maximum"],
        )
    if schema_type == "string":
        # Exclude null bytes due to reflect-cpp truncation bug:
        # https://github.com/getml/reflect-cpp/issues/559
        # Exclude surrogates (Cs category) as they're invalid in UTF-8/JSON
        return st.text(
            alphabet=st.characters(
                blacklist_characters="\x00", blacklist_categories=("Cs",)
            ),
            min_size=schema["min_size"],
            max_size=schema.get("max_size"),
        )
    if schema_type == "binary":
        return st.binary(
            min_size=schema["min_size"],
            max_size=schema.get("max_size"),
        ).map(lambda b: base64.b64encode(b).decode("ascii"))
    if schema_type == "regex":
        return st.from_regex(
            schema["pattern"],
            fullmatch=schema["fullmatch"],
        )
    if schema_type == "list":
        return st.lists(
            from_schema(schema["elements"]),
            min_size=schema["min_size"],
            max_size=schema.get("max_size"),
        )
    if schema_type == "set":
        return st.sets(
            from_schema(schema["elements"]),
            min_size=schema["min_size"],
            max_size=schema.get("max_size"),
        )
    if schema_type == "dict":
        # Convert to [[k, v], ...] format to support non-string keys
        return st.dictionaries(
            keys=from_schema(schema["keys"]),
            values=from_schema(schema["values"]),
            min_size=schema["min_size"],
            max_size=schema.get("max_size"),
        ).map(lambda d: list(d.items()))
    if schema_type == "tuple":
        elements = [from_schema(s) for s in schema["elements"]]
        return st.tuples(*elements)
    if schema_type == "object":
        properties = schema.get("properties", {})
        return st.fixed_dictionaries(
            {
                name: from_schema(prop_schema)
                for name, prop_schema in properties.items()
            },
        )
    if schema_type == "email":
        return st.emails()
    if schema_type == "url":
        return urls()
    if schema_type == "domain":
        return domains(max_length=schema["max_length"])
    if schema_type == "ipv4":
        return st.ip_addresses(v=4).map(str)
    if schema_type == "ipv6":
        return st.ip_addresses(v=6).map(str)
    if schema_type == "date":
        return st.dates().map(lambda d: d.isoformat())
    if schema_type == "time":
        return st.times().map(lambda t: t.isoformat())
    if schema_type == "datetime":
        return st.datetimes().map(lambda dt: dt.isoformat())

    raise ValueError(f"Unsupported schema: {schema}")
