ci: lint typecheck test

test:
    pytest

lint:
    ruff check .
    shed
    git diff --exit-code

typecheck:
    mypy src/

format:
    shed
