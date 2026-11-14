"""Build R on Unix, or copy an existing R installation (including Windows)."""

import argparse
import hashlib
import os
import re
import runpy
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
R_VERSION = ".".join(
    str(runpy.run_path(str(ROOT / "src/r4py/__init__.py"))["__version__"]).split(".")[
        :3
    ]
)
SOURCE_URL = f"https://cran.r-project.org/src/base/R-4/R-{R_VERSION}.tar.gz"
SOURCE_SHA256 = "c297040f6392b1dac6e5c0eb398422cfdaedf540ae2205edb9ad0d10cb10ee00"
RUNTIME = ROOT / "src/r4py/r-runtime"


def _run(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def _verify_archive(archive: Path) -> None:
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != SOURCE_SHA256:
        raise ValueError(f"SHA-256 mismatch for {archive}; refusing to build it.")


def _bundle_linux_libraries(runtime: Path) -> None:
    # manylinux permits some desktop libraries that a headless machine may lack.
    baseline_libraries = {
        "libX11.so.6",
        "libXext.so.6",
        "libXrender.so.1",
        "libICE.so.6",
        "libSM.so.6",
        "libGL.so.1",
        "libgobject-2.0.so.0",
        "libgthread-2.0.so.0",
        "libglib-2.0.so.0",
        "libexpat.so.1",
        "libz.so.1",
        "libnsl.so.1",
        "libatomic.so.1",
    }
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = str(runtime / "lib")
    result = subprocess.run(
        ["ldd", *map(str, runtime.rglob("*.so"))],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    if "not found" in result.stdout:
        raise RuntimeError(f"Unresolved R libraries:\n{result.stdout}")
    for match in re.finditer(
        r"^\s*(\S+)\s+=>\s+(.+?)\s+\(0x[0-9a-f]+\)\s*$",
        result.stdout,
        re.MULTILINE,
    ):
        name, filename = match.groups()
        source = Path(filename).resolve()
        if name in baseline_libraries and not source.is_relative_to(runtime.resolve()):
            destination = runtime / "lib" / name
            if not destination.exists():
                shutil.copy2(source, destination)


def _relocate(runtime: Path) -> None:
    wrapper = runtime / "bin/R"
    text = wrapper.read_text()
    start = text.index("R_HOME_DIR=")
    end = text.index('R_HOME="${R_HOME_DIR}"', start)
    text = (
        text[:start]
        + 'R_HOME_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P) || exit 1\n'
        + 'CURL_CA_BUNDLE=${CURL_CA_BUNDLE:-"${R_HOME_DIR}/etc/curl-ca-bundle.crt"}\n'
        + "export CURL_CA_BUNDLE\n"
        + text[end:]
    )
    for variable, directory in (
        ("R_SHARE_DIR", "share"),
        ("R_INCLUDE_DIR", "include"),
        ("R_DOC_DIR", "doc"),
    ):
        text, count = re.subn(
            rf"(?m)^{variable}=.*$",
            f'{variable}="${{R_HOME_DIR}}/{directory}"',
            text,
        )
        if count != 1:
            raise RuntimeError(f"Expected one {variable} assignment in {wrapper}.")
    wrapper.write_text(text)

    config = runtime / "bin/config"
    text = config.read_text()
    text = text.replace(
        'makefiles="-f ${R_HOME}/etc${R_ARCH}/Makeconf '
        '-f ${R_SHARE_DIR}/make/config.mk"',
        'makefiles="-f \\"${R_HOME}/etc${R_ARCH}/Makeconf\\" '
        '-f \\"${R_SHARE_DIR}/make/config.mk\\""',
    )
    text = text.replace('R_HOME=${R_HOME}"', 'R_HOME=\\"${R_HOME}\\""')
    text = text.replace(
        ". ${R_HOME}/etc${R_ARCH}/Renviron",
        '. "${R_HOME}/etc${R_ARCH}/Renviron"',
    )
    config.write_text(text)
    makeconf = runtime / "etc/Makeconf"
    text = makeconf.read_text().replace(
        "include $(R_SHARE_DIR)/make/vars.mk",
        "r4py_space := $(subst ,, )\n"
        r"include $(subst $(r4py_space),\$(r4py_space),$(R_SHARE_DIR))/make/vars.mk",
    )
    makeconf.write_text(text)

    native = runtime / "bin/Rscript"
    binary = runtime / "bin/Rscript.bin"
    if not binary.exists():
        native.rename(binary)
    native.write_text(
        "#!/bin/sh\n"
        'RHOME=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P) || exit 1\n'
        'CURL_CA_BUNDLE=${CURL_CA_BUNDLE:-"${RHOME}/etc/curl-ca-bundle.crt"}\n'
        "export RHOME CURL_CA_BUNDLE\n"
        'exec "${RHOME}/bin/Rscript.bin" "$@"\n'
    )
    native.chmod(0o755)


def _bundle_certificates(runtime: Path) -> None:
    bundle = runtime / "etc/curl-ca-bundle.crt"
    if not bundle.is_file():
        candidates = (
            ssl.get_default_verify_paths().cafile,
            "/opt/homebrew/etc/ca-certificates/cert.pem",
            "/usr/local/etc/ca-certificates/cert.pem",
            "/etc/ssl/certs/ca-certificates.crt",
            "/etc/pki/tls/certs/ca-bundle.crt",
        )
        source = next(
            (Path(path) for path in candidates if path and Path(path).is_file()),
            None,
        )
        if source is None:
            raise RuntimeError(
                "No CA certificate bundle found; install ca-certificates."
            )
        shutil.copy2(source, bundle)


def _build(work: Path, archive: Path | None, jobs: int) -> Path:
    if archive is None:
        archive = work / f"R-{R_VERSION}.tar.gz"
        print(f"Downloading {SOURCE_URL}", flush=True)
        with urllib.request.urlopen(SOURCE_URL, timeout=120) as response:
            with archive.open("wb") as stream:
                shutil.copyfileobj(response, stream)
    _verify_archive(archive)
    _run("tar", "-xzf", str(archive), "-C", str(work))
    source = work / f"R-{R_VERSION}"
    build = work / "build"
    build.mkdir()
    prefix = work / "install"
    _run(
        str(source / "configure"),
        f"--prefix={prefix}",
        "--enable-R-shlib",
        "--disable-java",
        "--without-x",
        "--with-tcltk=no",
        "--with-blas=no",
        "--with-lapack=no",
        "--with-cairo",
        "--enable-R-framework=no",
        cwd=build,
    )
    _run("make", f"-j{jobs}", cwd=build)
    _run("make", "install", cwd=build)
    candidates = [prefix / "lib/R", prefix / "lib64/R"]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise RuntimeError(f"R installation not found under {prefix}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime", type=Path, help="Copy this R home instead of compiling R."
    )
    parser.add_argument(
        "--source-archive", type=Path, help="Use a local, checksum-verified R tarball."
    )
    parser.add_argument("--jobs", type=int, default=2, help="Parallel make jobs.")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if args.runtime is not None and args.source_archive is not None:
        parser.error("--runtime and --source-archive are mutually exclusive")
    if RUNTIME.exists():
        parser.error(f"{RUNTIME} already exists; move it aside before rebuilding")
    if os.name == "nt" and args.runtime is None:
        parser.error("On Windows, pass --runtime pointing to an installed CRAN R")

    with tempfile.TemporaryDirectory(prefix="r4py-") as directory:
        work = Path(directory)
        source = (
            args.runtime.resolve()
            if args.runtime is not None
            else _build(work, args.source_archive, args.jobs)
        )
        staged = work / "r-runtime"
        shutil.copytree(source, staged)
        if os.name == "nt":
            for filename in (
                "unins000.exe",
                "unins000.dat",
                "doc/manual/fullrefman.pdf",
            ):
                path = staged / filename
                if path.exists():
                    path.unlink()
        if os.name != "nt":
            _relocate(staged)
            _bundle_certificates(staged)
        if sys.platform == "linux":
            _bundle_linux_libraries(staged)
        binary = staged / ("bin/x64/Rscript.exe" if os.name == "nt" else "bin/Rscript")
        version = subprocess.check_output(
            [str(binary), "--vanilla", "-e", "cat(as.character(getRversion()))"],
            text=True,
        )
        if version != R_VERSION:
            raise RuntimeError(f"Expected R {R_VERSION}, found {version!r}.")
        shutil.copy2(ROOT / "build_r_runtime.py", staged / "r4py-build.py")
        shutil.move(str(staged), RUNTIME)
    print(f"R {R_VERSION} ready at {RUNTIME}", flush=True)


if __name__ == "__main__":
    main()
