# Configuração manual — Databricks Free Edition

Passo a passo para colocar o portal no ar em uma conta Free Edition, sem
Terraform. Leva ~20 minutos. Cada passo diz **onde clicar** e **como conferir**.

> Códigos e comentários em inglês; a interface do portal e este guia em português.

---

## 0. O que a Free Edition permite (e o que não permite)

Confirme estas restrições antes de começar — elas explicam várias decisões do guia:

| Recurso | Free Edition | Impacto no portal |
|---|---|---|
| SQL warehouse | **1 warehouse, 2X-Small** | Todas as consultas usam o mesmo. Sem override por consulta na prática. |
| Databricks Apps | **até 3 apps**, param após **24h** | É preciso reiniciar o app no dia seguinte. |
| Console de conta / APIs de conta | **Não disponível** | **Não é possível criar grupos de conta.** |
| SSO / SCIM | **Não disponível** | Sem sincronismo com Entra ID; você é o único usuário. |
| `system.query.history` | **Não disponível** | O `statement_id` é gravado, mas o *chargeback* só funciona em workspace pago. |
| Metastore / workspace | 1 de cada | Use o catálogo `workspace`, já existente. |

**Consequência principal:** sem grupos de conta, `is_account_group_member()` não
tem o que resolver e o filtro por grupo esconderia consultas restritas para
todo mundo. Por isso o alvo `dev` do bundle define
`PORTAL_GROUP_FILTERING=false`.

Isso **não** enfraquece a segurança: `allowed_groups` nunca foi o controle de
acesso — quem decide o que cada pessoa lê é o Unity Catalog. Veja
[README](../README.md#allowed_groups-não-é-segurança).

---

## 1. Criar a conta e abrir o workspace

1. Acesse <https://login.databricks.com/> e escolha **Free Edition**.
2. Entre com e-mail (OTP), Google ou Microsoft.
3. Você cai direto no único workspace da conta.

✅ **Confira:** o seletor de catálogo em **Catalog** mostra `workspace` e `samples`.

---

## 2. Anotar o SQL warehouse

1. Menu lateral → **SQL Warehouses**.
2. Existe um warehouse pronto (normalmente *Serverless Starter Warehouse*). Abra-o.
3. Copie o **ID** — está na URL (`/sql/warehouses/<ID>`) e na aba **Connection details**.

✅ **Confira:** o estado fica **Running** depois do primeiro uso (ele liga sob demanda).

> Guarde esse ID: é a única informação obrigatória de configuração.

---

## 3. Criar o schema e as tabelas de metadados

O portal precisa de três tabelas em `workspace.portal`. O SQL está em
[`sql/ddl/001_portal_schema.sql`](../sql/ddl/001_portal_schema.sql) e usa
`IDENTIFIER(:catalog || …)`, ou seja, os nomes entram como **parâmetros**.

**Pelo editor SQL (mais simples):**

1. Menu lateral → **SQL Editor** → **New query**.
2. Cole o conteúdo do arquivo.
3. Troque os marcadores pelos valores fixos apenas nesta execução manual —
   ou, mais simples, rode as três instruções abaixo:

```sql
CREATE SCHEMA IF NOT EXISTS workspace.portal;
```

4. Depois cole os três `CREATE TABLE` do arquivo, substituindo
   `IDENTIFIER(:catalog || '.' || :schema || '.query_catalog')` por
   `workspace.portal.query_catalog` (e equivalentes).

✅ **Confira:**

```sql
SHOW TABLES IN workspace.portal;
```

Deve listar `query_catalog`, `query_parameter` e `execution_log`.

> Na implantação via bundle (passo 7) isso é feito pelo job `apply_metadata_ddl`,
> que passa `:catalog` e `:schema` corretamente — sem edição manual.

---

## 4. Publicar as consultas de exemplo

Mesma ideia com [`sql/dml/001_seed_example_query.sql`](../sql/dml/001_seed_example_query.sql),
trocando os `IDENTIFIER(...)` por `workspace.portal.<tabela>`.

Ele cadastra três consultas sobre `samples.nyctaxi.trips` (existe em toda conta):

| query_id | Para que serve |
|---|---|
| `corridas_por_cep` | Consulta real com período, multiseleção e valor mínimo. Retorna linhas de verdade. |
| `teste_datas_relativas` | Não lê tabela nenhuma. Mostra como “último mês fechado” foi resolvido — ótimo para testar autenticação. |
| `receita_confidencial` | Restrita a um grupo inexistente. Serve para provar que o filtro esconde consultas. |

✅ **Confira:**

```sql
SELECT query_id, title, allowed_groups FROM workspace.portal.query_catalog;
```

> Os dados de exemplo cobrem **jan–fev/2016**, por isso `corridas_por_cep` tem
> período padrão fixo em 2016. Um padrão relativo (`LAST_MONTH_START`) traria
> zero linhas — é exatamente por isso que existe a consulta de diagnóstico.

---

## 5. Testar pela linha de comando (antes de subir o app)

Isto valida autenticação e parâmetros sem depender do app.

```bash
databricks auth login --host https://<seu-workspace>.cloud.databricks.com
```

```bash
cd apps/query_portal
export PORTAL_CATALOG=workspace PORTAL_SCHEMA=portal DATABRICKS_WAREHOUSE_ID=<ID_DO_WAREHOUSE>
python -m portal.cli --list
```

No Windows (PowerShell):

```powershell
$env:PORTAL_CATALOG="workspace"; $env:PORTAL_SCHEMA="portal"; $env:DATABRICKS_WAREHOUSE_ID="<ID>"
```

Rode a consulta de diagnóstico:

```bash
python -m portal.cli --query-id teste_datas_relativas
```

✅ **Confira:** aparece `data_inicial` / `data_final` do **último mês fechado** no
fuso `America/Sao_Paulo`, mais o `statement_id`.

E a consulta com dados reais:

```bash
python -m portal.cli --query-id corridas_por_cep --max-print 10
```

---

## 6. Criar o app

1. Menu lateral → **Compute** → aba **Apps** → **Create app**.
2. Escolha **Custom app**, nome `query-portal`.
3. Em **App resources**, adicione o **SQL warehouse** do passo 2 com permissão
   **CAN USE**. O nome do recurso precisa ser `sql_warehouse` (é o que
   `app.yaml` referencia em `valueFrom`).
4. Crie o app. **Não faça deploy ainda** — anote o *service principal* mostrado
   na aba **Authorization**.

✅ **Confira:** o app aparece com estado *Stopped* e um service principal próprio.

---

## 7. Conceder permissões (o passo que realmente importa)

São **duas identidades** com necessidades opostas.

### 7.1 Service principal do app — só metadados

No SQL Editor, troque `<SP>` pelo service principal do passo 6:

```sql
GRANT USE CATALOG ON CATALOG workspace TO `<SP>`;
GRANT USE SCHEMA  ON SCHEMA  workspace.portal TO `<SP>`;
GRANT SELECT ON TABLE workspace.portal.query_catalog   TO `<SP>`;
GRANT SELECT ON TABLE workspace.portal.query_parameter TO `<SP>`;
GRANT SELECT, MODIFY ON TABLE workspace.portal.execution_log TO `<SP>`;
```

> **Não** conceda acesso a `samples.nyctaxi` ao service principal. Ele nunca deve
> conseguir executar uma consulta publicada — se conseguir, o modelo de duas
> identidades deixou de valer.

### 7.2 Você (e futuros usuários) — os dados de verdade

Na Free Edition você é admin e já enxerga `samples`, então normalmente não há o
que fazer. Em workspace pago, cada usuário precisa de `SELECT` nas tabelas que a
consulta lê **e** nas que alimentam os dropdowns (`options_sql`).

✅ **Confira:**

```sql
SHOW GRANTS ON TABLE workspace.portal.query_catalog;
```

---

## 8. Publicar o código do app

**Opção A — via bundle (recomendada):**

```bash
databricks bundle deploy -t dev --var="warehouse_id=<ID_DO_WAREHOUSE>"
```

```bash
databricks bundle run apply_metadata_ddl -t dev --var="warehouse_id=<ID_DO_WAREHOUSE>"
```

O alvo `dev` já usa `workspace` / `portal` e desliga o filtro por grupo.

**Opção B — upload manual:** copie a pasta `apps/query_portal/` para
`/Workspace/Users/<você>/query_portal` e aponte o *source code path* do app para lá.

Depois, no app: **Deploy**.

✅ **Confira:** o estado vira **Running** e o log mostra o Streamlit subindo.

---

## 9. Abrir e validar

Abra a URL do app.

| O que você deve ver | Por que |
|---|---|
| “Conectado como *seu e-mail*” | O token do usuário está sendo repassado |
| **2** consultas na lista | `receita_confidencial` fica oculta se o filtro estiver ligado |
| Período já preenchido | Gramática de datas relativas funcionando |
| Resultado + botões CSV/Excel | Execução e download OK |
| Aba **Minhas execuções** com linhas | Auditoria gravando |

✅ **Confirmação final da auditoria:**

```sql
SELECT query_id, user_email, status, row_count, statement_id, downloaded_format
FROM workspace.portal.execution_log
ORDER BY started_at DESC LIMIT 10;
```

---

## 10. Problemas comuns

| Sintoma | Causa provável | Correção |
|---|---|---|
| App para com “não está configurado para executar consultas em nome do usuário” | Falta `user_api_scopes` | Confirme os dois escopos em `app.yaml` e refaça o deploy. **É proposital**: o app se recusa a rodar como service principal. |
| Lista vazia | SP sem `SELECT` nos metadados | Refaça o passo 7.1 |
| “Você não tem acesso a esta consulta” | Falta grant de UC na tabela de dados | Passo 7.2 — o `allowed_groups` não substitui isso |
| Nenhuma consulta restrita aparece | Sem grupos de conta (esperado na Free Edition) | `PORTAL_GROUP_FILTERING=false` |
| Dropdown vazio | `options_sql` falhou para o **usuário** | Verifique o grant na tabela de origem |
| App parou sozinho | Limite de 24h da Free Edition | Reinicie pelo botão **Run** |
| Zero linhas em `corridas_por_cep` | Período fora de jan–fev/2016 | Ajuste o período |

---

## 11. Publicar uma consulta nova (sem deploy)

O ponto central do projeto: publicar é `INSERT`, não implantação.

```sql
INSERT INTO workspace.portal.query_catalog VALUES (
  'minha_consulta', 'Minha consulta', 'O que ela responde.', 'Operações',
  'SELECT * FROM samples.nyctaxi.trips
   WHERE CAST(tpep_pickup_datetime AS DATE) BETWEEN :periodo_inicio AND :periodo_fim',
  NULL, NULL, 10000, 120, true, 'voce@empresa.com', current_timestamp(), current_timestamp()
);

INSERT INTO workspace.portal.query_parameter VALUES
  ('minha_consulta', 'periodo', 'Período', NULL, 'DATE_RANGE', 'DATE', true,
   '2016-01-01:2016-01-31', NULL, NULL, 366, 10);
```

Recarregue o portal (o cache do catálogo dura 5 minutos). **Nenhum deploy.**

> Use sempre marcadores `:nome`. Nunca concatene valor de usuário no SQL — há um
> teste automatizado que quebra o build se alguém tentar.
