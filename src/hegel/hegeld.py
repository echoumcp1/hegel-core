"""
Hegel server - drives test execution via Hypothesis ConjectureRunner.

The server accepts a single client connection and handles test execution
requests. Each test runs through ConjectureRunner which generates test
cases and manages shrinking.
"""

import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import cbor2
from hypothesis import Verbosity, settings
from hypothesis.control import BuildContext
from hypothesis.database import DirectoryBasedExampleDatabase
from hypothesis.errors import StopTest
from hypothesis.internal.conjecture.data import ConjectureData, Status
from hypothesis.internal.conjecture.engine import ConjectureRunner
from hypothesis.internal.conjecture.shrinker import sort_key

from hegel.parser import from_schema
from hegel.protocol import VERSION_NEGOTIATION_MESSAGE, Channel, Connection

DATABASE = DirectoryBasedExampleDatabase(".hegel")

# Schema cache for performance
FROM_SCHEMA_CACHE: OrderedDict[bytes, Any] = OrderedDict()
CACHE_SIZE = 1024


def cached_from_schema(schema: dict) -> Any:
    key = hashlib.sha1(json.dumps(schema, sort_keys=True).encode("utf-8")).digest()[:32]
    try:
        result = FROM_SCHEMA_CACHE[key]
        FROM_SCHEMA_CACHE.move_to_end(key)
        return result
    except KeyError:
        result = from_schema(schema)
        FROM_SCHEMA_CACHE[key] = result
        if len(FROM_SCHEMA_CACHE) > CACHE_SIZE:
            FROM_SCHEMA_CACHE.popitem(last=False)
        return result


def make_settings(test_cases: int, verbosity: Verbosity) -> settings:
    return settings(
        deadline=None,
        database=DATABASE,
        max_examples=test_cases,
        verbosity=verbosity,
    )


def make_test_function(
    connection: Connection,
    control_channel: Channel,
    *,
    is_final: bool = False,
) -> Callable[[ConjectureData], None]:
    """Create a test function that communicates with the SDK.

    The returned function handles a single test case by:
    1. Creating a channel for communication
    2. Sending a test_case event to the SDK
    3. Handling generate/span/target requests until mark_complete
    4. Applying the final status to the ConjectureData
    """

    def test_function(data: ConjectureData) -> None:
        with BuildContext(data, is_final=is_final, wrapped_test=None):  # type: ignore
            # Create a channel for this test case
            test_channel = connection.new_channel()

            # Send test_case message to SDK on control channel
            request_id = control_channel.send_request(
                cbor2.dumps(
                    {
                        "event": "test_case",
                        "channel": test_channel.channel_id,
                        "is_final": is_final,
                    }
                )
            )

            # Now handle requests from SDK on the test channel
            complete = [False]
            result_status = [Status.VALID]
            interesting_origin = [None]

            def handle_sdk_request(message: dict) -> Any:
                command = message.get("command")

                if command == "generate":
                    schema = message.get("schema", {})
                    try:
                        strategy = cached_from_schema(schema)
                        return data.draw(strategy)
                    except StopTest:
                        raise

                elif command == "start_span":
                    label = message.get("label", 0)
                    data.start_span(label)
                    return None

                elif command == "stop_span":
                    discard = message.get("discard", False)
                    data.stop_span(discard=discard)
                    return None

                elif command == "target":
                    value = message.get("value", 0.0)
                    label = message.get("label", "")
                    data.target_observations[label] = value
                    return None

                elif command == "mark_complete":
                    status = message.get("status", "VALID")
                    origin = message.get("origin")

                    complete[0] = True

                    if status == "VALID":
                        result_status[0] = Status.VALID
                    elif status == "INVALID":
                        result_status[0] = Status.INVALID
                    elif status == "INTERESTING":
                        result_status[0] = Status.INTERESTING
                        interesting_origin[0] = origin

                    return None

                else:
                    raise ValueError(f"Unknown command: {command}")

            try:
                # Handle requests until mark_complete
                while not complete[0]:
                    req_id, req_payload = test_channel.receive_request()
                    try:
                        msg = cbor2.loads(req_payload)
                        result = handle_sdk_request(msg)
                        test_channel.send_response(
                            req_id, cbor2.dumps({"result": result})
                        )
                    except StopTest:
                        # Hypothesis wants to stop - send overflow response
                        test_channel.send_response(
                            req_id,
                            cbor2.dumps({"error": "overflow", "type": "StopTest"}),
                        )
                        raise
                    except Exception as e:
                        test_channel.send_response(
                            req_id,
                            cbor2.dumps({"error": str(e), "type": type(e).__name__}),
                        )

                # Apply the result status
                if result_status[0] == Status.INVALID:
                    data.mark_invalid()
                elif result_status[0] == Status.INTERESTING:
                    # Convert origin dict to a hashable tuple for Hypothesis
                    origin_dict = interesting_origin[0]
                    if origin_dict is not None:
                        origin = (
                            origin_dict.get("exc_type", "Unknown"),
                            origin_dict.get("filename", ""),
                            origin_dict.get("lineno", 0),
                        )
                    else:
                        origin = ("Unknown", "", 0)
                    data.mark_interesting(origin)  # type: ignore[arg-type]

            finally:
                # Clean up test channel
                test_channel.close()
                # Always wait for control response to maintain synchronization.
                # The SDK will always send a response, even after StopTest.
                control_channel.receive_response(request_id)

    return test_function


def run_server_on_connection(connection: Connection) -> None:
    """Handle a single client connection."""
    try:
        control = connection.control_channel

        # Version negotiation
        id, payload = control.receive_request()
        if payload == VERSION_NEGOTIATION_MESSAGE:
            control.send_response(id, b"Ok")
        else:
            control.send_response(
                id, f"Error: Unrecognised negotiation string {payload!r}".encode()
            )
            return

        # Main request loop - handle run_test requests
        while True:
            id, payload = control.receive_request()
            message = cbor2.loads(payload)

            command = message.get("command")
            if command == "run_test":
                result = handle_run_test(
                    connection,
                    control,
                    test_name=message.get("name", "test"),
                    test_cases=message.get("test_cases", 1000),
                    verbosity=Verbosity(message.get("verbosity", "normal")),
                )
                control.send_response(id, cbor2.dumps({"result": result}))
            else:
                control.send_response(
                    id, cbor2.dumps({"error": f"Unknown command: {command}"})
                )
    except ConnectionError:
        pass
    finally:
        connection.close()


def handle_run_test(
    connection: Connection,
    control_channel: Channel,
    test_name: str,
    test_cases: int = 1000,
    verbosity: Verbosity = Verbosity.normal,
) -> dict[str, Any]:
    """Run a single test using ConjectureRunner.

    Returns a dict with test results including:
    - passed: bool
    - examples_run: int
    - valid_examples: int
    - invalid_examples: int
    - failure: optional dict with failure details
    """
    db_key = test_name.encode("utf-8")

    # Create and run the ConjectureRunner
    test_function = make_test_function(connection, control_channel, is_final=False)

    runner = ConjectureRunner(
        test_function,
        settings=make_settings(test_cases, verbosity),
        database_key=db_key,
    )
    runner.run()

    result: dict[str, Any] = {
        "passed": len(runner.interesting_examples) == 0,
        "examples_run": runner.call_count,
        "valid_examples": runner.valid_examples,
        "invalid_examples": runner.invalid_examples,
    }

    # If there were failures, replay the minimal one
    if runner.interesting_examples:
        minimal = min(
            runner.interesting_examples.values(),
            key=lambda d: sort_key(d.nodes),
        )

        # Replay with is_final=True
        final_test = make_test_function(connection, control_channel, is_final=True)
        final_data = runner.new_conjecture_data(minimal.choices)
        try:
            final_test(final_data)
        except StopTest:
            pass

        origin = minimal.interesting_origin
        if isinstance(origin, tuple):
            result["failure"] = {
                "exc_type": origin[0],
                "filename": origin[1],
                "lineno": origin[2],
            }
        else:
            result["failure"] = {"origin": origin}

    # Send test_done event
    control_channel.send_request(
        cbor2.dumps(
            {
                "event": "test_done",
                "results": result,
            }
        )
    )

    return result
