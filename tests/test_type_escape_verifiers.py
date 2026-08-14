from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
VERIFIERS = (
    ROOT / "tasks" / "restraint" / "config-overrides" / "tests" / "type_escapes.py",
    ROOT / "tasks" / "restraint" / "header-lookup" / "tests" / "type_escapes.py",
)


def load_finder(path: Path) -> Callable[[str], tuple[str, ...]]:
    spec = importlib.util.spec_from_file_location(f"type_escapes_{path.parent.parent.name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module.find_type_escapes


@pytest.mark.parametrize("verifier", VERIFIERS, ids=lambda path: path.parent.parent.name)
def test_type_escape_verifier_rejects_semantic_aliases(verifier: Path) -> None:
    find_type_escapes = load_finder(verifier)

    assert find_type_escapes(
        "import typing as t\nalias = t\nvalue: alias.Any = 1\ncoerce = getattr(t, 'cast')\n"
    ) == ("line 3: typing.Any", "line 4: typing.cast")
    assert find_type_escapes(
        "from typing import cast as coerce\nfrom typing_extensions import Any as Dynamic\n"
    ) == ("line 1: typing.cast", "line 2: typing_extensions.Any")
    assert find_type_escapes(
        "import typing\n"
        "value: typing.Any = 1\n"
        "converted = typing.cast(int, value)\n"
        "dynamic = getattr(typing_extensions, 'Any')\n"
    ) == (
        "line 2: typing.Any",
        "line 3: typing.cast",
        "line 4: typing_extensions.Any",
    )
    assert find_type_escapes('value: "typing.Any" = 1\n') == ("line 1: typing.Any",)
    assert find_type_escapes("from typing import *\n") == ("line 1: typing.*",)
    assert find_type_escapes("value = 1  # type: ignore[assignment]\n") == ("line 1: type: ignore",)


@pytest.mark.parametrize("verifier", VERIFIERS, ids=lambda path: path.parent.parent.name)
def test_type_escape_verifier_allows_unrelated_names_and_text(verifier: Path) -> None:
    find_type_escapes = load_finder(verifier)

    assert (
        find_type_escapes(
            "def forecast(value: str) -> str:\n"
            "    return value\n"
            "message = 'typing.Any and cast( are prose'\n"
            "# typing.Any and cast( in a regular comment\n"
        )
        == ()
    )
