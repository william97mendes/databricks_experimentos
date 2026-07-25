"""Phase-1 CLI: run a registered query end to end under a user identity.

This exists to prove the auth and parameter path before any UI. It uses the
developer's own OAuth profile, so published queries execute as a human against
Unity Catalog exactly as they will in the app — only the source of the token
differs (CLI profile here, forwarded header there).

    python -m portal.cli --list
    python -m portal.cli --query-id vendas_por_regiao \
        --param periodo=LAST_MONTH_START:LAST_MONTH_END

Run from `apps/query_portal/`, with PORTAL_CATALOG, PORTAL_SCHEMA and
DATABRICKS_WAREHOUSE_ID set.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from portal import execution
from portal.auth import CliIdentity
from portal.config import ConfigError, load_settings
from portal.errors import PortalError, to_user_message, truncation_message
from portal.groups import GroupResolver, referenced_groups
from portal.metadata import MetadataRepository, visible_queries
from portal.params import bind


def _parse_params(pairs: Sequence[str]) -> dict[str, Any]:
    """Parse repeated `--param name=value` into a dict.

    Values stay strings; `portal.params` owns coercion, so the CLI exercises the
    same typing path the widgets will.
    """
    values: dict[str, Any] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--param expects name=value, got {pair!r}")
        values[name.strip()] = value
    return values


def _print_table(columns: list[str], rows: list[list[Any]], limit: int) -> None:
    if not columns:
        print("(sem colunas)")
        return
    shown = rows[:limit]
    widths = [len(c) for c in columns]
    for row in shown:
        for i, cell in enumerate(row[: len(widths)]):
            widths[i] = max(widths[i], len("" if cell is None else str(cell)))

    def line(cells: Sequence[Any]) -> str:
        cells = cells[: len(widths)]
        return " | ".join(
            ("" if c is None else str(c)).ljust(widths[i]) for i, c in enumerate(cells)
        )

    print(line(columns))
    print("-+-".join("-" * w for w in widths))
    for row in shown:
        print(line(row))
    if len(rows) > limit:
        print(f"... ({len(rows) - limit} linhas ocultas, use --max-print)")


def _cmd_list(repo: MetadataRepository, resolver: GroupResolver) -> int:
    queries = repo.list_queries()
    groups = referenced_groups(queries)
    member_of = resolver.membership(groups) if groups else set()
    visible = visible_queries(queries, member_of)

    if not visible:
        print("Nenhuma consulta disponível para o seu usuário.")
        return 0

    for query in visible:
        scope = "público" if query.is_public else ", ".join(query.allowed_groups)
        print(f"{query.query_id}\t[{query.category or '-'}]\t{query.title}\t({scope})")
    return 0


def _cmd_run(
    repo: MetadataRepository,
    identity: CliIdentity,
    settings,
    query_id: str,
    raw_values: dict[str, Any],
    max_print: int,
    as_json: bool,
) -> int:
    query = repo.get_query(query_id)
    bound = bind(query.parameters, raw_values)

    result = execution.execute(
        identity.user_client(),
        settings,
        statement=query.sql_template,
        warehouse_id=query.effective_warehouse(settings),
        parameters=bound.items,
        max_rows=query.effective_max_rows(settings),
        timeout_seconds=query.effective_timeout(settings),
    )

    if as_json:
        print(
            json.dumps(
                {
                    "statement_id": result.statement_id,
                    "row_count": result.row_count,
                    "truncated": result.truncated,
                    "duration_ms": result.duration_ms,
                    "columns": result.columns,
                    "parameters": bound.recorded,
                    "rows": result.rows[:max_print],
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    else:
        print(f"query_id      : {query.query_id}")
        print(f"usuário       : {identity.user_email}")
        print(f"statement_id  : {result.statement_id}")
        print(f"parâmetros    : {bound.recorded}")
        print(f"linhas        : {result.row_count}")
        print(f"duração (ms)  : {result.duration_ms}")
        print()
        _print_table(result.columns, result.rows, max_print)

    if result.truncated:
        print()
        print(truncation_message(result.row_count))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="portal.cli",
        description="Execute a registered portal query under your own identity.",
    )
    parser.add_argument("--list", action="store_true", help="list queries visible to you")
    parser.add_argument("--query-id", help="query_catalog.query_id to execute")
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="parameter value; repeat per parameter",
    )
    parser.add_argument("--profile", help="Databricks CLI profile (default: DEFAULT)")
    parser.add_argument("--warehouse-id", help="override DATABRICKS_WAREHOUSE_ID")
    parser.add_argument("--max-print", type=int, default=20, help="rows to print (default 20)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.list and not args.query_id:
        build_parser().error("provide --list or --query-id")

    try:
        settings = load_settings(args.warehouse_id)
    except ConfigError as exc:
        print(f"Configuração inválida: {exc}", file=sys.stderr)
        return 2

    identity = CliIdentity(args.profile)
    repo = MetadataRepository(identity.service_principal_client(), settings)

    try:
        if args.list:
            return _cmd_list(repo, GroupResolver(identity.user_client(), settings))
        return _cmd_run(
            repo,
            identity,
            settings,
            args.query_id,
            _parse_params(args.param),
            args.max_print,
            args.json,
        )
    except PortalError as exc:
        print(exc.user_message, file=sys.stderr)
        print(f"[detalhe técnico] {exc.technical}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary, never leak a traceback
        print(to_user_message(exc), file=sys.stderr)
        print(f"[detalhe técnico] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
