"""Refuse runtime-less wheels and mark native data as platform-specific."""

import os
import platform
import runpy
import subprocess
import sysconfig
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from hatchling.builders.wheel import WheelBuilderConfig


class CustomBuildHook(BuildHookInterface[WheelBuilderConfig]):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        runtime = Path(self.root) / "src/r4py/r-runtime"
        binaries = (
            ["bin/x64/R.exe", "bin/x64/Rscript.exe"]
            if os.name == "nt"
            else ["bin/R", "bin/Rscript", "lib/libR.so"]
        )
        if sysconfig.get_platform().startswith("macosx"):
            binaries[-1] = "lib/libR.dylib"
        if os.name != "nt":
            binaries.append("etc/curl-ca-bundle.crt")
        for binary in binaries:
            if not (runtime / binary).is_file():
                raise RuntimeError(
                    f"Missing bundled R file: {binary}. "
                    "Run build_r_runtime.py before building a wheel."
                )
        namespace = runpy.run_path(str(Path(self.root) / "src/r4py/__init__.py"))
        expected = ".".join(str(namespace["__version__"]).split(".")[:3])
        result = subprocess.run(
            [
                str(runtime / binaries[1]),
                "--vanilla",
                "-e",
                "cat(as.character(getRversion()))",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if result.stdout != expected:
            raise RuntimeError(
                f"Bundled R version {result.stdout!r} does not match {expected}."
            )
        build_data["pure_python"] = False
        tag = sysconfig.get_platform().replace("-", "_").replace(".", "_")
        if tag.startswith("macosx"):
            target = os.environ.get("MACOSX_DEPLOYMENT_TARGET", platform.mac_ver()[0])
            target = "_".join(target.split(".")[:2])
            tag = f"macosx_{target}_{platform.machine()}"
        build_data["tag"] = f"py3-none-{tag}"
