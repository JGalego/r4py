"""Command-line entry points; all arguments are passed to R unchanged."""

import os
import subprocess
import sys
from pathlib import Path

from . import RSCRIPT, R, get_env


def _launch(executable: Path) -> int:
    if not executable.is_file():
        print(
            "r4py: bundled R is missing. Install a platform wheel, or run "
            "build_r_runtime.py when working from source.",
            file=sys.stderr,
        )
        return 1
    args = [str(executable), *sys.argv[1:]]
    if os.name != "nt":
        os.execve(executable, args, get_env())
    try:
        return subprocess.call(args, env=get_env())
    except KeyboardInterrupt:
        return 130


def main() -> int:
    """Run Rscript."""
    return _launch(RSCRIPT)


def r_main() -> int:
    """Run the R console or an R CMD tool."""
    return _launch(R)


if __name__ == "__main__":
    sys.exit(main())
