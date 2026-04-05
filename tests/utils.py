from hypothesis import HealthCheck, Phase, given, settings as Settings


class Found(Exception):
    pass


def find_any(strategy, condition, *, settings=None):
    @Settings(
        settings,
        max_examples=1000,
        phases=set(Phase) - {Phase.shrink},
        suppress_health_check=list(HealthCheck),
    )
    @given(strategy)
    def test(value):
        if condition(value):
            raise Found

    try:
        test()
    except Found:
        return
    raise AssertionError("No example found satisfying condition")
