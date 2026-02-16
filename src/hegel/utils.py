class UniqueIdentifier:
    """A factory for sentinel objects with nice reprs."""

    def __init__(self, identifier: str) -> None:
        self.identifier = identifier

    def __repr__(self) -> str:
        return self.identifier


not_set = UniqueIdentifier("not_set")
