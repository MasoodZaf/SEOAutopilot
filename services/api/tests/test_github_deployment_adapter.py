"""The real GitHub adapter, driven against a fake GitHub.

The fake is a small state machine rather than a set of canned replies, so the
adapter has to do the things a repository actually requires -- read the file
before writing it, pass the blob sha on an update, create the branch before
committing to it -- instead of merely calling the right endpoints in the right
order. Every request is recorded so the tests can assert what was *not* done,
which for a deployment adapter is the more important half.

None of this reaches the network. What it cannot prove is that GitHub behaves as
modelled here; only a run against a real repository closes that, and that is
recorded in H3 as still outstanding.
"""

import base64
import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.domain.deployments import (
    DeploymentManifest,
    DeploymentRequest,
    DriftDetectedError,
    RollbackRequest,
)
from app.domain.github_adapter import (
    GitHubDeploymentAdapter,
    GitHubDeploymentError,
    GitHubTarget,
    branch_name,
    pull_request_body,
)
from app.domain.proposals import compute_content_hash

pytestmark = pytest.mark.asyncio

TARGET = GitHubTarget(owner="oryxen", repository="codearc", base_branch="main")
LIVE = "<meta name='description' content='the old one'>"
NEW = "<meta name='description' content='the new one'>"
PROPOSAL_ID = UUID("019d0000-0000-7000-8000-0000000000a1")


def make_request(*, base_hash: str | None = None, key: str = "deploy-key-0001") -> DeploymentRequest:
    manifest = DeploymentManifest(
        tenant_id=str(uuid4()),
        site_id=str(uuid4()),
        proposal_id=str(PROPOSAL_ID),
        target_path="src/pages/index.html",
        base_hash=base_hash or compute_content_hash(LIVE),
        proposal_hash=compute_content_hash(NEW),
        author_id=str(uuid4()),
        approver_ids=[str(uuid4()), str(uuid4())],
        deployed_at="2026-09-06T00:00:00+00:00",
    )
    return DeploymentRequest(
        tenant_id=UUID(manifest.tenant_id),
        site_id=UUID(manifest.site_id),
        proposal_id=PROPOSAL_ID,
        target_type="github_file",
        target_path=manifest.target_path,
        diff_unified="--- a\n+++ b\n",
        base_hash=manifest.base_hash,
        after_content=NEW,
        idempotency_key=key,
        manifest=manifest,
    )


class FakeGitHub:
    """Enough of a repository to make the adapter earn its result."""

    def __init__(self, *, file_content: str | None = LIVE, fail: dict[str, int] | None = None):
        self.file_content = file_content
        self.file_sha = "blob-sha-1"
        self.branches: dict[str, str] = {"main": "base-sha-1"}
        self.pulls: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []
        self.commits: list[dict[str, Any]] = []
        self.fail = fail or {}
        self.next_pull_number = 41
        self.closed: list[int] = []
        self.deleted: list[dict[str, Any]] = []

    def merge(self, number: int) -> None:
        """Mark a pull request merged, as a human clicking the button would."""
        for pull in self.pulls:
            if pull["number"] == number:
                pull["merged_at"] = "2026-09-06T00:00:00Z"
                pull["state"] = "closed"

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        for fragment, status in self.fail.items():
            if fragment in path:
                return httpx.Response(status, json={"message": "forced"})

        if request.method == "GET" and "/pulls/" in path:
            number = int(path.rsplit("/", 1)[-1])
            for pull in self.pulls:
                if pull["number"] == number:
                    return httpx.Response(200, json=pull)
            return httpx.Response(404, json={"message": "Not Found"})

        if request.method == "PATCH" and "/pulls/" in path:
            number = int(path.rsplit("/", 1)[-1])
            body = json.loads(request.content)
            if body.get("state") == "closed":
                self.closed.append(number)
                for pull in self.pulls:
                    if pull["number"] == number:
                        pull["state"] = "closed"
            return httpx.Response(200, json={"number": number, "state": "closed"})

        if request.method == "GET" and path.endswith("/pulls"):
            head = request.url.params.get("head", "")
            branch = head.split(":", 1)[-1]
            matching = [pull for pull in self.pulls if pull["head_branch"] == branch]
            return httpx.Response(200, json=matching[:1])

        if request.method == "GET" and "/contents/" in path:
            if self.file_content is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(self.file_content.encode()).decode(),
                    "sha": self.file_sha,
                },
            )

        if request.method == "GET" and "/git/ref/heads/" in path:
            branch = path.rsplit("/", 1)[-1]
            if branch not in self.branches:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"object": {"sha": self.branches[branch]}})

        if request.method == "POST" and path.endswith("/git/refs"):
            body = json.loads(request.content)
            branch = body["ref"].removeprefix("refs/heads/")
            if branch in self.branches:
                return httpx.Response(422, json={"message": "Reference already exists"})
            self.branches[branch] = body["sha"]
            return httpx.Response(201, json={"ref": body["ref"]})

        if request.method == "PUT" and "/contents/" in path:
            body = json.loads(request.content)
            if body.get("branch") not in self.branches:
                return httpx.Response(404, json={"message": "Branch not found"})
            if self.file_content is not None and body.get("sha") != self.file_sha:
                # GitHub refuses an update that does not name the blob it replaces.
                return httpx.Response(409, json={"message": "sha does not match"})
            self.commits.append(body)
            return httpx.Response(200, json={"commit": {"sha": "commit-sha-1"}})

        if request.method == "DELETE" and "/contents/" in path:
            body = json.loads(request.content)
            if body.get("branch") not in self.branches or body.get("sha") != self.file_sha:
                return httpx.Response(409, json={"message": "sha does not match"})
            self.deleted.append({"path": path, **body})
            return httpx.Response(200, json={"commit": {"sha": "commit-sha-2"}})

        if request.method == "POST" and path.endswith("/pulls"):
            body = json.loads(request.content)
            if any(pull["head_branch"] == body["head"] for pull in self.pulls):
                return httpx.Response(422, json={"message": "A pull request already exists"})
            self.next_pull_number += 1
            pull = {
                "number": self.next_pull_number,
                "state": "open",
                "html_url": f"https://github.com/{TARGET.slug}/pull/{self.next_pull_number}",
                "head_branch": body["head"],
                "body": body["body"],
                "title": body["title"],
            }
            self.pulls.append(pull)
            return httpx.Response(201, json=pull)

        return httpx.Response(500, json={"message": f"unhandled {request.method} {path}"})


def adapter_for(fake: FakeGitHub) -> tuple[GitHubDeploymentAdapter, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=fake.transport())
    return GitHubDeploymentAdapter(client, TARGET, "token-value"), client


async def test_an_approved_proposal_becomes_one_pull_request() -> None:
    fake = FakeGitHub()
    adapter, client = adapter_for(fake)
    async with client:
        result = await adapter.deploy(make_request())

    assert result.status == "applied"
    assert result.connector_type == "github"
    assert result.external_ref.startswith(f"https://github.com/{TARGET.slug}/pull/")
    assert len(fake.pulls) == 1
    assert len(fake.commits) == 1

    # The commit must carry the new content, not the diff or the old content.
    written = base64.b64decode(fake.commits[0]["content"]).decode()
    assert written == NEW

    # The receipt has to identify the branch, or a failed deploy leaves nothing
    # to look at.
    assert result.manifest_json["head_branch"] == branch_name(make_request())
    assert result.manifest_json["repository"] == TARGET.slug
    assert result.manifest_json["pull_request_number"] == 42


async def test_the_same_idempotency_key_opens_no_second_pull_request() -> None:
    """QA scenario 11, against a service that would happily open two."""
    fake = FakeGitHub()
    adapter, client = adapter_for(fake)
    async with client:
        first = await adapter.deploy(make_request())
        after_first = len(fake.calls)
        second = await adapter.deploy(make_request())

    assert first.external_ref == second.external_ref
    assert len(fake.pulls) == 1
    assert len(fake.commits) == 1, "the retry wrote a second commit"

    # The retry must not have attempted a write at all: one lookup, no more.
    retry_calls = fake.calls[after_first:]
    assert [method for method, _ in retry_calls] == ["GET"]


async def test_a_different_key_opens_a_separate_pull_request() -> None:
    """The guard must not collapse two genuinely different deployments."""
    fake = FakeGitHub()
    adapter, client = adapter_for(fake)
    async with client:
        first = await adapter.deploy(make_request(key="deploy-key-0001"))
        fake.file_sha = "blob-sha-2"  # the first commit moved the file
        fake.file_content = LIVE  # still the base content on main
        second = await adapter.deploy(make_request(key="deploy-key-0002"))

    assert first.external_ref != second.external_ref
    assert len(fake.pulls) == 2


async def test_drift_is_refused_before_anything_is_written() -> None:
    """QA scenario 12. The file moved under the proposal."""
    fake = FakeGitHub(file_content="<meta name='description' content='someone else edited'>")
    adapter, client = adapter_for(fake)
    async with client:
        with pytest.raises(DriftDetectedError, match="Drift detected"):
            await adapter.deploy(make_request())

    assert fake.commits == []
    assert fake.pulls == []
    assert not any(method in {"PUT", "POST"} for method, _ in fake.calls)


async def test_a_missing_file_is_drift_unless_the_proposal_expected_it() -> None:
    """A proposal against a file that is gone must not silently create it."""
    fake = FakeGitHub(file_content=None)
    adapter, client = adapter_for(fake)
    async with client:
        with pytest.raises(DriftDetectedError):
            await adapter.deploy(make_request())
    assert fake.commits == []


async def test_a_proposal_that_creates_a_file_is_allowed() -> None:
    """A proposal whose base is empty is a create, and the hash says so."""
    fake = FakeGitHub(file_content=None)
    adapter, client = adapter_for(fake)
    async with client:
        result = await adapter.deploy(make_request(base_hash=compute_content_hash("")))
    assert result.status == "applied"
    assert len(fake.commits) == 1
    assert "sha" not in fake.commits[0], "a create must not claim to replace a blob"


async def test_a_commit_failure_names_the_branch_it_left_behind() -> None:
    """QA scenario 13: a half-finished deployment has to be traceable."""
    fake = FakeGitHub(fail={"/contents/src": 403})
    adapter, client = adapter_for(fake)
    async with client:
        with pytest.raises(GitHubDeploymentError) as error:
            await adapter.deploy(make_request())
    assert "github_contents_read_failed:403" in str(error.value)


async def test_a_pull_request_race_returns_the_other_callers_pull_request() -> None:
    """Two deploys of one key that overlap must converge on one pull request."""
    fake = FakeGitHub()
    adapter, client = adapter_for(fake)
    request = make_request()

    async with client:
        # Simulate the branch and pull request appearing between this adapter's
        # lookup and its own create, which is what a concurrent caller does.
        original_handle = fake._handle

        def racing(http_request: httpx.Request) -> httpx.Response:
            racing_create = (
                http_request.method == "POST"
                and http_request.url.path.endswith("/pulls")
            )
            if racing_create and not fake.pulls:
                    fake.pulls.append(
                        {
                            "number": 99,
                            "state": "open",
                            "html_url": f"https://github.com/{TARGET.slug}/pull/99",
                            "head_branch": branch_name(request),
                            "body": "",
                            "title": "",
                        }
                    )
            return original_handle(http_request)

        client._transport = httpx.MockTransport(racing)
        result = await adapter.deploy(request)

    assert result.external_ref.endswith("/pull/99")
    assert len(fake.pulls) == 1


async def test_a_transport_failure_is_named_not_leaked() -> None:
    """A raw httpx error would reach the caller as a 500 with a stack trace."""

    def explode(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    client = httpx.AsyncClient(transport=httpx.MockTransport(explode))
    adapter = GitHubDeploymentAdapter(client, TARGET, "token-value")
    async with client:
        with pytest.raises(GitHubDeploymentError, match="github_unavailable"):
            await adapter.deploy(make_request())


async def test_the_token_is_sent_and_never_placed_in_the_url() -> None:
    """A token in a query string ends up in logs, proxies and referrers."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    adapter = GitHubDeploymentAdapter(client, TARGET, "token-value")
    async with client:
        with pytest.raises(Exception):  # noqa: B017 - the fake answers only the first call
            await adapter.deploy(make_request())

    assert seen
    assert seen[0].headers["Authorization"] == "Bearer token-value"
    assert "token-value" not in str(seen[0].url)


async def test_the_pull_request_body_carries_the_approval_trail() -> None:
    """A reviewer must be able to judge the change without opening the tool."""
    request = make_request()
    body = pull_request_body(request)
    for approver in request.manifest.approver_ids:
        assert approver in body
    assert request.manifest.base_hash in body
    assert request.manifest.author_id in body
    assert request.target_path in body


def rollback_request(pull_number: int | None = 42, notes: str = "") -> RollbackRequest:
    request = make_request()
    manifest: dict[str, Any] = dict(request.manifest.to_dict())
    if pull_number is not None:
        manifest["pull_request_number"] = pull_number
    return RollbackRequest(
        tenant_id=request.tenant_id,
        site_id=request.site_id,
        proposal_id=request.proposal_id,
        target_path=request.target_path,
        before_content=LIVE,
        deployed_hash=compute_content_hash(NEW),
        external_ref=f"https://github.com/{TARGET.slug}/pull/{pull_number}",
        manifest_json=manifest,
        notes=notes,
    )


async def test_rolling_back_an_open_pull_request_just_closes_it() -> None:
    """Nothing reached the base branch, so closing it undoes everything."""
    fake = FakeGitHub()
    adapter, client = adapter_for(fake)
    async with client:
        deployed = await adapter.deploy(make_request())
        number = deployed.manifest_json["pull_request_number"]
        result = await adapter.rollback(rollback_request(number))

    # Applied, and it is the only rollback shape that has earned the word: the
    # adapter itself put the site back, with nobody left to act.
    assert result.status == "applied"
    assert result.detail == "pull_request_closed_before_merge"
    assert result.restored_hash == compute_content_hash(LIVE)
    # Closing is enough; a revert branch would be noise on an unmerged change.
    assert len(fake.pulls) == 1
    assert len(fake.commits) == 1
    assert fake.closed == [number]


async def test_rolling_back_a_merged_pull_request_opens_a_revert() -> None:
    """The change is in the base branch, so undoing it takes another commit."""
    fake = FakeGitHub()
    adapter, client = adapter_for(fake)
    async with client:
        deployed = await adapter.deploy(make_request())
        number = deployed.manifest_json["pull_request_number"]
        fake.merge(number)
        result = await adapter.rollback(rollback_request(number, notes="canary failed"))

    # Pending, not applied. The deployed change is still in the base branch and
    # stays there until a person merges this. Calling it applied is how a
    # deployment receipt came to read `rolled_back` for a change still live on
    # the pilot site, next to a revert pull request that was closed unmerged.
    assert result.status == "pending"
    assert result.detail == "revert_pull_request_opened_not_merged"
    assert result.restored_hash == compute_content_hash(LIVE)
    assert len(fake.pulls) == 2

    revert = fake.pulls[-1]
    assert revert["title"].startswith("Revert SEO:")
    # The reviewer has to know the undo is not in force yet.
    assert "not merged" in revert["body"].lower()
    assert "canary failed" in revert["body"]

    # And the revert commit restores the old content, not the new.
    restored = base64.b64decode(fake.commits[-1]["content"]).decode()
    assert restored == LIVE


async def test_the_adapter_never_merges_anything() -> None:
    """Rollback is not the moment to grant merge authority."""
    fake = FakeGitHub()
    adapter, client = adapter_for(fake)
    async with client:
        deployed = await adapter.deploy(make_request())
        number = deployed.manifest_json["pull_request_number"]
        fake.merge(number)
        await adapter.rollback(rollback_request(number))

    assert not any(path.endswith("/merge") for _, path in fake.calls)
    assert not any(method == "DELETE" for method, _ in fake.calls)


async def test_rolling_back_twice_reuses_the_open_revert() -> None:
    fake = FakeGitHub()
    adapter, client = adapter_for(fake)
    async with client:
        deployed = await adapter.deploy(make_request())
        number = deployed.manifest_json["pull_request_number"]
        fake.merge(number)
        first = await adapter.rollback(rollback_request(number))
        second = await adapter.rollback(rollback_request(number))

    assert first.external_ref == second.external_ref
    assert second.detail == "revert_pull_request_already_open"
    # Asking twice does not merge anything either.
    assert (first.status, second.status) == ("pending", "pending")
    assert len(fake.pulls) == 2


async def test_a_receipt_without_a_pull_request_number_cannot_be_rolled_back() -> None:
    """Better to refuse than to guess which pull request to undo."""
    fake = FakeGitHub()
    adapter, client = adapter_for(fake)
    async with client:
        with pytest.raises(GitHubDeploymentError, match="github_rollback_no_pull_request"):
            await adapter.rollback(rollback_request(pull_number=None))
    assert fake.calls == []


def new_post_rollback(pull_number: int) -> RollbackRequest:
    request = rollback_request(pull_number)
    return RollbackRequest(
        tenant_id=request.tenant_id,
        site_id=request.site_id,
        proposal_id=request.proposal_id,
        target_path=request.target_path,
        before_content="",
        deployed_hash=compute_content_hash(NEW),
        external_ref=request.external_ref,
        manifest_json=request.manifest_json,
        notes="",
    )


async def test_reverting_a_merged_new_post_deletes_the_file() -> None:
    """A proposal that created a file is undone by removing it.

    Writing the recorded empty `before_content` back instead would publish a
    blank page at the post's address.
    """
    fake = FakeGitHub(file_content=None)
    adapter, client = adapter_for(fake)
    async with client:
        deployed = await adapter.deploy(make_request(base_hash=compute_content_hash("")))
        number = deployed.manifest_json["pull_request_number"]
        fake.merge(number)
        fake.file_content = NEW
        result = await adapter.rollback(new_post_rollback(number))

    assert result.status == "pending"
    assert len(fake.deleted) == 1
    assert fake.deleted[0]["sha"] == fake.file_sha
    # Only the original deployment wrote content; the revert wrote none.
    assert len(fake.commits) == 1
    assert fake.pulls[-1]["title"].startswith("Revert SEO:")


async def test_reverting_a_new_post_whose_file_is_gone_needs_no_pull_request() -> None:
    fake = FakeGitHub(file_content=None)
    adapter, client = adapter_for(fake)
    async with client:
        deployed = await adapter.deploy(make_request(base_hash=compute_content_hash("")))
        number = deployed.manifest_json["pull_request_number"]
        fake.merge(number)
        result = await adapter.rollback(new_post_rollback(number))

    assert result.status == "applied"
    assert result.detail == "file_already_absent"
    assert fake.deleted == []
    assert len(fake.pulls) == 1
