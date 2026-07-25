-- Worked example of publishing a query: two INSERTs, no deploy.
-- Applied by the same job as the DDL, with :catalog and :schema bound.
--
-- Note every user value in sql_template is a :marker. The MULTI_SELECT parameter
-- arrives as one comma-joined STRING and is unpacked with split() inside SQL,
-- because the Statement Execution API has no ARRAY parameter type.

MERGE INTO IDENTIFIER(:catalog || '.' || :schema || '.query_catalog') AS target
USING (
  SELECT
    'vendas_por_regiao' AS query_id,
    'Vendas por região' AS title,
    'Receita e pedidos por região no período selecionado.' AS description,
    'Vendas' AS category,
    "SELECT r.regiao,\n       COUNT(DISTINCT p.pedido_id) AS pedidos,\n       SUM(p.valor_total)     AS receita\nFROM samples.vendas.pedido p\nJOIN samples.vendas.regiao r ON r.regiao_id = p.regiao_id\nWHERE p.data_pedido BETWEEN :periodo_inicio AND :periodo_fim\n  AND (:regioes IS NULL OR array_contains(split(:regioes, ','), r.regiao))\n  AND (:valor_minimo IS NULL OR p.valor_total >= :valor_minimo)\nGROUP BY r.regiao\nORDER BY receita DESC" AS sql_template,
    array('analistas-vendas', 'diretoria-comercial') AS allowed_groups,
    CAST(NULL AS STRING) AS warehouse_id,
    100000 AS max_rows,
    300 AS timeout_seconds,
    true AS is_active,
    'dados@empresa.com.br' AS owner_email
) AS source
ON target.query_id = source.query_id
WHEN MATCHED THEN UPDATE SET
  title = source.title,
  description = source.description,
  category = source.category,
  sql_template = source.sql_template,
  allowed_groups = source.allowed_groups,
  warehouse_id = source.warehouse_id,
  max_rows = source.max_rows,
  timeout_seconds = source.timeout_seconds,
  is_active = source.is_active,
  owner_email = source.owner_email,
  updated_at = current_timestamp()
WHEN NOT MATCHED THEN INSERT (
  query_id, title, description, category, sql_template, allowed_groups,
  warehouse_id, max_rows, timeout_seconds, is_active, owner_email,
  created_at, updated_at
) VALUES (
  source.query_id, source.title, source.description, source.category,
  source.sql_template, source.allowed_groups, source.warehouse_id,
  source.max_rows, source.timeout_seconds, source.is_active, source.owner_email,
  current_timestamp(), current_timestamp()
);

MERGE INTO IDENTIFIER(:catalog || '.' || :schema || '.query_parameter') AS target
USING (
  -- DATE_RANGE renders as one date_input and binds :periodo_inicio + :periodo_fim.
  -- The default is "último mês fechado", the request that comes up constantly.
  SELECT 'vendas_por_regiao' AS query_id, 'periodo' AS param_name, 'Período' AS label,
         'Intervalo de datas do pedido. Máximo de 366 dias.' AS help_text,
         'DATE_RANGE' AS param_type, 'DATE' AS sql_type, true AS is_required,
         'LAST_MONTH_START:LAST_MONTH_END' AS default_value,
         CAST(NULL AS STRING) AS options_sql,
         CAST(NULL AS ARRAY<STRING>) AS options_static,
         366 AS max_range_days, 10 AS display_order
  UNION ALL
  -- options_sql runs as the requesting user: a dropdown must never reveal values
  -- the user could not have queried themselves.
  SELECT 'vendas_por_regiao', 'regioes', 'Regiões',
         'Deixe vazio para incluir todas as regiões.',
         'MULTI_SELECT', 'STRING', false,
         CAST(NULL AS STRING),
         'SELECT DISTINCT regiao FROM samples.vendas.regiao ORDER BY regiao',
         CAST(NULL AS ARRAY<STRING>),
         CAST(NULL AS INT), 20
  UNION ALL
  SELECT 'vendas_por_regiao', 'valor_minimo', 'Valor mínimo do pedido',
         'Filtra pedidos abaixo deste valor.',
         'DECIMAL', 'DECIMAL(18,2)', false,
         CAST(NULL AS STRING),
         CAST(NULL AS STRING),
         CAST(NULL AS ARRAY<STRING>),
         CAST(NULL AS INT), 30
) AS source
ON target.query_id = source.query_id AND target.param_name = source.param_name
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;
