# Run all CI checks locally
ci: lint typecheck test

# Run tests
test:
    pytest

# Run linting (ruff + shed formatting check)
lint:
    ruff check .
    shed
    git diff --exit-code

# Run type checking
typecheck:
    mypy src/

# Format code
format:
    shed
