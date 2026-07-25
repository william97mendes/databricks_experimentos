# Portal de consultas — Databricks App

Portal interno de autoatendimento: usuários de negócio escolhem uma consulta
publicada, preenchem parâmetros, executam e baixam o resultado em CSV ou XLSX.

**O portal é orientado a metadados.** Publicar uma consulta nova é inserir linhas
em uma tabela Delta — não há alteração de código nem implantação. Se algo exigir
deploy para adicionar uma consulta, está errado.

Interface em português; código e comentários em inglês.

---

## Comece por aqui

- **Free Edition, passo a passo:** [`docs/setup-free-edition.md`](docs/setup-free-edition.md)
- **Publicar uma consulta:** [seção abaixo](#publicando-uma-consulta-nova)
- **Modelo de segurança:** [seção abaixo](#as-duas-identidades)

---

## Como funciona

```
Usuário ──(OAuth repassado)──▶ consulta publicada ──▶ Unity Catalog decide
   │
   └── App ──(service principal)──▶ metadados + log de auditoria
```

Três tabelas Delta em `<catalog>.<schema>`:

| Tabela | Conteúdo |
|---|---|
| `query_catalog` | Uma linha por consulta publicada: título, SQL, grupos, limites |
| `query_parameter` | Definição de cada parâmetro: tipo, rótulo, padrão, dropdown |
| `execution_log` | Toda execução: usuário, parâmetros, `statement_id`, status, duração |

---

## As duas identidades

Esta é a regra central do projeto.

**1. O usuário final executa as consultas.** O token OAuth repassado no cabeçalho
`x-forwarded-access-token` é quem roda toda consulta publicada — e também as
consultas de dropdown (`options_sql`). Portanto **o Unity Catalog é a fronteira
de autorização real**, não o aplicativo.

**2. O service principal só cuida da infraestrutura do portal:** lê os metadados
e grava a auditoria. Ele **nunca** executa uma consulta publicada, e não deve ter
acesso às tabelas de negócio.

Para isso funcionar, `app.yaml` declara:

```yaml
user_api_scopes:
  - sql
  - iam.current-user:read
```

Sem o escopo `sql`, o token do usuário não consegue executar instruções e o SDK
volta silenciosamente para o service principal — ou seja, todo mundo passaria a
consultar com os privilégios do app. Como essa falha é silenciosa e grave, o app
**se recusa a iniciar** quando o token repassado não chega
([`portal/auth.py`](apps/query_portal/portal/auth.py)).

### `allowed_groups` **não** é segurança

`allowed_groups` decide **o que aparece na lista**. Só isso.

Ele **não** protege dado nenhum. Quem pode ler cada tabela é definido pelos
*grants* do Unity Catalog. Um usuário que chegue à consulta por outro caminho é
barrado pelo UC, não pelo portal — e um usuário que esteja no grupo mas sem grant
recebe “Você não tem acesso a esta consulta”.

Consequência prática: desligar o filtro (`PORTAL_GROUP_FILTERING=false`, padrão
na Free Edition, que não tem grupos de conta) **não abre acesso a nada**.

### Sem interpolação de SQL

Todo valor vindo do usuário vira `StatementParameterListItem` com tipo explícito;
os templates usam apenas marcadores `:nome`.
[`portal/sql.py`](apps/query_portal/portal/sql.py) é o **único** módulo que monta
texto SQL, e substitui apenas identificadores validados — nunca valores.

Isso é verificado estaticamente: `tests/unit/test_no_sql_interpolation.py`
percorre a AST de cada módulo e quebra o build se um f-string ou concatenação
chegar ao argumento `statement=`.

---

## Publicando uma consulta nova

Dois `INSERT`. Sem deploy.

```sql
INSERT INTO governance.portal.query_catalog VALUES (
  'vendas_por_regiao',
  'Vendas por região',
  'Receita e pedidos por região no período.',
  'Vendas',
  'SELECT r.regiao, SUM(p.valor_total) AS receita
   FROM vendas.pedido p
   JOIN vendas.regiao r ON r.regiao_id = p.regiao_id
   WHERE p.data_pedido BETWEEN :periodo_inicio AND :periodo_fim
     AND (:regioes IS NULL OR array_contains(split(:regioes, ","), r.regiao))
   GROUP BY r.regiao',
  array('analistas-vendas'),   -- filtro de exibição, NÃO segurança
  NULL,                        -- warehouse padrão
  100000,                      -- max_rows
  300,                         -- timeout_seconds
  true, 'dados@empresa.com.br', current_timestamp(), current_timestamp()
);

INSERT INTO governance.portal.query_parameter VALUES
  ('vendas_por_regiao', 'periodo', 'Período', 'Máximo de 366 dias.',
   'DATE_RANGE', 'DATE', true, 'LAST_MONTH_START:LAST_MONTH_END',
   NULL, NULL, 366, 10),
  ('vendas_por_regiao', 'regioes', 'Regiões', 'Vazio = todas.',
   'MULTI_SELECT', 'STRING', false, NULL,
   'SELECT DISTINCT regiao FROM vendas.regiao ORDER BY 1', NULL, NULL, 20);
```

Regras ao escrever o `sql_template`:

- Use **apenas** marcadores `:nome`. Nunca concatene entrada do usuário.
- `DATE_RANGE` gera **dois** marcadores: `:<nome>_inicio` e `:<nome>_fim`.
- `MULTI_SELECT` chega como **uma** string separada por vírgula (a API não tem
  tipo ARRAY) — desempacote com `split(:param, ",")`.
- Parâmetro opcional em branco chega como `NULL`; trate com
  `(:p IS NULL OR …)`.
- `options_sql` roda **como o usuário**: um dropdown nunca pode revelar valores
  que a pessoa não poderia consultar.

### Tipos de parâmetro

`DATE` · `DATE_RANGE` · `STRING` · `INT` · `DECIMAL` · `SELECT` · `MULTI_SELECT`

### Datas relativas em `default_value`

`TODAY` · `TODAY-30D` · `TODAY+7D` · `MONTH_START` · `LAST_MONTH_START` ·
`LAST_MONTH_END` · ou uma data ISO (`2016-01-31`).

`LAST_MONTH_START:LAST_MONTH_END` é o “último mês fechado” que a área de negócio
pede o tempo todo. Tudo é resolvido no fuso de `PORTAL_TIMEZONE`
(padrão `America/Sao_Paulo`) — não no UTC do contêiner.

---

## Estrutura

```
apps/query_portal/          # tudo que o app enxerga em execução
├── app.py                  # entrada: assertiva de startup + abas
├── app.yaml                # user_api_scopes + env
└── portal/
    ├── auth.py             # as duas identidades (fronteira de segurança)
    ├── sql.py              # ÚNICO módulo que monta texto SQL
    ├── config.py           # env → Settings
    ├── metadata.py         # leitura do catálogo (service principal)
    ├── groups.py           # is_account_group_member(), como o usuário
    ├── params.py           # tipagem, datas relativas, validação
    ├── options.py          # dropdowns, como o usuário
    ├── execution.py        # submit assíncrono, poll, cancelamento
    ├── results.py          # CSV (utf-8-sig) e XLSX em streaming
    ├── audit.py            # execution_log
    ├── errors.py           # mensagens acionáveis em português
    ├── cli.py              # execução por linha de comando
    └── ui/                 # Streamlit (lista, formulário, resultado, histórico)
sql/ddl · sql/dml           # schema e consultas de exemplo
resources/                  # recursos do bundle (app, job de DDL)
tests/unit                  # 153 testes, API mockada, sem Spark
docs/setup-free-edition.md  # guia de configuração manual
```

O código do portal fica **dentro** da pasta do app porque o Databricks Apps só
publica arquivos sob o `source_code_path`. Os testes importam `portal.*` via
`pythonpath` configurado em `pyproject.toml` — uma cópia só do código.

---

## Configuração

| Variável | Padrão | Para que serve |
|---|---|---|
| `DATABRICKS_WAREHOUSE_ID` | — | **Obrigatória** |
| `PORTAL_CATALOG` | `workspace` | Catálogo dos metadados |
| `PORTAL_SCHEMA` | `portal` | Schema dos metadados |
| `PORTAL_TIMEZONE` | `America/Sao_Paulo` | Fuso das datas relativas |
| `PORTAL_GROUP_FILTERING` | `true` | Filtro por grupo (UX). `false` na Free Edition |
| `PORTAL_HISTORY_LIMIT` | `20` | Linhas em “Minhas execuções” |
| `PORTAL_EXTERNAL_LINKS_THRESHOLD` | `50000` | Acima disso, resultado via EXTERNAL_LINKS |

---

## Desenvolvimento

```bash
pip install -r requirements-dev.txt
```

```bash
pytest tests/unit && ruff check .
```

Executar uma consulta sem subir o app (usa seu perfil da CLI):

```bash
cd apps/query_portal && python -m portal.cli --list
```

Implantar:

```bash
databricks bundle deploy -t dev --var="warehouse_id=<ID>"
```

Alvos: `dev` (Free Edition — `workspace.portal`, filtro de grupo desligado) e
`prd` (`governance.portal`, filtro ligado).

---

## Limitações conhecidas na Free Edition

- **Sem grupos de conta** → o filtro de `allowed_groups` fica desligado.
- **`system.query.history` indisponível** → o `statement_id` é gravado, mas o
  rateio de custo por área só funciona em workspace pago.
- **App para após 24h** e há **um único warehouse 2X-Small**.

Detalhes e contornos em [`docs/setup-free-edition.md`](docs/setup-free-edition.md).
