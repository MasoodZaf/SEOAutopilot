"""What happens to the records once somebody acts on a revert pull request.

Rolling back a merged deployment opens a revert and stops; the deployed content
stays live until a person merges. Migration 0033 gave that state its own name
after a receipt claimed an undo that had not happened. What was still missing is
the other end: nothing found out what became of the revert, so a rollback stayed
`rollback_pending` for ever -- the pilot's emi-calculator revert was closed
unmerged and the records still said an undo was in progress.

Three outcomes mean three different things about the live site, so all three are
pinned here against PostgreSQL rather than a mocked session: the transition
touches three tables and its correctness is exactly whether they end up agreeing.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.domain.github_adapter import GitHubTarget
from app.services.github_connector import GitHubCredential
from app.services.rollback_reconciliation import (
    PendingRollback,
    RollbackReconciler,
    parse_pull_request,
    pending_rollbacks,
    reconcile_once,
)
from tests.conftest import requires_database

pytestmark = [pytest.mark.asyncio, requires_database]

PULL_URL = "https://github.com/MasoodZaf/mindTools/pull/7"
SCORING_VERSION_ID = UUID("019d0000-0000-7000-8000-000000000090")

CREDENTIAL = GitHubCredential(
    target=GitHubTarget(owner="MasoodZaf", repository="mindTools", base_branch="main"),
    token="resolved-for-this-site",
    path_template="{path}.html",
    connector_id=UUID("019d0000-0000-7000-8000-0000000000c1"),
    source="github_app",
)


def settings() -> Settings:
    return Settings(_env_file=None, cursor_signing_key="c" * 32)  # pyright: ignore[reportCallIssue]


def github(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def pull(**overrides) -> httpx.Response:
    body = {"state": "open", "merged_at": None, "html_url": PULL_URL}
    body.update(overrides)
    return httpx.Response(200, json=body)


@pytest.fixture
def scoped(app_engine):
    """A committing tenant scope, the shape background work actually uses.

    The shared `tenant_session_factory` rolls back so cases cannot leak into
    each other. That is wrong here: the reconciler's whole job is to leave three
    tables agreeing afterwards, and a transaction that never commits cannot show
    that. The fixture teardown deletes the tenant instead.
    """
    factory = async_sessionmaker(app_engine, expire_on_commit=False)

    def build(tenant_id: UUID):
        return _Scope(factory, tenant_id)

    return build


class _Scope:
    def __init__(self, factory, tenant_id: UUID) -> None:
        self._factory = factory
        self._tenant_id = tenant_id

    async def __aenter__(self):
        self._session = self._factory()
        await self._session.__aenter__()
        self._transaction = self._session.begin()
        await self._transaction.__aenter__()
        await self._session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(self._tenant_id)},
        )
        return self._session

    async def __aexit__(self, *exc):
        await self._transaction.__aexit__(*exc)
        await self._session.__aexit__(*exc)


@pytest_asyncio.fixture
async def deployed_and_reverting(engine):
    """A tenant whose merged deployment has an open, unresolved revert.

    Committed, because the sweep reads across tenants on one connection and then
    writes on another; rows staged inside a single transaction would be
    invisible to the half that has to find them.
    """
    ids = {
        name: uuid4()
        for name in (
            "tenant_id", "site_id", "page_id", "opportunity_id",
            "proposal_id", "receipt_id", "rollback_id", "author_id",
        )
    }
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO tenant(id,slug,name,status)"
                " VALUES(:id,:slug,'reconcile','active')"
            ),
            {"id": ids["tenant_id"], "slug": f"reconcile-{ids['tenant_id'].hex[:8]}"},
        )
        await session.execute(
            text(
                "INSERT INTO site(id,tenant_id,name,canonical_origin,normalized_host,"
                "mode,status,verified_at)"
                " VALUES(:id,:tenant_id,'TheCalcHive','https://thecalchive.com',:host,"
                "'recommend','active',now())"
            ),
            {
                "id": ids["site_id"],
                "tenant_id": ids["tenant_id"],
                "host": f"{ids['site_id'].hex[:8]}.example.com",
            },
        )
        await session.execute(
            text(
                "INSERT INTO page(id,tenant_id,site_id,normalized_url,url_hash)"
                " VALUES(:id,:tenant_id,:site_id,:url,:url_hash)"
            ),
            {
                "id": ids["page_id"],
                "tenant_id": ids["tenant_id"],
                "site_id": ids["site_id"],
                "url": "https://thecalchive.com/emi-calculator",
                "url_hash": hashlib.sha256(b"https://thecalchive.com/emi-calculator").hexdigest(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO opportunity(id,tenant_id,site_id,page_id,title,impact,confidence,"
                "urgency,effort,risk,score,scoring_version_id,evidence_refs,fingerprint)"
                " VALUES(:id,:tenant_id,:site_id,:page_id,'title',0.5,0.5,0.5,0.5,'low',50,"
                ":scoring_version_id,'{}'::jsonb,:fingerprint)"
            ),
            {
                "id": ids["opportunity_id"],
                "tenant_id": ids["tenant_id"],
                "site_id": ids["site_id"],
                "page_id": ids["page_id"],
                "scoring_version_id": SCORING_VERSION_ID,
                "fingerprint": hashlib.sha256(ids["opportunity_id"].bytes).hexdigest(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO proposal(id,tenant_id,site_id,opportunity_id,page_id,author_id,"
                "title,rationale,target_type,target_path,before_content,after_content,"
                "diff_unified,base_hash,proposal_hash,risk,status,expires_at,version)"
                " VALUES(:id,:tenant_id,:site_id,:opportunity_id,:page_id,:author_id,"
                "'Name the calculator','the title omits it','github_file',"
                "'CalcHive/emi-calculator.html',:before,:after,'--- a\n+++ b\n',"
                ":base_hash,:proposal_hash,'low','deployed',:expires_at,1)"
            ),
            {
                "id": ids["proposal_id"],
                "tenant_id": ids["tenant_id"],
                "site_id": ids["site_id"],
                "opportunity_id": ids["opportunity_id"],
                "page_id": ids["page_id"],
                "author_id": ids["author_id"],
                "before": "<title>EMI</title>",
                "after": "<title>EMI Calculator</title>",
                "base_hash": "c" * 64,
                "proposal_hash": "a" * 64,
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
        await session.execute(
            text(
                "INSERT INTO deployment_receipt(id,tenant_id,site_id,proposal_id,connector_type,"
                "idempotency_key,external_ref,manifest_json,status)"
                " VALUES(:id,:tenant_id,:site_id,:proposal_id,'github',:key,:ref,"
                "'{\"pull_request_number\": 5}'::jsonb,'rollback_pending')"
            ),
            {
                "id": ids["receipt_id"],
                "tenant_id": ids["tenant_id"],
                "site_id": ids["site_id"],
                "proposal_id": ids["proposal_id"],
                "key": f"key-{ids['receipt_id'].hex[:12]}",
                "ref": "https://github.com/MasoodZaf/mindTools/pull/5",
            },
        )
        await session.execute(
            text(
                "INSERT INTO rollback_receipt(id,tenant_id,site_id,proposal_id,"
                "deployment_receipt_id,restored_hash,status,external_ref,notes)"
                " VALUES(:id,:tenant_id,:site_id,:proposal_id,:receipt_id,:hash,'pending',"
                ":ref,'revert_pull_request_opened_not_merged')"
            ),
            {
                "id": ids["rollback_id"],
                "tenant_id": ids["tenant_id"],
                "site_id": ids["site_id"],
                "proposal_id": ids["proposal_id"],
                "receipt_id": ids["receipt_id"],
                "hash": "b" * 64,
                "ref": PULL_URL,
            },
        )
    yield PendingRollback(
        tenant_id=ids["tenant_id"],
        rollback_id=ids["rollback_id"],
        site_id=ids["site_id"],
        proposal_id=ids["proposal_id"],
        deployment_receipt_id=ids["receipt_id"],
        external_ref=PULL_URL,
    )
    async with factory() as session, session.begin():
        for table in (
            "audit_event",
            "outbox_event",
            "rollback_receipt",
            "deployment_receipt",
            "proposal",
            "opportunity",
            "page",
            "site",
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :id"),
                {"id": ids["tenant_id"]},
            )
        await session.execute(
            text("DELETE FROM tenant WHERE id = :id"), {"id": ids["tenant_id"]}
        )


async def states(engine, pending: PendingRollback) -> tuple[str, str, str]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        rollback = await session.scalar(
            text("SELECT status FROM rollback_receipt WHERE id = :id"),
            {"id": pending.rollback_id},
        )
        receipt = await session.scalar(
            text("SELECT status FROM deployment_receipt WHERE id = :id"),
            {"id": pending.deployment_receipt_id},
        )
        proposal = await session.scalar(
            text("SELECT status FROM proposal WHERE id = :id"), {"id": pending.proposal_id}
        )
    return rollback, receipt, proposal


async def run(monkeypatch, scoped, pending, handler) -> str:
    monkeypatch.setattr(
        "app.services.rollback_reconciliation.credential_for_site",
        lambda *args, **kwargs: _credential(),
    )
    async with github(handler) as client, scoped(pending.tenant_id) as session:
        return await RollbackReconciler(session, settings(), client).reconcile(pending)


async def _credential() -> GitHubCredential:
    return CREDENTIAL


def test_a_pull_request_url_yields_its_repository_and_number() -> None:
    """Read from the URL, not the connector.

    The revert lives wherever it was opened. A site whose connector is later
    repointed must not make this poll ask a different repository about that
    number and act on the answer.
    """
    assert parse_pull_request(PULL_URL) == ("MasoodZaf/mindTools", 7)
    assert parse_pull_request(f"{PULL_URL}/") == ("MasoodZaf/mindTools", 7)
    for bad in ("", "not-a-url", "https://github.com/owner/repo/issues/7",
                "https://evil.example.com/MasoodZaf/mindTools/pull/7"):
        assert parse_pull_request(bad) is None


async def test_a_merged_revert_is_the_only_thing_that_undoes_a_change(
    engine, monkeypatch, scoped, deployed_and_reverting
) -> None:
    pending = deployed_and_reverting
    outcome = await run(
        monkeypatch,
        scoped,
        pending,
        lambda request: pull(state="closed", merged_at="2026-09-07T10:00:00Z"),
    )

    assert outcome == "merged"
    assert await states(engine, pending) == ("applied", "rolled_back", "failed")


async def test_a_revert_closed_unmerged_leaves_the_change_live(
    engine, monkeypatch, scoped, deployed_and_reverting
) -> None:
    """The exact state the pilot has been sitting in since 2026-09-06.

    Somebody looked at the revert and decided against it. Nothing was undone, so
    the deployment goes back to `applied` -- it is live and no undo is
    outstanding -- the proposal stays deployed, and the rollback is the thing
    that failed.
    """
    pending = deployed_and_reverting
    outcome = await run(
        monkeypatch, scoped, pending, lambda request: pull(state="closed")
    )

    assert outcome == "abandoned"
    assert await states(engine, pending) == ("failed", "applied", "deployed")


async def test_an_open_revert_is_recorded_as_checked_not_as_resolved(
    engine, monkeypatch, scoped, deployed_and_reverting
) -> None:
    pending = deployed_and_reverting
    outcome = await run(monkeypatch, scoped, pending, lambda request: pull())

    assert outcome == "still_open"
    assert await states(engine, pending) == ("pending", "rollback_pending", "deployed")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        reconciled_at = await session.scalar(
            text("SELECT reconciled_at FROM rollback_receipt WHERE id = :id"),
            {"id": pending.rollback_id},
        )
    # "Nothing has looked" and "looked, still open" have to be distinguishable,
    # or the reconciler's own silence is indistinguishable from its absence.
    assert isinstance(reconciled_at, datetime)


async def test_github_being_unreadable_never_closes_a_rollback_out(
    engine, monkeypatch, scoped, deployed_and_reverting
) -> None:
    """A transient failure must not be able to decide a governance outcome."""
    pending = deployed_and_reverting
    outcome = await run(
        monkeypatch,
        scoped,
        pending,
        lambda request: httpx.Response(403, json={"message": "rate limited"}),
    )

    assert outcome == "unresolved"
    assert await states(engine, pending) == ("pending", "rollback_pending", "deployed")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        error = await session.scalar(
            text("SELECT reconcile_error FROM rollback_receipt WHERE id = :id"),
            {"id": pending.rollback_id},
        )
    assert error == "github_pull_read_failed:403"


async def test_a_merged_revert_records_who_observed_it(
    engine, monkeypatch, scoped, deployed_and_reverting
) -> None:
    """No person merged this here; a poll saw that one had.

    An audit row claiming a user did it would put a name on a machine's
    observation, which is exactly the kind of thing the audit trail exists to
    stop somebody having to guess about later.
    """
    pending = deployed_and_reverting
    await run(
        monkeypatch,
        scoped,
        pending,
        lambda request: pull(state="closed", merged_at="2026-09-07T10:00:00Z"),
    )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        row = (
            await session.execute(
                text(
                    "SELECT actor_type, actor_id, action, metadata->>'change_reversed'"
                    " FROM audit_event WHERE tenant_id = :id"
                ),
                {"id": pending.tenant_id},
            )
        ).one()
    assert row == ("system", "reconciler", "proposal.rolled_back", "true")


async def test_the_sweep_finds_work_across_tenants_and_settles_it_in_one(
    engine, app_engine, monkeypatch, scoped, deployed_and_reverting
) -> None:
    """The two halves use two identities, and both are exercised here.

    Finding work spans tenants and cannot be tenant scoped. Settling it is a
    write to one tenant's records and must be.
    """
    pending = deployed_and_reverting
    monkeypatch.setattr(
        "app.services.rollback_reconciliation.credential_for_site",
        lambda *args, **kwargs: _credential(),
    )
    async with engine.connect() as relay:
        found = await pending_rollbacks(relay)
        assert any(item.rollback_id == pending.rollback_id for item in found)

        async with github(
            lambda request: pull(state="closed", merged_at="2026-09-07T10:00:00Z")
        ) as client:
            report = await reconcile_once(relay, scoped, settings(), client)

    assert report.checked >= 1
    assert report.merged >= 1
    assert await states(engine, pending) == ("applied", "rolled_back", "failed")
