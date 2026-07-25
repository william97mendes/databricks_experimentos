"""Group filtering: what the list shows, and how membership is resolved."""

from __future__ import annotations

import pytest
from portal import sql
from portal.groups import GroupResolver, referenced_groups, resolve_membership
from portal.metadata import visible_queries

from tests.conftest import FakeWorkspaceClient, make_query, response


def membership_response(pairs):
    return response(
        state="SUCCEEDED",
        columns=["group_name", "is_member"],
        rows=[[name, str(is_member).lower()] for name, is_member in pairs],
    )


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #


def test_query_without_groups_is_visible_to_everyone():
    queries = [make_query(query_id="publica", allowed_groups=[])]
    assert len(visible_queries(queries, set())) == 1


def test_restricted_query_is_hidden_from_non_members():
    queries = [make_query(query_id="rh", allowed_groups=["rh-analistas"])]
    assert visible_queries(queries, {"vendas"}) == []


def test_restricted_query_is_shown_to_members():
    queries = [make_query(query_id="rh", allowed_groups=["rh-analistas"])]
    assert len(visible_queries(queries, {"rh-analistas"})) == 1


def test_any_matching_group_grants_visibility():
    queries = [make_query(allowed_groups=["a", "b", "c"])]
    assert len(visible_queries(queries, {"c"})) == 1


def test_group_matching_ignores_case_and_padding():
    """SCIM-synced Entra group names vary in casing across sources."""
    queries = [make_query(allowed_groups=["  RH-Analistas "])]
    assert len(visible_queries(queries, {"rh-analistas"})) == 1


def test_filtering_preserves_catalog_order():
    queries = [
        make_query(query_id="a", allowed_groups=[]),
        make_query(query_id="b", allowed_groups=["x"]),
        make_query(query_id="c", allowed_groups=[]),
    ]
    assert [q.query_id for q in visible_queries(queries, set())] == ["a", "c"]


# --------------------------------------------------------------------------- #
# Membership resolution
# --------------------------------------------------------------------------- #


def test_referenced_groups_is_deduplicated_and_order_stable():
    queries = [
        make_query(query_id="a", allowed_groups=["vendas", "diretoria"]),
        make_query(query_id="b", allowed_groups=["vendas", " "]),
        make_query(query_id="c", allowed_groups=["rh"]),
    ]
    assert referenced_groups(queries) == ["vendas", "diretoria", "rh"]


def test_membership_is_resolved_in_a_single_round_trip(settings):
    client = FakeWorkspaceClient(
        [membership_response([("vendas", True), ("rh", False), ("diretoria", True)])]
    )

    member_of = resolve_membership(client, settings, ["vendas", "rh", "diretoria"])

    assert member_of == {"vendas", "diretoria"}
    assert len(client.statement_execution.calls) == 1, "one call regardless of group count"


def test_group_names_bind_as_parameters_never_as_text(settings):
    client = FakeWorkspaceClient([membership_response([("vendas", True)])])

    resolve_membership(client, settings, ["vendas"])

    call = client.statement_execution.calls[0]
    assert "vendas" not in call["statement"], "group name must not appear in the SQL text"
    assert [(p.name, p.value) for p in call["parameters"]] == [("g0", "vendas")]


def test_membership_runs_with_the_users_client(settings):
    """Resolving membership as the service principal would report the wrong user."""
    user_client = FakeWorkspaceClient([membership_response([("vendas", True)])])

    resolve_membership(user_client, settings, ["vendas"])

    assert len(user_client.statement_execution.calls) == 1


def test_empty_group_list_makes_no_call(settings):
    client = FakeWorkspaceClient([membership_response([])])
    assert resolve_membership(client, settings, []) == set()
    assert client.statement_execution.calls == []


def test_resolver_caches_across_calls(settings):
    client = FakeWorkspaceClient([membership_response([("vendas", True), ("rh", False)])])
    resolver = GroupResolver(client, settings)

    first = resolver.membership(["vendas", "rh"])
    second = resolver.membership(["vendas", "rh"])

    assert first == second == {"vendas"}
    assert len(client.statement_execution.calls) == 1, "second lookup must hit the cache"


def test_group_membership_statement_shape():
    statement = sql.group_membership(2)
    assert statement.count("is_account_group_member") == 2
    assert ":g0" in statement and ":g1" in statement
    assert "UNION ALL" in statement


def test_group_membership_requires_at_least_one_group():
    with pytest.raises(ValueError):
        sql.group_membership(0)
