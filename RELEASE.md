RELEASE_TYPE: patch

This release adds support for alphabet parameters in `{"type": "string"}` and `{"type": "regex"}` schemas, allowing control over generated characters. Supported parameters are `codec`, `min_codepoint`, `max_codepoint`, `categories`, `exclude_categories`, `exclude_characters`, and `include_characters`.
