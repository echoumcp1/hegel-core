

def add_additional_properties_false(schema: dict) -> dict:
    """Recursively add additionalProperties: false to object schemas.

    This prevents hegel from generating unexpected fields in objects,
    ensuring generated data matches the expected structure exactly.
    """
    if not isinstance(schema, dict):
        return schema

    # Add additionalProperties: false to object types
    if schema.get("type") == "object" and "additionalProperties" not in schema:
        schema["additionalProperties"] = False

    # Recurse into properties
    if "properties" in schema and isinstance(schema["properties"], dict):
        for value in schema["properties"].values():
            add_additional_properties_false(value)

    # Recurse into definitions
    for key in ("$defs", "definitions"):
        if key in schema and isinstance(schema[key], dict):
            for value in schema[key].values():
                add_additional_properties_false(value)

    # Recurse into items (arrays)
    if "items" in schema:
        if isinstance(schema["items"], dict):
            add_additional_properties_false(schema["items"])
        elif isinstance(schema["items"], list):
            for item in schema["items"]:
                add_additional_properties_false(item)

    # Recurse into prefixItems (tuple schemas)
    if "prefixItems" in schema and isinstance(schema["prefixItems"], list):
        for item in schema["prefixItems"]:
            add_additional_properties_false(item)

    # Recurse into allOf, anyOf, oneOf
    for key in ("allOf", "anyOf", "oneOf"):
        if key in schema and isinstance(schema[key], list):
            for subschema in schema[key]:
                add_additional_properties_false(subschema)

    # Recurse into if/then/else/not conditionals
    for key in ("if", "then", "else", "not"):
        if key in schema and isinstance(schema[key], dict):
            add_additional_properties_false(schema[key])

    # Recurse into additionalProperties if it's a schema
    if "additionalProperties" in schema and isinstance(
        schema["additionalProperties"], dict
    ):
        add_additional_properties_false(schema["additionalProperties"])

    return schema
