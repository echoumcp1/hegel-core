RELEASE_TYPE: patch

This patch changes how `const`, `sampled_from`, and `one_of` are defined in the protocol, to harmonize with the other generator definitions:

- `{"const": value}` is now `{"type": "constant", "value": value}`
- `{"sampled_from": [...]}` is now `{"type": "sampled_from", "values": [...]}`
- `{"one_of": [...]}` is now `{"type": "one_of", "generators": [...]}`

As a result, this patch bumps our protocol version to `0.8`.
