"""Test the installed wheel, never the source checkout or its original R."""

import argparse
import os
import shutil
import subprocess
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    if wheel.stat().st_size > 100 * 1024 * 1024:
        raise RuntimeError(f"{wheel.name} exceeds PyPI's default 100 MiB file limit.")
    runtime = ROOT / "src/r4py/r-runtime"
    hidden = runtime.with_name("r-runtime.build-backup")
    if hidden.exists():
        raise RuntimeError(f"Backup path already exists: {hidden}")
    moved = runtime.exists()
    if moved:
        runtime.rename(hidden)
    try:
        with tempfile.TemporaryDirectory(prefix="r4py wheel test ") as directory:
            work = Path(directory)
            environment = work / "venv with spaces"
            venv.EnvBuilder(with_pip=True).create(environment)
            binary_dir = environment / ("Scripts" if os.name == "nt" else "bin")
            python = binary_dir / ("python.exe" if os.name == "nt" else "python")
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            for key in (
                "R_HOME",
                "RHOME",
                "R_ARCH",
                "R_SHARE_DIR",
                "R_INCLUDE_DIR",
                "R_DOC_DIR",
                "R_LD_LIBRARY_PATH",
                "CURL_CA_BUNDLE",
            ):
                env.pop(key, None)
            env["R4PY_REQUIRE_RUNTIME"] = "1"
            subprocess.run(
                [python, "-m", "pip", "install", str(wheel), "pytest>=8"],
                cwd=work,
                env=env,
                check=True,
            )
            tests = [work / "test_runtime.py", work / "test_session.py"]
            for test in tests:
                shutil.copy2(ROOT / "tests" / test.name, test)
            subprocess.run(
                [
                    python,
                    "-m",
                    "pytest",
                    "-v",
                    "--tb=short",
                    "-o",
                    "markers=runtime: requires the bundled R",
                    *map(str, tests),
                ],
                cwd=work,
                env=env,
                check=True,
            )
            for command, arguments in (
                ("r4py", ["--vanilla", "-e", "cat(6 * 7)"]),
                ("r4py-r", ["--vanilla", "--slave", "-e", "cat(6 * 7)"]),
            ):
                executable = binary_dir / (
                    f"{command}.exe" if os.name == "nt" else command
                )
                result = subprocess.run(
                    [executable, *arguments],
                    cwd=work,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.stdout != "42":
                    raise RuntimeError(f"{command} returned {result.stdout!r}")
    finally:
        if moved:
            hidden.rename(runtime)


if __name__ == "__main__":
    main()
