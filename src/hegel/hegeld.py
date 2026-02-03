"""
Hegel server - drives test execution via Hypothesis ConjectureRunner.

The server accepts a single client connection and handles test execution
requests. Each test runs through ConjectureRunner which generates test
cases and manages shrinking.
"""

import contextlib
import hashlib
import json
import os
import traceback
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from hypothesis import settings
from hypothesis.control import BuildContext
from hypothesis.database import DirectoryBasedExampleDatabase
from hypothesis.errors import StopTest
from hypothesis.internal.conjecture.data import ConjectureData, Status
from hypothesis.internal.conjecture.engine import ConjectureRunner
from hypothesis.internal.conjecture.shrinker import sort_key

from hegel.parser import from_schema
from hegel.protocol import Channel, Connection

DATABASE = DirectoryBasedExampleDatabase(".hegel")

# Schema cache for performance
FROM_SCHEMA_CACHE: OrderedDict[bytes, Any] = OrderedDict()
CACHE_SIZE = 1024


def cached_from_schema(schema: dict) -> Any:
    """Convert schema to strategy with LRU caching by SHA1 hash."""
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


def make_settings(test_cases: int) -> settings:
    """Create Hypothesis settings for a test run."""
    return settings(
        deadline=None,
        database=DATABASE,
        max_examples=test_cases,
    )


def make_test_function(
    connection: Connection,
    channel: Channel,
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
            test_case_channel = connection.new_channel(role="Test Case")

            # Send test_case message to SDK on test case channel
            channel.request(
                {
                    "event": "test_case",
                    "channel": test_case_channel.channel_id,
                    "is_final": is_final,
                },
            ).get()

            done = False

            # Now handle requests from SDK on the test channel
            def handle_sdk_request(message: dict) -> Any:
                nonlocal done
                try:
                    command = message.get("command")

                    if command == "generate":
                        schema = message.get("schema", {})
                        strategy = cached_from_schema(schema)
                        return data.draw(strategy)

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
                        if status == "VALID":
                            data.conclude_test(Status.VALID)
                        elif status == "INVALID":
                            data.mark_invalid()
                        elif status == "INTERESTING":
                            data.mark_interesting(
                                origin,  # type: ignore[arg-type]
                            )
                    else:
                        raise ValueError(f"Unknown command: {command}")
                except BaseException:
                    done = True
                    raise

            test_case_channel.handle_requests(handle_sdk_request, until=lambda: done)

    return test_function


def run_server_on_connection(connection: Connection) -> None:
    """Handle a single client connection."""
    connection.receive_handshake()

    pending_futures = []
    try:
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as thread_pool:
            # Main request loop - handle run_test requests
            test_count = 0
            while True:
                test_count += 1
                id, message = connection.control_channel.receive_request(timeout=None)

                command = message.get("command")
                if command == "run_test":
                    test_name = message.get("name", f"test {test_count}")
                    channel = connection.connect_channel(
                        message["channel"],
                        role=f"Test channel for {test_name}",
                    )

                    pending_futures.append(
                        thread_pool.submit(
                            handle_run_test,
                            connection,
                            channel,
                            test_name=test_name,
                            test_cases=message.get("test_cases", 1000),
                        ),
                    )
                    connection.control_channel.send_response_value(
                        id,
                        message=True,
                    )
                else:
                    connection.control_channel.send_response_error(
                        id,
                        error=f"Unknown command: {command}",
                        error_type="UnknownCommand",
                    )
    except ConnectionError:
        pass
    except BaseException:
        traceback.print_exc()
    finally:
        connection.close()
    for f in pending_futures:
        try:
            f.result(timeout=0.5)
        except (ConnectionError, TimeoutError):
            f.cancel()


def handle_run_test(
    connection: Connection,
    channel: Channel,
    test_name: str,
    test_cases: int = 100,
) -> dict[str, Any]:
    """Run a single test using ConjectureRunner.

    Returns a dict with test results including:
    - passed: bool
    - examples_run: int
    - valid_examples: int
    - invalid_examples: int
    - failure: optional dict with failure details
    """
    try:
        db_key = test_name.encode("utf-8")

        # Create and run the ConjectureRunner
        test_function = make_test_function(connection, channel, is_final=False)

        runner = ConjectureRunner(
            test_function,
            settings=make_settings(test_cases),
            database_key=db_key,
        )
        runner.run()

        result: dict[str, Any] = {
            "passed": len(runner.interesting_examples) == 0,
            "examples_run": runner.call_count,
            "valid_test_cases": runner.valid_examples,
            "invalid_test_cases": runner.invalid_examples,
            "interesting_test_cases": len(runner.interesting_examples),
        }

        channel.request(
            {
                "event": "test_done",
                "results": result,
            },
        ).get()

        final_test_function = make_test_function(connection, channel, is_final=True)

        for v in sorted(
            runner.interesting_examples.values(),
            key=lambda d: sort_key(d.nodes),
        ):
            with contextlib.suppress(StopTest):
                final_test_function(
                    ConjectureData(
                        prefix=v.choices,
                        max_choices=len(v.choices),
                        random=None,
                    ),
                )

        return result
    except Exception:
        # We don't actually await the futures and just sortof run them fire and
        # forget in the background, so we won't see any exceptions that are
        # thrown unless we print them here.
        traceback.print_exc()
        raise
