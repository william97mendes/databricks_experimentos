-- Metadata schema for the self-service query portal.
-- Applied by resources/jobs/apply_metadata_ddl.yml with :catalog and :schema bound
-- as job parameters. The catalog itself is owned by Terraform; this bundle owns
-- only the schema and these three tables.
--
-- IDENTIFIER() is used instead of string interpolation so object names bind as
-- parameters, consistent with the no-interpolation rule the app enforces.

CREATE SCHEMA IF NOT EXISTS IDENTIFIER(:catalog || '.' || :schema)
  COMMENT 'Self-service query portal metadata and audit log.';

-- --------------------------------------------------------------------------
-- query_catalog: one row per published query. Publishing = INSERT, not deploy.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :schema || '.query_catalog') (
  query_id        STRING  NOT NULL COMMENT 'Stable slug, used in URLs and filenames',
  title           STRING  NOT NULL COMMENT 'Shown in the list, Portuguese',
  description     STRING           COMMENT 'What the query answers, Portuguese',
  category        STRING           COMMENT 'Grouping in the list, e.g. Vendas',
  sql_template    STRING  NOT NULL COMMENT 'SQL with :named markers only. Never string-formatted.',
  allowed_groups  ARRAY<STRING>    COMMENT 'UX filter only, NOT security. Empty/null = visible to all.',
  warehouse_id    STRING           COMMENT 'Optional override of the app default warehouse',
  max_rows        INT              COMMENT 'row_limit applied on execution',
  timeout_seconds INT              COMMENT 'Statement is cancelled past this',
  is_active       BOOLEAN NOT NULL DEFAULT true,
  owner_email     STRING           COMMENT 'Who to contact about this query',
  created_at      TIMESTAMP NOT NULL DEFAULT current_timestamp(),
  updated_at      TIMESTAMP NOT NULL DEFAULT current_timestamp(),
  CONSTRAINT pk_query_catalog PRIMARY KEY (query_id)
)
USING DELTA
COMMENT 'Published queries. allowed_groups filters the list; UC grants enforce access.'
TBLPROPERTIES (delta.feature.allowColumnDefaults = 'supported');

-- --------------------------------------------------------------------------
-- query_parameter: widgets and typing for each query's inputs.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :schema || '.query_parameter') (
  query_id        STRING  NOT NULL,
  param_name      STRING  NOT NULL COMMENT 'Matches the :marker in sql_template',
  label           STRING  NOT NULL COMMENT 'Widget label, Portuguese',
  help_text       STRING           COMMENT 'Widget tooltip, Portuguese',
  param_type      STRING  NOT NULL COMMENT 'DATE|DATE_RANGE|STRING|INT|DECIMAL|SELECT|MULTI_SELECT',
  sql_type        STRING           COMMENT 'Explicit statement parameter type, e.g. DATE, INT',
  is_required     BOOLEAN NOT NULL DEFAULT false,
  default_value   STRING           COMMENT 'Literal, or TODAY|TODAY-30D|MONTH_START|LAST_MONTH_START|LAST_MONTH_END',
  options_sql     STRING           COMMENT 'Dropdown query. Runs AS THE USER, so it cannot leak values.',
  options_static  ARRAY<STRING>    COMMENT 'Fixed dropdown options, used when options_sql is null',
  max_range_days  INT              COMMENT 'DATE_RANGE only; validated before submission',
  display_order   INT     NOT NULL DEFAULT 0,
  CONSTRAINT pk_query_parameter PRIMARY KEY (query_id, param_name)
)
USING DELTA
COMMENT 'Parameter definitions. DATE_RANGE expands into :<name>_inicio and :<name>_fim.'
TBLPROPERTIES (delta.feature.allowColumnDefaults = 'supported');

-- --------------------------------------------------------------------------
-- execution_log: audit trail and the chargeback join key.
-- statement_id joins to system.query.history for bytes scanned and warehouse
-- time, which is how cost is attributed per requesting area.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.' || :schema || '.execution_log') (
  execution_id      STRING  NOT NULL COMMENT 'UUID minted by the app',
  query_id          STRING  NOT NULL,
  user_email        STRING  NOT NULL COMMENT 'Identity that executed, not the service principal',
  parameters        STRING           COMMENT 'JSON of the bound parameter values',
  statement_id      STRING           COMMENT 'Join key to system.query.history',
  warehouse_id      STRING,
  status            STRING  NOT NULL COMMENT 'SUCCEEDED|FAILED|TIMEOUT|CANCELED',
  error_message     STRING           COMMENT 'Technical detail, never shown raw to the user',
  row_count         BIGINT,
  duration_ms       BIGINT,
  downloaded_format STRING           COMMENT 'CSV|XLSX, set when the user downloads',
  started_at        TIMESTAMP NOT NULL,
  ended_at          TIMESTAMP,
  execution_date    DATE GENERATED ALWAYS AS (CAST(started_at AS DATE))
)
USING DELTA
PARTITIONED BY (execution_date)
COMMENT 'Every execution attempt. Also powers the "Minhas execuções" tab, which is the only window Consumer-access users have into their own history.';
