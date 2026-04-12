"""Simple verbosity-aware logger for relievo."""

import sys

_verbose = False


def setup(verbose: bool) -> None:
    global _verbose
    _verbose = verbose


def info(msg: str) -> None:
    """Always printed - key pipeline steps only."""
    print(msg, file=sys.stderr)


def debug(msg: str) -> None:
    """Only printed when --verbose is active."""
    if _verbose:
        print(msg, file=sys.stderr)
