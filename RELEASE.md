RELEASE_TYPE: minor

Add absolute barebones requirement for Antithesis support: If the ANTITHESIS_OUTPUT_DIR
environment variable is set (indicating that we are running on the Antithesis system),
use the hypothesis-urandom backend, which will get its entropy from the Antithesis fuzzer.
