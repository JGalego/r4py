"""R, bundled in a Python wheel."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

__version__ = "4.6.2.2"

R_HOME: Final = Path(__file__).resolve().parent / "r-runtime"
R: Final = R_HOME / ("bin/x64/R.exe" if os.name == "nt" else "bin/R")
RSCRIPT: Final = R_HOME / ("bin/x64/Rscript.exe" if os.name == "nt" else "bin/Rscript")
R_VERSION: Final = tuple(int(part) for part in __version__.split(".")[:3])

__all__ = ["R", "R_HOME", "R_VERSION", "RSCRIPT", "get_env"]


def get_env(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy the current environment, apply overrides, and select the bundled R."""
    env = dict(os.environ)
    if overrides is not None:
        env.update(overrides)
    env.update(
        R_HOME=str(R_HOME),
        RHOME=str(R_HOME),
        R_SHARE_DIR=str(R_HOME / "share"),
        R_INCLUDE_DIR=str(R_HOME / "include"),
        R_DOC_DIR=str(R_HOME / "doc"),
    )
    # A system R's sub-architecture and loader settings do not describe this R.
    env.pop("R_ARCH", None)
    env.pop("R_LD_LIBRARY_PATH", None)
    return env
