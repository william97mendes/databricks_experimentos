# Conventions

## Where code goes

| Kind of code | Location | Tested by |
|---|---|---|
| Reusable Spark logic | `src/databricks_experimentos/transformations/` | `tests/unit` |
| Job entry points | `src/databricks_experimentos/jobs/` | `tests/integration` |
| DLT pipeline definitions | `src/databricks_experimentos/pipelines/` | pipeline expectations |
| Orchestration / thin glue | `notebooks/` | run in a job |

Rule of thumb: if you would want to unit test it, it does not belong in a notebook.

## Notebooks

- Commit as source `.py` files with the `# Databricks notebook source` first line and
  `# COMMAND ----------` cell separators. Never commit `.ipynb` with outputs.
- Take inputs via `dbutils.widgets`, never hardcoded catalog/schema strings.
- End with `dbutils.notebook.exit(...)` when the notebook is a job task.

## Environments

Three bundle targets — `dev`, `staging`, `prod` — differing only in workspace root path,
schema and permissions. Deployments are driven exclusively by
`databricks bundle deploy -t <target>`; nothing is created by hand in the UI.

## Naming

- Tables: `<domain>_<layer>`, e.g. `orders_bronze`, `orders_silver`, `daily_revenue_gold`.
- Jobs and pipelines: prefixed with the target, e.g. `[dev] sample_etl_job`.
- Branches: `feat/`, `fix/`, `chore/`.

## Secrets

Use Databricks secret scopes only:

```python
token = dbutils.secrets.get(scope="experimentos", key="api_token")
```

No `.env` files, tokens or connection strings in the repo. `.env` is gitignored;
`.env.example` documents the variable names.

## Before opening a PR

```bash
ruff check . && pytest tests/unit && databricks bundle validate -t dev
```
