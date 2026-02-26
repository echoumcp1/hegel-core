from collections.abc import Callable
from typing import TypeVar

from hegel.protocol.channel import Channel
from hegel_sdk import assume, note
from hegel_sdk.client import AssumeRejected, _get_channel
from hegel_sdk.generators import Generator, integers, sampled_from

T = TypeVar("T")


class Variables(Generator[T]):
    def __init__(self, pool_id, channel):
        self.__pool_id: int = pool_id
        self.__channel: Channel = channel
        self.__values: dict[int, T] = {}

    def empty(self) -> bool:
        return len(self.__values) == 0

    def add(self, value: T) -> None:
        variable_id = self.__channel.send_request(
            {
                "command": "pool_add",
                "pool_id": self.__pool_id,
            }
        ).get()
        self.__values[variable_id] = value

    def generate(self, *, consume: bool = False) -> T:
        assume(not self.empty())
        if _get_channel() is not self.__channel:
            raise ValueError(
                "Variables should not be used outside the test they are defined in"
            )

        variable_id = self.__channel.send_request(
            {
                "command": "pool_generate",
                "pool_id": self.__pool_id,
                "consume": consume,
            }
        ).get()
        if consume:
            return self.__values.pop(variable_id)
        else:
            return self.__values[variable_id]


def variables() -> Variables[T]:
    channel = _get_channel()
    pool_id = channel.send_request(
        {
            "command": "new_pool",
        }
    ).get()
    return Variables(pool_id, channel)


class Machine:
    def __init__(self):
        self.__invariants = []
        self.__rules = []
        self.__running = False

    def invariant(self, check: Callable[[], None], *, name: str | None = None) -> None:
        if self.__running:
            raise ValueError("Cannot change machine shape while running.")
        if name is None:
            name = check.__name__
        self.__invariants.append((name, check))

    def rule(self, fn: Callable[[], None], *, name: str | None = None) -> None:
        if self.__running:
            raise ValueError("Cannot change machine shape while running.")
        if name is None:
            name = fn.__name__
        self.__rules.append((name, fn))

    def __check_invariants(self):
        for name, check in self.__invariants:
            try:
                check()
            except Exception:
                note(f"Invariant {name} failed.")
                raise

    def run(self, max_steps=50):
        if not self.__rules:
            raise ValueError("Cannot run a machine with no rules.")
        self.__running = True
        try:
            note("Initial invariant check")
            self.__check_invariants()
            rules = sampled_from(self.__rules)

            # We generate an unbounded integer as the step cap that
            # hypothesis actually sees. This means we almost always
            # run the maximum amount of steps, but allows us the
            # possibility of shrinking to a smaller number of steps.
            step_cap = min(integers(min_value=1).generate(), max_steps)
            names = []
            steps_run_successfully = 0
            steps_attempted = 0
            while steps_run_successfully < step_cap and (
                steps_attempted < 10 * step_cap
                or (steps_run_successfully == 0 and steps_attempted < 1000)
            ):
                try:
                    steps_attempted += 1
                    name, rule = rules.generate()
                    names.append(name)
                    rule()
                    steps_run_successfully += 1
                except AssumeRejected:
                    pass
                self.__check_invariants()
            if steps_run_successfully == 0:
                raise AssertionError("No valid rules found in 1000 attempts.")
        finally:
            self.__running = False
