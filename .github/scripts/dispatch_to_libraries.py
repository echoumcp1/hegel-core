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


def get_changelog(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text()
    # Each entry starts with "## <version> - <date>". Extract the content
    # of the entry for the given version, up to the next entry or EOF.
    pattern = (
        rf"^## {re.escape(version)} - \d{{4}}-\d{{2}}-\d{{2}}\n\n(.*?)(?=\n## |\Z)"
    )
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    assert m is not None
    return m.group(1).strip()


def dispatch(repos: list[str]) -> None:
    version = get_version()
    changelog = get_changelog(version)

    for repo in repos:
        body = json.dumps(
            {
                "event_type": "hegel-core-release",
                "client_payload": {"version": version, "changelog": changelog},
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
