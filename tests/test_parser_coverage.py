"""Tests for parser.py uncovered paths: binary, object, url, domain types."""

import base64

from hegel.parser import from_schema


def test_binary_schema():
    """Test binary type returns base64 encoded string."""
    v = from_schema({"type": "binary", "min_size": 1, "max_size": 10}).example()
    assert isinstance(v, str)
    # Should be valid base64
    decoded = base64.b64decode(v)
    assert 1 <= len(decoded) <= 10


def test_object_schema():
    """Test object type with properties."""
    v = from_schema(
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer", "minimum": 0, "maximum": 100},
            },
        },
    ).example()
    assert isinstance(v, dict)
    assert "name" in v
    assert "age" in v
    assert isinstance(v["name"], str)
    assert isinstance(v["age"], int)


def test_url_schema():
    """Test url type."""
    v = from_schema({"type": "url"}).example()
    assert isinstance(v, str)
    assert "://" in v


def test_domain_schema():
    """Test domain type."""
    v = from_schema({"type": "domain"}).example()
    assert isinstance(v, str)
    assert "." in v or len(v) > 0


def test_domain_with_max_length():
    """Test domain type with max_length."""
    v = from_schema({"type": "domain", "max_length": 50}).example()
    assert isinstance(v, str)
    assert len(v) <= 50
