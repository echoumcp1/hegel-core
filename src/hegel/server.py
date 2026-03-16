import contextlib
import hashlib
import json
import os
import random
import traceback
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from random import Random
from typing import Any

import cbor2
from hypothesis import settings
from hypothesis.control import BuildContext
from hypothesis.core import decode_failure, encode_failure
from hypothesis.errors import StopTest, UnsatisfiedAssumption
from hypothesis.internal.cache import LRUCache
from hypothesis.internal.conjecture.data import ConjectureData, Status
from hypothesis.internal.conjecture.engine import ConjectureRunner
from hypothesis.internal.conjecture.shrinker import sort_key
from hypothesis.internal.conjecture.utils import calc_label_from_name, many

from hegel.protocol import ProtocolError
from hegel.protocol.channel import Channel
from hegel.protocol.connection import Connection
from hegel.schema import from_schema

FROM_SCHEMA_CACHE: LRUCache = LRUCache(1024)


def cached_from_schema(schema: dict) -> Any:
    key = hashlib.sha1(json.dumps(schema, sort_keys=True).encode("utf-8")).digest()[:32]
    try:
        return FROM_SCHEMA_CACHE[key]
    except KeyError:
        result = from_schema(schema)
        FROM_SCHEMA_CACHE[key] = result
        return result


VARIABLES_LABEL = calc_label_from_name("Variables")


class Variables:
    def __init__(self):
        self.last_id = 0
        self.variables = []
        self.removed = set()

    def generate(self, data: ConjectureData) -> int:
        if not self.variables:
            data.mark_invalid()
        else:
            for _ in range(3):
                data.start_span(VARIABLES_LABEL)
                i = data.draw_integer(
                    min_value=0,
                    max_value=len(self.variables) - 1,
                    # Follows convention from hypothesis.stateful.Bundle.
                    # Apparently this shrinks better because it means that
                    # problems found later on are easier to shrink because
                    # there's no padding.
                    shrink_towards=len(self.variables),
                )
                v = self.variables[i]
                if v not in self.removed:
                    data.stop_span()
                    return v
                else:
                    data.stop_span(discard=True)
            i = len(self.variables) - 1
            assert i >= 0
            v = self.variables[i]
            data.draw_integer(
                min_value=0,
                max_value=len(self.variables) - 1,
                forced=i,
            )
            return v

    def consume(self, variable_id: int) -> None:
        self.removed.add(variable_id)
        while self.variables and self.variables[-1] in self.removed:
            self.variables.pop()

    def next(self) -> int:
        self.last_id += 1
        self.variables.append(self.last_id)
        return self.last_id


def make_test_function(
    connection: Connection,
    channel: Channel,
    *,
    is_final: bool = False,
) -> Callable[[ConjectureData], None]:
    """Create a test function that communicates with the client.

    The returned function handles a single test case by:
    1. Creating a channel for communication
    2. Sending a test_case event to the client
    3. Handling generate/span/target requests from the client until mark_complete
    4. Applying the final status to the ConjectureData
    """

    def test_function(data: ConjectureData) -> None:
        collections: dict[str, many] = {}
        variable_pools: list[Variables] = []
        collection_name_counter: Counter[str] = Counter()

        with BuildContext(data, is_final=is_final, wrapped_test=None):  # type: ignore
            test_case_channel = connection.new_channel(role="Test Case")
            channel.send_request(
                {
                    "event": "test_case",
                    "channel_id": test_case_channel.channel_id,
                    "is_final": is_final,
                },
            ).get()

            done = False

            def handle_client_request(message: dict) -> Any:
                nonlocal done
                try:
                    command = message["command"]

                    if command == "generate":
                        schema = message["schema"]
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
                        value = message["value"]
                        label = message["label"]
                        data.target_observations[label] = value
                        return None
                    elif command == "mark_complete":
                        done = True
                        status = Status[message["status"]]
                        origin = message.get("origin")
                        if status is Status.VALID:
                            data.conclude_test(Status.VALID)
                        elif status is Status.INVALID:
                            data.mark_invalid()
                        else:
                            assert status is Status.INTERESTING
                            data.mark_interesting(
                                origin,  # type: ignore[arg-type]
                            )
                    elif command == "new_collection":
                        base_name = message.get("name", "collection")
                        name = f"{base_name}_{collection_name_counter[base_name]}"
                        collection_name_counter[base_name] += 1
                        assert name not in collections
                        min_size = message.get("min_size", 0)
                        max_size = message.get("max_size", float("inf"))
                        if max_size is None:
                            max_size = float("inf")
                        # Standard formula for Hypothesis collections.
                        average_size = min(
                            max(min_size * 2, min_size + 5),
                            0.5 * (min_size + max_size),
                        )
                        collections[name] = many(
                            data,
                            min_size=min_size,
                            max_size=max_size,
                            average_size=average_size,
                        )
                        return name
                    elif command == "collection_more":
                        collection = collections[message["collection"]]
                        return collection.more()
                    elif command == "collection_reject":
                        collection = collections[message["collection"]]
                        return collection.reject(why=message.get("why"))
                    elif command == "new_pool":
                        i = len(variable_pools)
                        v = Variables()
                        variable_pools.append(v)
                        return i
                    elif command == "pool_consume":
                        pool_id = message["pool_id"]
                        variable_id = message["variable_id"]
                        variable_pools[pool_id].consume(variable_id)
                        return None
                    elif command == "pool_add":
                        pool_id = message["pool_id"]
                        return variable_pools[pool_id].next()
                    elif command == "pool_generate":
                        pool_id = message["pool_id"]
                        consume = message.get("consume", False)
                        pool = variable_pools[pool_id]
                        v = pool.generate(data)
                        if consume:
                            pool.consume(v)
                        return v
                    else:
                        raise ValueError(f"Unknown command: {command}")
                except UnsatisfiedAssumption:
                    done = True
                    data.mark_invalid()
                except StopTest:
                    done = True
                    raise

            test_case_channel.handle_requests(handle_client_request, until=lambda: done)

    return test_function


def run_server_on_connection(connection: Connection) -> None:
    """Handle a single client connection."""
    connection.receive_handshake()

    pending_futures = []
    try:
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as thread_pool:
            while True:
                packet = connection.control_channel.read_request(timeout=None)
                message = cbor2.loads(packet.payload)
                command = message["command"]
                if command == "run_test":
                    channel = connection.connect_channel(
                        message["channel_id"], role="Test channel"
                    )

                    pending_futures.append(
                        thread_pool.submit(
                            _run_one,
                            connection,
                            channel,
                            test_cases=message["test_cases"],
                            database_key=message.get("database_key"),
                            seed=message.get("seed"),
                            failure_blob=message.get("failure_blob"),
                        ),
                    )
                    connection.control_channel.write_reply(packet.message_id, True)
                else:
                    raise ValueError(f"Unknown command: {command}")
    except (ConnectionError, ProtocolError):
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


def _run_one(
    connection: Connection,
    channel: Channel,
    *,
    test_cases: int,
    database_key: bytes | None,
    seed: int | None,
    failure_blob: bytes | None = None,
) -> dict[str, Any]:
    """Run a single test using ConjectureRunner.

    Returns a dict with test results including:
    - passed: bool
    - test_cases: int
    - valid_examples: int
    - invalid_examples: int
    - failure: optional dict with failure details
    """
    try:
        test_function = make_test_function(connection, channel, is_final=False)

        if failure_blob is not None:
            choices = decode_failure(failure_blob)
            data = ConjectureData.for_choices(choices)
            with contextlib.suppress(StopTest):
                test_function(data)

            is_interesting = data.status is Status.INTERESTING
            result: dict[str, int | list[bytes] | str] = {
                "passed": not is_interesting,
                "test_cases": 1,
                "valid_test_cases": 0,
                "invalid_test_cases": 0,
                "interesting_test_cases": int(is_interesting),
            }
            if is_interesting:
                result["failure_blobs"] = [failure_blob]
                interesting_choices = [choices]
            else:
                result["failure_blobs"] = []
                interesting_choices = []
        else:
            seed = random.getrandbits(128) if seed is None else seed
            runner = ConjectureRunner(
                test_function,
                settings=settings(
                    deadline=None,
                    max_examples=test_cases,
                    backend=(
                        "hypothesis-urandom"
                        if os.environ.get("ANTITHESIS_OUTPUT_DIR")
                        else "hypothesis"
                    ),
                ),
                random=Random(seed),
                database_key=database_key,
            )
            runner.run()

            interesting_examples = sorted(
                runner.interesting_examples.values(),
                key=lambda d: sort_key(d.nodes),
            )
            result = {
                "passed": len(interesting_examples) == 0,
                "test_cases": runner.call_count,
                "valid_test_cases": runner.valid_examples,
                "invalid_test_cases": runner.invalid_examples,
                "interesting_test_cases": len(interesting_examples),
                "seed": str(seed),
            }

            interesting_choices = [v.choices for v in interesting_examples]

            result["failure_blobs"] = [
                encode_failure(choices) for choices in interesting_choices
            ]

        channel.send_request({"event": "test_done", "results": result}).get()
        final_test_function = make_test_function(connection, channel, is_final=True)
        for choices in interesting_choices:
            with contextlib.suppress(StopTest):
                final_test_function(ConjectureData.for_choices(choices))

        return result
    except Exception:
        # We don't actually await the futures and just sortof run them fire and
        # forget in the background, so we won't see any exceptions that are
        # thrown unless we print them here.
        traceback.print_exc()
        raise
