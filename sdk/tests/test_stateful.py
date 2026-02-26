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


def test_machine_with_no_rules():
    machine = s.Machine()
    with pytest.raises(ValueError, match="Cannot run a machine with no rules."):
        machine.run()


def test_cannot_add_rule_while_running():
    @hegel
    def test_fn():
        machine = s.Machine()

        @machine.rule
        def my_rule():
            machine.rule(lambda: None, name="sneaky")

        machine.run()

    with pytest.raises(ValueError, match="Cannot change machine shape while running."):
        test_fn()


def test_cannot_add_invariant_while_running():
    @hegel
    def test_fn():
        machine = s.Machine()

        @machine.rule
        def my_rule():
            machine.invariant(lambda: None, name="sneaky")

        machine.run()

    with pytest.raises(ValueError, match="Cannot change machine shape while running."):
        test_fn()


def test_consume_variable():
    consumed_any = False

    @hegel
    def consume_machine():
        nonlocal consumed_any
        values = s.variables()
        machine = s.Machine()

        @machine.rule
        def add_value():
            values.add(g.integers().generate())

        @machine.rule
        def consume_value():
            nonlocal consumed_any
            values.generate(consume=True)
            consumed_any = True

        machine.run(max_steps=10)

    consume_machine()
    assert consumed_any


def test_variables_cannot_be_used_across_tests():
    saved_variables = None

    @hegel
    def first_test():
        nonlocal saved_variables
        saved_variables = s.variables()
        saved_variables.add(1)

    first_test()

    @hegel
    def second_test():
        # The pool has values from the first test so empty() is False,
        # but the channel has changed, so generate() should raise.
        saved_variables.generate()

    with pytest.raises(
        ValueError,
        match="Variables should not be used outside the test they are defined in",
    ):
        second_test()


def test_rule_with_explicit_name():
    @hegel
    def test_fn():
        machine = s.Machine()
        machine.rule(lambda: None, name="custom_rule_name")
        machine.run()

    test_fn()


def test_invariant_with_explicit_name():
    @hegel
    def test_fn():
        machine = s.Machine()

        machine.invariant(lambda: None, name="custom_invariant_name")
        machine.rule(lambda: None, name="noop")

        machine.run()

    test_fn()
