# Run all CI checks locally
ci: lint typecheck coverage

test:
    uv run pytest tests

# Run tests with coverage enforcement
coverage:
    uv run coverage run -m pytest tests
    uv run coverage combine
    uv run coverage report

# Run linting (ruff + shed formatting check)
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

check: typecheck format coverage
    echo "Checks passed successfully"
