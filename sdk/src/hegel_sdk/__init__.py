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
    # Client
    "AssumeRejected",
    "Client",
    "DataExhausted",
    "Labels",
    "assume",
    "collection",
    "generate_from_schema",
    "note",
    "start_span",
    "stop_span",
    "target",
    # Generators
    "Generator",
    "BasicGenerator",
    "MappedGenerator",
    "FlatMappedGenerator",
    "FilteredGenerator",
    "CompositeListGenerator",
    "CompositeTupleGenerator",
    "CompositeOneOfGenerator",
    "CompositeDictGenerator",
    # Factory functions
    "integers",
    "floats",
    "booleans",
    "text",
    "binary",
    "lists",
    "tuples",
    "just",
    "sampled_from",
    "one_of",
    "optional",
    "dicts",
    # Type generation
    "from_type",
    "DataclassGenerator",
    # Session management
    "hegel",
    "run_hegel_test",
]
