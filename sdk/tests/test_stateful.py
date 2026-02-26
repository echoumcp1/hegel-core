from hegel_sdk import generators as g, stateful as s
from hegel_sdk import hegel
import pytest


def test_can_run_machine():
    @hegel
    def stack_machine():
        stack = []
        machine = s.Machine()

        @machine.invariant
        def stack_is_small():
            assert len(stack) <= 10

        @machine.rule
        def grow_stack():
            stack.extend(g.lists(g.integers()).generate())

        machine.run()

    with pytest.raises(AssertionError):
        stack_machine()


def test_variable_dependencies():
    any_adds = False

    @hegel
    def add_machine():
        numbers = s.variables()
        machine = s.Machine()

        @machine.rule
        def gen():
            i = g.integers().generate()
            numbers.add(i)
            assert not numbers.empty()

        @machine.rule
        def add():
            nonlocal any_adds
            x = numbers.generate()
            y = numbers.generate()
            any_adds = True
            numbers.add(x + y)

        machine.run(max_steps=10)

    add_machine()
    assert any_adds
