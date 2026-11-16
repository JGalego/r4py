"""A persistent R worker process with a small Python-to-R bridge.

``RSession`` starts the bundled Rscript once and evaluates code in it, so R
variables and loaded packages survive between calls. Values cross the process
boundary as text: Python values are written as R literals, and R results come
back as JSON. No R packages and no compiler are needed.

Conversion summary (R to Python):

* ``NULL`` is ``None`` and ``NA`` is ``None``; ``NaN`` and ``Inf`` stay floats.
* Length-one vectors become scalars; longer vectors become lists. Check
  ``length()`` in R if you need to tell ``1`` from ``c(1)``.
* Named lists and named vectors become dicts; unnamed lists become lists.
* Matrices become lists of rows. Factors, dates and times become strings.
* Data frames become ``pandas.DataFrame`` if pandas is installed, otherwise a
  dict mapping column names to lists.
* Functions, environments, S4 objects and the like raise ``RError``.
"""

import importlib
import json
import math
import subprocess
import sys
import tempfile
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from . import RSCRIPT, get_env

__all__ = ["RError", "RSession", "RWarning", "to_r"]

_WORKER = Path(__file__).with_name("_worker.R")
_READY = "<<r4py-ready>>"
_DONE = "<<r4py-done>>"
_INT_MAX = 2**31 - 1


class RError(Exception):
    """An error raised by R, or a failure of the R process itself."""


class RWarning(UserWarning):
    """A warning raised by R code run through an ``RSession``."""


def _quote(text: str) -> str:
    if "\x00" in text:
        raise ValueError("R strings cannot contain NUL characters")
    # JSON string escapes are valid R string escapes.
    return json.dumps(text, ensure_ascii=False)


def _scalar_kind(value: object) -> str | None:
    if isinstance(value, bool):
        return "l"
    if isinstance(value, int):
        return "i" if abs(value) <= _INT_MAX else "d"
    if isinstance(value, float):
        return "d"
    if isinstance(value, str):
        return "s"
    return None


def _float(value: float, *, na_for_nan: bool = False) -> str:
    if math.isnan(value):
        return "NA" if na_for_nan else "NaN"
    if math.isinf(value):
        return "Inf" if value > 0 else "-Inf"
    return repr(value)


def _vector(items: Sequence[object], *, na_for_nan: bool = False) -> str | None:
    """Return an R atomic vector literal, or None if items are not one."""
    kinds = {_scalar_kind(item) for item in items if item is not None}
    if not kinds or None in kinds:
        return None
    if kinds == {"i", "d"}:
        kinds = {"d"}
    if len(kinds) != 1:
        return None
    kind = kinds.pop()
    parts: list[str] = []
    for item in items:
        if item is None:
            parts.append("NA")
        elif kind == "d":
            parts.append(_float(float(item), na_for_nan=na_for_nan))  # type: ignore[arg-type]
        else:
            parts.append(to_r(item))
    return f"c({', '.join(parts)})"


def _is_data_frame(value: object) -> bool:
    return hasattr(value, "columns") and hasattr(value, "to_dict")


def to_r(value: object) -> str:
    """Return R source code that builds the Python value.

    Supports None, bool, int, float, str, lists and tuples, mappings, and
    pandas data frames or numpy arrays (via ``tolist``).
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return f"{value}L" if abs(value) <= _INT_MAX else _float(float(value))
    if isinstance(value, float):
        return _float(value)
    if isinstance(value, str):
        return _quote(value)
    if _is_data_frame(value):
        columns: Any = value
        cells: list[str] = []
        for name in columns.columns:
            literal = _vector(columns[name].tolist(), na_for_nan=True)
            if literal is None:
                raise TypeError(f"column {name!r} cannot be converted to an R vector")
            cells.append(f"{_quote(str(name))} = {literal}")
        cells += ["check.names = FALSE", "stringsAsFactors = FALSE"]
        return f"data.frame({', '.join(cells)})"
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        pairs = [f"{_quote(str(key))} = {to_r(item)}" for key, item in mapping.items()]
        return f"list({', '.join(pairs)})"
    if isinstance(value, (list, tuple)):
        items = cast("Sequence[object]", value)
        literal = _vector(items)
        if literal is not None:
            return literal
        return f"list({', '.join(to_r(item) for item in items)})"
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return to_r(tolist())
    raise TypeError(f"cannot convert {type(value).__name__} to R")


def _decode(value: object) -> object:
    if isinstance(value, dict):
        node = cast("dict[str, Any]", value)
        if node.get("__r4py__") == "data.frame":
            return _data_frame(node)
        return {key: _decode(item) for key, item in node.items()}
    if isinstance(value, list):
        return [_decode(item) for item in cast("list[object]", value)]
    return value


def _data_frame(node: dict[str, Any]) -> object:
    names: list[str] = node["columns"]
    data = {name: _decode(col) for name, col in zip(names, node["data"], strict=True)}
    try:
        pandas: Any = importlib.import_module("pandas")
    except ImportError:
        return data
    return pandas.DataFrame(data, columns=names, index=range(node["nrow"]))


class RSession:
    """A persistent R process. Use it as a context manager.

    >>> with RSession() as r:
    ...     r.assign("x", [1, 2, 3, 4])
    ...     r.eval("mean(x)")
    2.5

    ``eval`` returns the value of the last expression. Text that R code prints
    is echoed to ``sys.stdout``, R messages to ``sys.stderr``, and R warnings
    are raised as ``RWarning``. R errors raise ``RError``; the session stays
    usable afterwards. Sessions are not thread-safe, and R code that reads
    standard input or never returns cannot be interrupted other than by
    ``close()``.
    """

    def __init__(self) -> None:
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._count = 0
        self._start()

    def __enter__(self) -> "RSession":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001 - never raise from a destructor
            pass

    @property
    def alive(self) -> bool:
        """Whether the R process is running."""
        return self._proc is not None and self._proc.poll() is None

    def _start(self) -> None:
        if not RSCRIPT.is_file():
            raise RError(
                "bundled R is missing. Install a platform wheel, or run "
                "build_r_runtime.py when working from source."
            )
        self._tmp = tempfile.TemporaryDirectory(prefix="r4py-")
        self._proc = subprocess.Popen(
            [str(RSCRIPT), "--vanilla", str(_WORKER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=get_env(),
        )
        while True:
            line = self._readline()
            if line == _READY:
                return

    def _readline(self) -> str:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        raw = proc.stdout.readline()
        if raw == b"":
            code = proc.wait()
            self._proc = None
            raise RError(f"the R process exited unexpectedly (exit code {code})")
        return raw.decode("utf-8", "replace").rstrip("\r\n")

    def eval(self, code: str, *, echo: bool = True) -> object:
        """Evaluate R code and return the value of its last expression."""
        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            raise RError("the R session is closed; call restart() or make a new one")
        self._count += 1
        assert self._tmp is not None
        directory = Path(self._tmp.name)
        request = directory / f"{self._count}.R"
        result = directory / f"{self._count}.json"
        request.write_bytes(code.encode("utf-8"))
        try:
            proc.stdin.write(f"{request.as_posix()}\t{result.as_posix()}\n".encode())
            proc.stdin.flush()
            while (line := self._readline()) != _DONE:
                if echo:  # output R wrote outside its capture, e.g. from C code
                    print(line)
        except OSError as error:
            self.close()
            raise RError("the R process exited unexpectedly") from error
        except BaseException:
            # The conversation is out of sync (or R died); start clean next time.
            self.close()
            raise
        finally:
            request.unlink(missing_ok=True)
        payload: dict[str, Any] = json.loads(result.read_text(encoding="utf-8"))
        result.unlink(missing_ok=True)
        if echo and payload["stdout"]:
            print(payload["stdout"])
        if payload["stderr"]:
            sys.stderr.write(payload["stderr"])
        for text in payload["warnings"]:
            warnings.warn(RWarning(text), stacklevel=2)
        if not payload["ok"]:
            raise RError(payload["error"])
        return _decode(payload["value"])

    def assign(self, name: str, value: object) -> None:
        """Set an R variable in the global environment."""
        self.eval(f"assign({_quote(name)}, {to_r(value)}, envir = globalenv())")

    def call(self, function: str, *args: object, **kwargs: object) -> object:
        """Call an R function by name, e.g. ``call("stats::sd", [1, 2, 3])``."""
        parts = [to_r(arg) for arg in args]
        parts += [f"{_quote(key)} = {to_r(value)}" for key, value in kwargs.items()]
        return self.eval(
            f"do.call(eval(parse(text = {_quote(function)})), list({', '.join(parts)}))"
        )

    def restart(self) -> None:
        """Discard all R state and start a fresh process."""
        self.close()
        self._start()

    def close(self) -> None:
        """Stop the R process. Safe to call more than once."""
        proc, self._proc = self._proc, None
        if proc is not None:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()
        tmp, self._tmp = self._tmp, None
        if tmp is not None:
            tmp.cleanup()
