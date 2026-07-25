# databricks_experimentos

Monorepo for Databricks development: notebooks, Lakeflow pipelines, Databricks Apps,
Lakeview dashboards and SQL assets — all deployed with
[Databricks Asset Bundles](https://docs.databricks.com/dev-tools/bundles/index.html).

## Structure

```
.
├── databricks.yml           # Asset Bundle root (targets: dev / staging / prod)
├── notebooks/               # Notebooks, stored as source (.py with # Databricks notebook source)
│   ├── exploration/         # Ad-hoc / scratch analysis
│   ├── etl/                 # Ingestion & transformation (bronze → silver → gold)
│   ├── ml/                  # Feature engineering, training, inference
│   └── analytics/           # Reporting / business analysis
├── src/databricks_experimentos/
│   ├── jobs/                # Entry points invoked by job tasks
│   ├── pipelines/           # Lakeflow Declarative Pipelines (DLT) definitions
│   ├── transformations/     # Reusable, testable pure-Spark logic
│   └── utils/               # Config, logging, Spark session helpers
├── apps/                    # Databricks Apps (one folder per app)
├── dashboards/
│   ├── lakeview/            # .lvdash.json dashboard definitions
│   └── sql_queries/         # Standalone .sql queries powering dashboards
├── resources/               # Bundle resource definitions
│   ├── jobs/                # Job YAML
│   ├── pipelines/           # Pipeline YAML
│   └── clusters/            # Shared / job cluster YAML
├── sql/
│   ├── ddl/                 # Catalog, schema, table, volume DDL
│   ├── dml/                 # Seed / backfill / maintenance statements
│   └── models/              # Views & modelled marts
├── tests/
│   ├── unit/                # Local pytest, no workspace needed
│   └── integration/         # Run against a real workspace / cluster
├── data/
│   ├── sample/              # Small fixtures for tests (keep tiny)
│   └── schemas/             # JSON schema / contract definitions
├── scripts/                 # Dev helper scripts (deploy, fmt, lint)
└── docs/                    # Architecture, conventions, runbooks
```

## Getting started

1. Install the CLI and authenticate:

```bash
databricks auth login --host https://<your-workspace>.cloud.databricks.com
```

2. Set your workspace host in `databricks.yml` (replace `<your-workspace>`).

3. Validate and deploy to dev:

```bash
databricks bundle validate -t dev
```

```bash
databricks bundle deploy -t dev
```

4. Run a job:

```bash
databricks bundle run sample_etl_job -t dev
```

## Local development

```bash
pip install -r requirements-dev.txt
```

```bash
pytest tests/unit
```

## Conventions

- Notebooks are committed as **source files** (`.py` with the `# Databricks notebook source` header)
  so diffs are reviewable. Keep them thin — real logic belongs in `src/` and gets unit tested.
- Everything deployable is declared under `resources/` and referenced from `databricks.yml`.
- Never hardcode catalog/schema names; use the bundle variables `${var.catalog}` / `${var.schema}`.
- No secrets in the repo — use Databricks secret scopes (`dbutils.secrets.get`).

See [docs/conventions.md](docs/conventions.md) for details.
