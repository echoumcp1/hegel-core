import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def get_version() -> str:
    text = (ROOT / "pyproject.toml").read_text()
    m = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert m is not None
    return m.group(1)


def dispatch(repos: list[str]) -> None:
    version = get_version()

    for repo in repos:
        body = json.dumps(
            {
                "event_type": "hegel-core-release",
                "client_payload": {"version": version},
            }
        )
        subprocess.run(
            ["gh", "api", f"repos/hegeldev/{repo}/dispatches", "--input", "-"],
            input=body,
            text=True,
            check=True,
            cwd=ROOT,
        )


if __name__ == "__main__":
    dispatch(sys.argv[1:])
