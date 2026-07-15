"""Update checking.

There's no package registry involved — this runs straight from a git
checkout via `uvx --from git+...`, which re-resolves and rebuilds against
the latest commit on `main` on every fresh process launch (verified: uv
queries GitHub's API for the current HEAD commit each time). So a client
that restarts/reconnects the server already gets the latest code for free.
The gap this fills is *telling* the user that's worth doing: a
long-running session keeps its already-started process alive even after
`main` moves on, with no way to notice on its own.

We compare the running process's own version (from its installed package
metadata, i.e. whatever pyproject.toml said at the commit it was built
from) against the version currently on `main`'s pyproject.toml.
"""

import tomllib
from importlib import metadata

import httpx
from packaging.version import Version

PYPROJECT_URL = "https://raw.githubusercontent.com/tinhvqbk/memobot-mcp/main/pyproject.toml"


def get_running_version():
    return metadata.version("memobot-mcp")


def get_latest_version(timeout_seconds=5):
    response = httpx.get(PYPROJECT_URL, timeout=timeout_seconds)
    response.raise_for_status()
    return tomllib.loads(response.text)["project"]["version"]


def check_for_updates():
    """Returns a dict describing whether a newer version is available.
    Never raises — a failed check (offline, GitHub down, etc.) is reported
    in the result rather than blowing up whatever called this."""
    running_version = get_running_version()
    try:
        latest_version = get_latest_version()
    except Exception as e:
        return {
            "running_version": running_version,
            "latest_version": None,
            "update_available": False,
            "error": str(e),
        }
    return {
        "running_version": running_version,
        "latest_version": latest_version,
        "update_available": Version(latest_version) > Version(running_version),
    }
