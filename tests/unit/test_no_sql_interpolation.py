"""Static guarantee that no code path concatenates user input into a statement.

This is a static check, not a runtime one, so it covers branches the test suite
never executes. It walks every module under `portal/` and, per function, marks
names assigned from a string-construction expression (f-string, `+`, `%`,
`.format()`, `.join()`, `.replace()`) as constructed. It then fails if any
`execute_statement` call receives a constructed expression, or a name bound to
one, as its `statement`.

`portal/sql.py` is exempt because it is the single audited construction surface:
it substitutes only identifiers, and only after `identifier()` validation. Its
guarantees are covered by runtime tests at the bottom of this file.

Limits, stated plainly: the taint analysis is intraprocedural and does not follow
values across function boundaries. It catches the realistic mistake — building a
statement string next to the call that sends it — not an adversarial author.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from portal import sql
from portal.config import Settings
from portal.sql import UnsafeIdentifierError, identifier, qualify

PORTAL_DIR = Path(__file__).resolve().parents[2] / "apps" / "query_portal" / "portal"
EXEMPT = {"sql.py"}
EXECUTION_CALLS = {"execute_statement"}
CONSTRUCTION_METHODS = {"format", "join", "replace", "format_map"}


def portal_modules() -> list[Path]:
    return sorted(p for p in PORTAL_DIR.rglob("*.py") if p.name not in EXEMPT)


def test_portal_modules_are_discovered():
    """Guard against the scan silently passing because it found nothing."""
    names = {p.name for p in portal_modules()}
    assert {"execution.py", "metadata.py", "groups.py", "params.py"} <= names


def _is_construction(node: ast.AST) -> bool:
    """True when this expression builds a string by substitution or concatenation."""
    if isinstance(node, ast.JoinedStr):  # f-string
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in CONSTRUCTION_METHODS:
            return True
    return False


def _contains_construction(node: ast.AST) -> bool:
    return any(_is_construction(child) for child in ast.walk(node))


def _constructed_names(scope: ast.AST) -> set[str]:
    """Names in this scope bound to a string-construction expression."""
    names: set[str] = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and _contains_construction(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if node.value is not None and _contains_construction(node.value):
                if isinstance(node.target, ast.Name):
                    names.add(node.target.id)
    return names


def _statement_argument(call: ast.Call) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == "statement":
            return keyword.value
    return call.args[0] if call.args else None


def _execute_calls(tree: ast.AST) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in EXECUTION_CALLS:
                calls.append(node)
    return calls


def _scopes(tree: ast.AST):
    yield tree
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _parse(module_path: Path) -> ast.AST:
    # utf-8-sig: files authored on Windows may carry a BOM, which would otherwise
    # fail the parse and pass the scan for the wrong reason.
    return ast.parse(module_path.read_text(encoding="utf-8-sig"))


@pytest.mark.parametrize("module_path", portal_modules(), ids=lambda p: p.name)
def test_no_constructed_string_reaches_execute_statement(module_path: Path):
    tree = _parse(module_path)

    for scope in _scopes(tree):
        constructed = _constructed_names(scope)
        for call in _execute_calls(scope):
            argument = _statement_argument(call)
            if argument is None:
                continue

            location = f"{module_path.name}:{call.lineno}"
            assert not _is_construction(argument), (
                f"{location}: statement= is built by string construction. "
                "Pass user values as StatementParameterListItem instead."
            )
            if isinstance(argument, ast.Name):
                assert argument.id not in constructed, (
                    f"{location}: statement= is the variable {argument.id!r}, which was "
                    "assigned from a constructed string. Route SQL text through portal.sql."
                )


@pytest.mark.parametrize("module_path", portal_modules(), ids=lambda p: p.name)
def test_only_portal_sql_builds_statement_text(module_path: Path):
    """Statements must arrive from `portal.sql` or from a metadata column."""
    tree = _parse(module_path)

    for scope in _scopes(tree):
        for call in _execute_calls(scope):
            argument = _statement_argument(call)
            if argument is None or isinstance(argument, ast.Constant):
                continue
            assert isinstance(argument, (ast.Name, ast.Attribute, ast.Call)), (
                f"{module_path.name}:{call.lineno}: unexpected statement= expression "
                f"{type(argument).__name__}"
            )
            if isinstance(argument, ast.Call):
                func = argument.func
                assert isinstance(func, ast.Attribute) and _module_of(func) == "sql", (
                    f"{module_path.name}:{call.lineno}: statement= must come from portal.sql"
                )


def _module_of(func: ast.Attribute) -> str | None:
    return func.value.id if isinstance(func.value, ast.Name) else None


# --------------------------------------------------------------------------- #
# Runtime guarantees of the exempt module
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [
        "governance; DROP TABLE x",
        "gov.portal",
        "gov`portal",
        "gov portal",
        "1_catalog",
        "",
        "--comment",
        "gov'or'1'='1",
    ],
)
def test_identifier_rejects_anything_but_a_bare_identifier(value: str):
    with pytest.raises(UnsafeIdentifierError):
        identifier(value)


@pytest.mark.parametrize("value", ["governance", "portal", "_x", "q1_catalog"])
def test_identifier_accepts_valid_names(value: str):
    assert identifier(value) == value


def test_qualify_validates_every_part():
    hostile = Settings(catalog="main; DROP TABLE t", schema="portal", warehouse_id="w")
    with pytest.raises(UnsafeIdentifierError):
        qualify(hostile, "query_catalog")


def test_metadata_statements_carry_markers_not_values(settings: Settings):
    """The only substitution in portal.sql is the validated table name."""
    for statement in (
        sql.select_active_queries(settings),
        sql.select_query_by_id(settings),
        sql.select_query_parameters(settings),
    ):
        assert "governance.portal." in statement
    assert ":query_id" in sql.select_query_by_id(settings)
    assert ":query_id" in sql.select_query_parameters(settings)
