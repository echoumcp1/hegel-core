"""Tests for parser.py uncovered paths: binary, object, url, domain types."""

import base64

from hypothesis import given, settings

from hegel.parser import from_schema


def schema_test(schema):
    def accept(test):
        return settings(database=None, max_examples=1)(given(from_schema(schema))(test))

    return accept


@schema_test({"type": "binary", "min_size": 1, "max_size": 10})
def test_binary_schema(example):
    """Test binary type returns base64 encoded string."""
    assert isinstance(example, str)
    # Should be valid base64
    decoded = base64.b64decode(example)
    assert 1 <= len(decoded) <= 10


@schema_test(
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer", "minimum": 0, "maximum": 100},
        },
    },
)
def test_object_schema(example):
    """Test object type with properties."""
    assert isinstance(example, dict)
    assert "name" in example
    assert "age" in example
    assert isinstance(example["name"], str)
    assert isinstance(example["age"], int)


@schema_test({"type": "url"})
def test_url_schema(example):
    """Test url type."""
    assert isinstance(example, str)
    assert "://" in example


@schema_test({"type": "domain"})
def test_domain_schema(example):
    """Test domain type."""
    assert isinstance(example, str)
    assert "." in example or len(example) > 0


@schema_test({"type": "domain", "max_length": 50})
def test_domain_with_max_length(example):
    """Test domain type with max_length."""
    assert isinstance(example, str)
    assert len(example) <= 50
