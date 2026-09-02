"""Studio Baton: scripted operations for a one-to-one teaching studio.

The public surface is the ``baton`` command-line tool. Everything a skill or an
agent needs to do is one subcommand with a stable exit code; nothing is left to
be assembled by hand from raw API calls.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    # Read from the installed distribution rather than repeating the number
    # here. Two copies drift, and the one that drifts is the one a user reads
    # off `baton --version` when they report a bug, which is how a report
    # ends up describing a build nobody shipped.
    __version__ = version("studio-baton")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
