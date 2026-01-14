"""Tests for schema processing functions."""

from hegel.__main__ import add_additional_properties_false


def test_non_dict_input_returns_unchanged():
    """Non-dict inputs should be returned unchanged."""
    assert add_additional_properties_false("not a dict") == "not a dict"
    assert add_additional_properties_false(123) == 123
    assert add_additional_properties_false(None) is None
    assert add_additional_properties_false([1, 2, 3]) == [1, 2, 3]


def test_adds_additional_properties_false_to_object_type():
    """Should add additionalProperties: false to object schemas."""
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    result = add_additional_properties_false(schema)
    assert result["additionalProperties"] is False


def test_preserves_existing_additional_properties():
    """Should not override existing additionalProperties."""
    schema = {"type": "object", "additionalProperties": True}
    result = add_additional_properties_false(schema)
    assert result["additionalProperties"] is True


def test_recurses_into_properties():
    """Should recurse into nested object properties."""
    schema = {
        "type": "object",
        "properties": {
            "nested": {"type": "object", "properties": {"x": {"type": "integer"}}}
        },
    }
    result = add_additional_properties_false(schema)
    assert result["additionalProperties"] is False
    assert result["properties"]["nested"]["additionalProperties"] is False


def test_recurses_into_defs():
    """Should recurse into $defs."""
    schema = {
        "type": "object",
        "$defs": {
            "MyType": {"type": "object", "properties": {"x": {"type": "integer"}}}
        },
    }
    result = add_additional_properties_false(schema)
    assert result["additionalProperties"] is False
    assert result["$defs"]["MyType"]["additionalProperties"] is False


def test_recurses_into_definitions():
    """Should recurse into definitions (older JSON Schema)."""
    schema = {
        "type": "object",
        "definitions": {
            "MyType": {"type": "object", "properties": {"x": {"type": "integer"}}}
        },
    }
    result = add_additional_properties_false(schema)
    assert result["additionalProperties"] is False
    assert result["definitions"]["MyType"]["additionalProperties"] is False


def test_recurses_into_items_dict():
    """Should recurse into items when it's a dict (array of objects)."""
    schema = {
        "type": "array",
        "items": {"type": "object", "properties": {"x": {"type": "integer"}}},
    }
    result = add_additional_properties_false(schema)
    assert result["items"]["additionalProperties"] is False


def test_recurses_into_items_list():
    """Should recurse into items when it's a list (tuple-style)."""
    schema = {
        "type": "array",
        "items": [
            {"type": "object", "properties": {"x": {"type": "integer"}}},
            {"type": "object", "properties": {"y": {"type": "string"}}},
        ],
    }
    result = add_additional_properties_false(schema)
    assert result["items"][0]["additionalProperties"] is False
    assert result["items"][1]["additionalProperties"] is False


def test_recurses_into_prefix_items():
    """Should recurse into prefixItems (JSON Schema draft 2020-12 tuples)."""
    schema = {
        "type": "array",
        "prefixItems": [
            {"type": "object", "properties": {"x": {"type": "integer"}}},
            {"type": "object", "properties": {"y": {"type": "string"}}},
        ],
    }
    result = add_additional_properties_false(schema)
    assert result["prefixItems"][0]["additionalProperties"] is False
    assert result["prefixItems"][1]["additionalProperties"] is False


def test_recurses_into_allof():
    """Should recurse into allOf."""
    schema = {
        "allOf": [
            {"type": "object", "properties": {"x": {"type": "integer"}}},
            {"type": "object", "properties": {"y": {"type": "string"}}},
        ]
    }
    result = add_additional_properties_false(schema)
    assert result["allOf"][0]["additionalProperties"] is False
    assert result["allOf"][1]["additionalProperties"] is False


def test_recurses_into_anyof():
    """Should recurse into anyOf."""
    schema = {
        "anyOf": [
            {"type": "object", "properties": {"x": {"type": "integer"}}},
            {"type": "string"},
        ]
    }
    result = add_additional_properties_false(schema)
    assert result["anyOf"][0]["additionalProperties"] is False
    # String type should not have additionalProperties
    assert "additionalProperties" not in result["anyOf"][1]


def test_recurses_into_oneof():
    """Should recurse into oneOf."""
    schema = {
        "oneOf": [
            {"type": "object", "properties": {"x": {"type": "integer"}}},
            {"type": "object", "properties": {"y": {"type": "string"}}},
        ]
    }
    result = add_additional_properties_false(schema)
    assert result["oneOf"][0]["additionalProperties"] is False
    assert result["oneOf"][1]["additionalProperties"] is False


def test_recurses_into_additional_properties_schema():
    """Should recurse into additionalProperties when it's a schema."""
    schema = {
        "type": "object",
        "additionalProperties": {
            "type": "object",
            "properties": {"nested": {"type": "string"}},
        },
    }
    result = add_additional_properties_false(schema)
    # The additionalProperties schema itself should get additionalProperties: false
    assert result["additionalProperties"]["additionalProperties"] is False


def test_recurses_into_if_then_else():
    """Should recurse into if/then/else conditionals."""
    schema = {
        "if": {"type": "object", "properties": {"kind": {"const": "user"}}},
        "then": {"type": "object", "properties": {"name": {"type": "string"}}},
        "else": {"type": "object", "properties": {"id": {"type": "integer"}}},
    }
    result = add_additional_properties_false(schema)
    assert result["if"]["additionalProperties"] is False
    assert result["then"]["additionalProperties"] is False
    assert result["else"]["additionalProperties"] is False


def test_recurses_into_not():
    """Should recurse into not schemas."""
    schema = {
        "not": {"type": "object", "properties": {"forbidden": {"type": "string"}}}
    }
    result = add_additional_properties_false(schema)
    assert result["not"]["additionalProperties"] is False


def test_complex_nested_schema():
    """Should handle complex nested schemas correctly."""
    schema = {
        "type": "object",
        "properties": {
            "users": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "address": {
                            "type": "object",
                            "properties": {
                                "city": {"type": "string"},
                                "zip": {"type": "string"},
                            },
                        },
                    },
                },
            }
        },
        "$defs": {
            "Contact": {
                "type": "object",
                "properties": {"email": {"type": "string"}},
            }
        },
    }
    result = add_additional_properties_false(schema)

    # Root object
    assert result["additionalProperties"] is False
    # Nested user object in array items
    assert result["properties"]["users"]["items"]["additionalProperties"] is False
    # Deeply nested address object
    assert (
        result["properties"]["users"]["items"]["properties"]["address"][
            "additionalProperties"
        ]
        is False
    )
    # Definition
    assert result["$defs"]["Contact"]["additionalProperties"] is False
