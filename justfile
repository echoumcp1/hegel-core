ci: lint typecheck test

test:
    uv run pytest tests

lint:
    uv run ruff check .
    uv run shed
    git diff --exit-code

typecheck:
    uv run mypy src/

format:
    uv run ruff check src tests --fix
    uv run shed
    uv run ruff format src tests

check: typecheck format test
    echo "Checks passed successfully"
