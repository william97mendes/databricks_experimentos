-- Seed queries. Publishing is INSERT-only: no code change, no deploy.
--
-- These run against `samples.nyctaxi.trips`, which exists in every Databricks
-- workspace including Free Edition, so the portal returns real rows on first run.
-- The sample data covers Jan–Feb 2016, which is why query 1 defaults to a literal
-- 2016 period while query 2 exists to demonstrate the relative-date grammar.
--
-- Delete-then-insert keeps this re-runnable.

DELETE FROM IDENTIFIER(:catalog || '.' || :schema || '.query_catalog')
WHERE query_id IN ('corridas_por_cep', 'teste_datas_relativas', 'receita_confidencial');

DELETE FROM IDENTIFIER(:catalog || '.' || :schema || '.query_parameter')
WHERE query_id IN ('corridas_por_cep', 'teste_datas_relativas', 'receita_confidencial');

-- ---------------------------------------------------------------------------
-- 1. Real query over sample data. Visible to everyone (no allowed_groups).
--    Every user value is a :marker; the MULTI_SELECT arrives as one comma-joined
--    STRING and is unpacked with split() because the Statement Execution API has
--    no ARRAY parameter type.
-- ---------------------------------------------------------------------------
INSERT INTO IDENTIFIER(:catalog || '.' || :schema || '.query_catalog') VALUES (
  'corridas_por_cep',
  'Corridas por CEP de embarque',
  'Total de corridas, distância média e receita por CEP de embarque no período selecionado.',
  'Operações',
  'SELECT t.pickup_zip                        AS cep_embarque,
       COUNT(*)                            AS corridas,
       ROUND(AVG(t.trip_distance), 2)      AS distancia_media,
       ROUND(SUM(t.fare_amount), 2)        AS receita
FROM samples.nyctaxi.trips t
WHERE CAST(t.tpep_pickup_datetime AS DATE) BETWEEN :periodo_inicio AND :periodo_fim
  AND (:ceps IS NULL OR array_contains(split(:ceps, ","), CAST(t.pickup_zip AS STRING)))
  AND (:valor_minimo IS NULL OR t.fare_amount >= :valor_minimo)
GROUP BY t.pickup_zip
ORDER BY receita DESC',
  NULL,
  NULL,
  50000,
  120,
  true,
  'dados@empresa.com.br',
  current_timestamp(),
  current_timestamp()
);

INSERT INTO IDENTIFIER(:catalog || '.' || :schema || '.query_parameter') VALUES
  ('corridas_por_cep', 'periodo', 'Período',
   'Data de embarque. Os dados de exemplo cobrem janeiro e fevereiro de 2016.',
   'DATE_RANGE', 'DATE', true, '2016-01-01:2016-01-31',
   NULL, NULL, 366, 10),
  -- options_sql runs as the requesting user: a dropdown must never reveal values
  -- the user could not have queried themselves.
  ('corridas_por_cep', 'ceps', 'CEPs de embarque',
   'Deixe vazio para incluir todos os CEPs.',
   'MULTI_SELECT', 'STRING', false, NULL,
   'SELECT DISTINCT pickup_zip FROM samples.nyctaxi.trips ORDER BY pickup_zip LIMIT 50',
   NULL, NULL, 20),
  ('corridas_por_cep', 'valor_minimo', 'Valor mínimo da corrida',
   'Filtra corridas com tarifa abaixo deste valor.',
   'DECIMAL', 'DECIMAL(18,2)', false, NULL,
   NULL, NULL, NULL, 30);

-- ---------------------------------------------------------------------------
-- 2. Smoke test for the relative-date grammar. Touches no table, so it works
--    even before any data grants exist — useful for proving the OBO path.
--    Default is "último mês fechado".
-- ---------------------------------------------------------------------------
INSERT INTO IDENTIFIER(:catalog || '.' || :schema || '.query_catalog') VALUES (
  'teste_datas_relativas',
  'Teste de datas relativas',
  'Mostra como o período padrão "último mês fechado" foi resolvido. Não consulta nenhuma tabela.',
  'Diagnóstico',
  'SELECT :periodo_inicio AS data_inicial,
       :periodo_fim    AS data_final,
       datediff(:periodo_fim, :periodo_inicio) + 1 AS dias_no_periodo,
       current_date()  AS hoje_no_warehouse',
  NULL,
  NULL,
  10,
  60,
  true,
  'dados@empresa.com.br',
  current_timestamp(),
  current_timestamp()
);

INSERT INTO IDENTIFIER(:catalog || '.' || :schema || '.query_parameter') VALUES
  ('teste_datas_relativas', 'periodo', 'Período',
   'O padrão é o último mês fechado, resolvido no fuso configurado no app.',
   'DATE_RANGE', 'DATE', true, 'LAST_MONTH_START:LAST_MONTH_END',
   NULL, NULL, 366, 10);

-- ---------------------------------------------------------------------------
-- 3. Restricted query, to demonstrate list filtering. On Free Edition no
--    account groups exist, so this stays hidden while PORTAL_GROUP_FILTERING is
--    on — which is exactly what proves the filter works.
--
--    Reminder: this hides the row. It does NOT protect the data. Anything the
--    query reads is protected only by Unity Catalog grants.
-- ---------------------------------------------------------------------------
INSERT INTO IDENTIFIER(:catalog || '.' || :schema || '.query_catalog') VALUES (
  'receita_confidencial',
  'Receita consolidada (confidencial)',
  'Exemplo de consulta restrita a um grupo. Não deve aparecer para usuários fora do grupo.',
  'Financeiro',
  'SELECT ROUND(SUM(fare_amount), 2) AS receita_total
FROM samples.nyctaxi.trips
WHERE CAST(tpep_pickup_datetime AS DATE) BETWEEN :periodo_inicio AND :periodo_fim',
  array('diretoria-financeira'),
  NULL,
  10,
  60,
  true,
  'dados@empresa.com.br',
  current_timestamp(),
  current_timestamp()
);

INSERT INTO IDENTIFIER(:catalog || '.' || :schema || '.query_parameter') VALUES
  ('receita_confidencial', 'periodo', 'Período',
   NULL,
   'DATE_RANGE', 'DATE', true, '2016-01-01:2016-01-31',
   NULL, NULL, 366, 10);
