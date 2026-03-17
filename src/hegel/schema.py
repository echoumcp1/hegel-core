import hashlib
import json
from typing import Any

from hypothesis import strategies as st
from hypothesis.internal.cache import LRUCache
from hypothesis.internal.conjecture.data import ConjectureData
from hypothesis.provisional import domains, urls
from hypothesis.strategies import SearchStrategy

FROM_SCHEMA_CACHE: LRUCache = LRUCache(1024)


class BooleansStrategy(SearchStrategy[bool]):
    def __init__(self, p: float):
        super().__init__()
        self.p = p

    def do_draw(self, data: ConjectureData) -> bool:
        return data.draw_boolean(p=self.p)


def _from_schema(schema: dict[str, Any]) -> SearchStrategy[Any]:
    if "const" in schema:
        assert len(schema) == 1
        return st.just(schema["const"])
    if "sampled_from" in schema:
        assert len(schema) == 1
        return st.sampled_from(schema["sampled_from"])
    if "one_of" in schema:
        assert len(schema) == 1
        return st.one_of([_from_schema(s) for s in schema["one_of"]])

    schema_type = schema["type"]

    if schema_type == "null":
        return st.none()
    if schema_type == "boolean":
        return BooleansStrategy(schema.get("p", 0.5))
    if schema_type == "integer":
        return st.integers(
            min_value=schema.get("min_value"),
            max_value=schema.get("max_value"),
        )
    if schema_type == "float":
        return st.floats(
            schema.get("min_value"),
            schema.get("max_value"),
            allow_nan=schema.get("allow_nan"),
            allow_infinity=schema.get("allow_infinity"),
            width=schema.get("width", 64),
            exclude_min=schema.get("exclude_min", False),
            exclude_max=schema.get("exclude_max", False),
        )
    if schema_type == "string":
        # Exclude null bytes due to reflect-cpp truncation bug:
        # https://github.com/getml/reflect-cpp/issues/559
        # Exclude surrogates (Cs category) as they're invalid in UTF-8/JSON
        return st.text(
            alphabet=st.characters(
                blacklist_characters="\x00",
                blacklist_categories=("Cs",),  # type: ignore[arg-type]
            ),
            min_size=schema.get("min_size", 0),
            max_size=schema.get("max_size"),
        )
    if schema_type == "binary":
        return st.binary(
            min_size=schema.get("min_size", 0),
            max_size=schema.get("max_size"),
        )
    if schema_type == "regex":
        return st.from_regex(
            schema["pattern"],
            fullmatch=schema.get("fullmatch", False),
        )
    if schema_type == "list":
        return st.lists(
            _from_schema(schema["elements"]),
            min_size=schema.get("min_size", 0),
            max_size=schema.get("max_size"),
            unique=schema.get("unique", False),
        )
    if schema_type == "dict":
        # Possibly "dict" should be removed entirely and replaced by libraries calling "tuple"
        # themselves.
        #
        # We initially returned a tuple here to avoid json requiring string keys in dicts,
        # but since we switched to cbor that's no longer a problem.
        return st.dictionaries(
            keys=_from_schema(schema["keys"]),
            values=_from_schema(schema["values"]),
            min_size=schema.get("min_size", 0),
            max_size=schema.get("max_size"),
        ).map(lambda d: list(d.items()))
    if schema_type == "tuple":
        elements = [_from_schema(s) for s in schema["elements"]]
        return st.tuples(*elements)
    if schema_type == "email":
        return st.emails()
    if schema_type == "url":
        return urls()
    if schema_type == "domain":
        return domains(max_length=schema.get("max_length", 255))
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


def from_schema(schema: dict[str, Any]) -> SearchStrategy[Any]:
    key = json.dumps(schema, sort_keys=True).encode("utf-8")
    key = hashlib.sha1(key).digest()[:32]
    try:
        return FROM_SCHEMA_CACHE[key]
    except KeyError:
        result = _from_schema(schema)
        FROM_SCHEMA_CACHE[key] = result
        return result
