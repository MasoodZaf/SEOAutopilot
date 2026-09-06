"""Deploying several approved changes as one reviewable pull request.

Thirty single-file pull requests describe the same work as one touching thirty
files, and only the second can be read. What must not travel with the
convenience is authority: the batch shares a branch and a review, not an
approval.

These exercise the adapter against a fake GitHub. The service-level gates are
covered against real PostgreSQL in `integration/test_deployment_gate.py`.
"""

import base64
import json
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.domain.deployments import (
    BatchDeploymentRequest,
    DeploymentManifest,
    DeploymentRequest,
    DriftDetectedError,
)
from app.domain.github_adapter import (
    GitHubDeploymentAdapter,
    GitHubDeploymentError,
    GitHubTarget,
    batch_branch_name,
    batch_pull_request_body,
)
from app.domain.proposals import compute_content_hash

pytestmark = pytest.mark.asyncio

TARGET = GitHubTarget(owner="acme", repository="site", base_branch="main")
SITE = uuid4()
TENANT = uuid4()


class FakeGitHub:
    """A repository that refuses what real GitHub refuses."""

    def __init__(self, files: dict[str, str], *, fail: dict[str, int] | None = None):
        self.files = dict(files)
        self.branches = {"main": "base-sha"}
        self.pulls: list[dict[str, Any]] = []
        self.writes: list[tuple[str, str]] = []
        self.fail = fail or {}
        self.next_number = 7

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        for fragment, code in self.fail.items():
            if fragment in path and method in {"POST", "PUT"}:
                return httpx.Response(code, json={"message": "forced"})

        if method == "GET" and path.endswith("/pulls"):
            head = request.url.params.get("head", "")
            matching = [p for p in self.pulls if head.endswith(p["head"]["ref"])]
            return httpx.Response(200, json=matching)
        if method == "GET" and "/contents/" in path:
            name = path.split("/contents/", 1)[1]
            if name not in self.files:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={
                "encoding": "base64",
                "content": base64.b64encode(self.files[name].encode()).decode(),
                "sha": f"blob-{name}",
            })
        if method == "GET" and "/git/ref/heads/" in path:
            return httpx.Response(200, json={"object": {"sha": self.branches["main"]}})
        if method == "POST" and path.endswith("/git/refs"):
            body = json.loads(request.content)
            ref = body["ref"].removeprefix("refs/heads/")
            if ref in self.branches:
                return httpx.Response(422, json={"message": "Reference already exists"})
            self.branches[ref] = body["sha"]
            return httpx.Response(201, json={})
        if method == "PUT" and "/contents/" in path:
            name = path.split("/contents/", 1)[1]
            body = json.loads(request.content)
            if body["branch"] not in self.branches:
                return httpx.Response(422, json={"message": "Branch does not exist"})
            if name in self.files and body.get("sha") != f"blob-{name}":
                return httpx.Response(409, json={"message": "sha mismatch"})
            self.files[name] = base64.b64decode(body["content"]).decode()
            self.writes.append((body["branch"], name))
            return httpx.Response(200, json={"commit": {"sha": "c"}})
        if method == "POST" and path.endswith("/pulls"):
            body = json.loads(request.content)
            if any(p["head"]["ref"] == body["head"] for p in self.pulls):
                return httpx.Response(422, json={"message": "already exists"})
            pull = {
                "number": self.next_number,
                "html_url": f"https://github.com/acme/site/pull/{self.next_number}",
                "head": {"ref": body["head"]}, "title": body["title"], "body": body["body"],
                "state": "open", "merged_at": None,
            }
            self.next_number += 1
            self.pulls.append(pull)
            return httpx.Response(201, json=pull)
        raise AssertionError(f"unexpected {method} {path}")


def change(path: str, before: str, after: str) -> DeploymentRequest:
    proposal_id = uuid4()
    return DeploymentRequest(
        tenant_id=TENANT, site_id=SITE, proposal_id=proposal_id,
        target_type="github_file", target_path=path, diff_unified=f"-{before}\n+{after}",
        base_hash=compute_content_hash(before), after_content=after,
        idempotency_key="batch-key-0001",
        manifest=DeploymentManifest(
            tenant_id=str(TENANT), site_id=str(SITE), proposal_id=str(proposal_id),
            target_path=path, base_hash=compute_content_hash(before),
            proposal_hash=compute_content_hash(after), author_id=str(uuid4()),
            approver_ids=["approver-1"], deployed_at="2026-09-06T00:00:00Z",
        ),
    )


def batch(*changes: DeploymentRequest, key: str = "batch-key-0001") -> BatchDeploymentRequest:
    return BatchDeploymentRequest(
        tenant_id=TENANT, site_id=SITE, idempotency_key=key, changes=changes
    )


def adapter_for(fake: FakeGitHub, client: httpx.AsyncClient) -> GitHubDeploymentAdapter:
    return GitHubDeploymentAdapter(client, TARGET, "token")


REPO = {"a.html": "<h1>old a</h1>", "b.html": "<h1>old b</h1>", "c.html": "<h1>old c</h1>"}


def three_changes() -> tuple[DeploymentRequest, ...]:
    return (
        change("a.html", REPO["a.html"], "<h1>new a</h1>"),
        change("b.html", REPO["b.html"], "<h1>new b</h1>"),
        change("c.html", REPO["c.html"], "<h1>new c</h1>"),
    )


async def test_three_changes_become_one_branch_and_one_pull_request() -> None:
    fake = FakeGitHub(REPO)
    async with httpx.AsyncClient(transport=fake.transport(), base_url="https://api.github.com") as c:
        result = await adapter_for(fake, c).deploy_batch(batch(*three_changes()))

    assert result.status == "applied"
    assert result.external_ref == "https://github.com/acme/site/pull/7"
    assert len(fake.pulls) == 1
    assert sorted(result.applied_paths) == ["a.html", "b.html", "c.html"]
    # One branch carries all three writes.
    assert len({branch for branch, _ in fake.writes}) == 1
    assert sorted(path for _, path in fake.writes) == ["a.html", "b.html", "c.html"]
    assert fake.files["a.html"] == "<h1>new a</h1>"
    assert result.manifest_json["change_count"] == 3


async def test_a_retry_under_the_same_key_returns_the_same_pull_request() -> None:
    fake = FakeGitHub(REPO)
    async with httpx.AsyncClient(transport=fake.transport(), base_url="https://api.github.com") as c:
        adapter = adapter_for(fake, c)
        first = await adapter.deploy_batch(batch(*three_changes()))
        writes_after_first = len(fake.writes)
        second = await adapter.deploy_batch(batch(*three_changes()))

    assert first.external_ref == second.external_ref
    assert len(fake.pulls) == 1
    # The retry is a read: nothing was written a second time.
    assert len(fake.writes) == writes_after_first


async def test_one_drifted_file_stops_the_whole_batch_before_any_write() -> None:
    """A batch that half-applies leaves receipts disagreeing with the repo."""
    fake = FakeGitHub(REPO)
    drifted = change("b.html", "<h1>something else entirely</h1>", "<h1>new b</h1>")
    changes = (three_changes()[0], drifted, three_changes()[2])

    async with httpx.AsyncClient(transport=fake.transport(), base_url="https://api.github.com") as c:
        with pytest.raises(DriftDetectedError, match="b.html"):
            await adapter_for(fake, c).deploy_batch(batch(*changes))

    assert fake.writes == []
    assert fake.pulls == []
    assert len(fake.branches) == 1  # only main
    assert fake.files == REPO


async def test_two_changes_to_one_path_are_refused_before_anything_runs() -> None:
    with pytest.raises(ValueError, match="two changes to the same path"):
        batch(
            change("a.html", REPO["a.html"], "<h1>first</h1>"),
            change("a.html", REPO["a.html"], "<h1>second</h1>"),
        )


async def test_an_empty_batch_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one change"):
        batch()


async def test_a_failed_commit_partway_through_is_reported_with_its_path() -> None:
    fake = FakeGitHub(REPO, fail={"/contents/b.html": 403})
    async with httpx.AsyncClient(transport=fake.transport(), base_url="https://api.github.com") as c:
        with pytest.raises(GitHubDeploymentError, match="github_commit_failed:403:b.html"):
            await adapter_for(fake, c).deploy_batch(batch(*three_changes()))

    # No pull request claims work that did not finish.
    assert fake.pulls == []


async def test_the_branch_name_is_stable_for_a_key_and_differs_between_keys() -> None:
    one = batch(*three_changes(), key="batch-key-0001")
    same = batch(*three_changes(), key="batch-key-0001")
    other = batch(*three_changes(), key="batch-key-0002")

    assert batch_branch_name(one) == batch_branch_name(same)
    assert batch_branch_name(one) != batch_branch_name(other)
    assert str(SITE) in batch_branch_name(one)


async def test_the_pull_request_body_lists_every_change_and_its_approvers() -> None:
    body = batch_pull_request_body(batch(*three_changes()))

    assert "3 changes" in body
    assert "`a.html`" in body and "`b.html`" in body and "`c.html`" in body
    assert "approver-1" in body
    assert "own deployment receipt" in body
