from uuid import UUID

from app.core.context import Role
from app.services.keywords import READ_TERM_ROLES, query_aad

TENANT = UUID("019d0000-0000-7000-8000-000000000011")
SITE = UUID("019d0000-0000-7000-8000-000000000012")
OTHER_SITE = UUID("019d0000-0000-7000-8000-000000000013")


def test_query_aad_matches_the_worker_formula() -> None:
    """The worker seals with this exact string; a drift here silently breaks reads."""
    assert query_aad(TENANT, SITE, "local-v1") == (
        f"{TENANT}:{SITE}:search_query:local-v1".encode()
    )


def test_query_aad_is_bound_to_tenant_site_and_key_version() -> None:
    baseline = query_aad(TENANT, SITE, "local-v1")
    assert baseline != query_aad(TENANT, OTHER_SITE, "local-v1")
    assert baseline != query_aad(SITE, SITE, "local-v1")
    assert baseline != query_aad(TENANT, SITE, "local-v2")


def test_raw_terms_are_limited_to_roles_that_act_on_them() -> None:
    assert Role.VIEWER not in READ_TERM_ROLES
    assert Role.DEVELOPER not in READ_TERM_ROLES
    assert {Role.OWNER, Role.ADMIN, Role.SEO_MANAGER, Role.EDITOR} == READ_TERM_ROLES


def test_cluster_read_model_carries_no_query_term() -> None:
    from app.api.schemas import KeywordClusterRead

    assert "term" not in KeywordClusterRead.model_fields
    assert "query_hash" not in KeywordClusterRead.model_fields
