RELEASE_TYPE: patch

Fix JSONL parsing in conformance tests to split only on `\n` instead of using `splitlines()`, which incorrectly splits on Unicode line boundary characters (`\x85`, `\u2028`, `\u2029`) that are valid inside JSON string values.
