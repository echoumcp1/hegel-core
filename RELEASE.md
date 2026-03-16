RELEASE_TYPE: minor

This release adds health check support to the protocol. The `run_test` command now accepts an optional `suppress_health_check` field, and health check failures are reported in the `test_done` results via a `health_check_failure` field.
