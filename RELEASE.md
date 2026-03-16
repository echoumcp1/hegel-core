RELEASE_TYPE: minor

This release adds health check support to the protocol (bumps protocol version to 0.5). The `run_test` command now accepts an optional `suppress_health_check` field, and health check failures are reported in the `test_done` results via a `health_check_failure` field. Invalid health check names produce a clear error message.
