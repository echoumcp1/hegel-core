"""
Hegel Python SDK - Reference implementation for writing property tests.

This SDK provides the API for writing property-based tests using Hegel.

Example usage:

    from hegel_sdk import hegel, integers, lists

    @hegel
    def test_addition_is_commutative():
        a = integers().generate()
        b = integers().generate()
        assert a + b == b + a
"""

from hegel_sdk.client import (
    AssumeRejected,
    Client,
    DataExhausted,
    Labels,
    assume,
    collection,
    generate_from_schema,
    note,
    start_span,
    stop_span,
    target,
)
from hegel_sdk.from_type import (
    DataclassGenerator,
    from_type,
)
from hegel_sdk.generators import (
    BasicGenerator,
    CompositeDictGenerator,
    CompositeListGenerator,
    CompositeOneOfGenerator,
    CompositeTupleGenerator,
    FilteredGenerator,
    FlatMappedGenerator,
    Generator,
    MappedGenerator,
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
from hegel_sdk.session import (
    hegel,
    run_hegel_test,
)

__all__ = [
    "AssumeRejected",
    "BasicGenerator",
    "Client",
    "CompositeDictGenerator",
    "CompositeListGenerator",
    "CompositeOneOfGenerator",
    "CompositeTupleGenerator",
    "DataExhausted",
    "DataclassGenerator",
    "FilteredGenerator",
    "FlatMappedGenerator",
    "Generator",
    "Labels",
    "MappedGenerator",
    "assume",
    "binary",
    "booleans",
    "collection",
    "dicts",
    "floats",
    "from_type",
    "generate_from_schema",
    "hegel",
    "integers",
    "just",
    "lists",
    "note",
    "one_of",
    "optional",
    "run_hegel_test",
    "sampled_from",
    "start_span",
    "stop_span",
    "target",
    "text",
    "tuples",
]
