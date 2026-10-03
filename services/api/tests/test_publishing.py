"""Publishing from the app: which changes may be merged here, and the merge itself.

The merge is driven against a fake GitHub. It has to refuse anything that is
not exactly the reviewed change, and treat GitHub's own refusals as final.
"""

import base64
import json
from typing import Any

import httpx
import pytest

from app.domain.github_adapter import GitHubDeploymentAdapter, GitHubMergeRefused, GitHubTarget
from app.domain.proposals import compute_content_hash
from app.domain.publishing import publish_blocked_reason

TARGET = GitHubTarget(owner="oryxen", repository="mindtools", base_branch="main")
PATH = "CalcHive/bmi-calculator.html"
REVIEWED = "<html><head><title>BMI Calculator</title></head><body></body></html>"


def test_ordinary_page_edits_may_be_published():
    assert publish_blocked_reason(PATH, "<title>BMI</title>", "<title>BMI Calculator</title>") is None
    assert publish_blocked_reason("blog/new-post.html", "", "<h1>New</h1>") is None


@pytest.mark.parametrize(
    ("path", "before", "after", "reason"),
    [
        (PATH, "<head></head>", '<head><meta name="robots" content="noindex"></head>', "indexing_control_change"),
        (PATH, "<head></head>", '<head><link rel="canonical" href="https://x.example/"></head>', "indexing_control_change"),
        ("robots.txt", "User-agent: *", "User-agent: *\nDisallow: /", "indexing_control_file"),
        ("public/sitemap.xml", "<urlset/>", "<urlset></urlset>", "indexing_control_file"),
        ("_redirects", "", "/a /b 301", "site_configuration_file"),
        ("package.json", "{}", '{"a":1}', "site_configuration_file"),
        (".github/workflows/deploy.yml", "", "on: push", "site_configuration_file"),
        ("next.config.ts", "", "export default {}", "site_configuration_file"),
        ("src/app/layout.tsx", "a", "b", "template_wide_file"),
        ("_includes/footer.html", "a", "b", "template_wide_file"),
    ],
)
def test_changes_kept_for_a_merge_on_github(path, before, after, reason):
    assert publish_blocked_reason(path, before, after) == reason


class FakePull:
    def __init__(self, *, files=(PATH,), head_content=REVIEWED, state="open", merged=False,
                 merge_status=200, merge_text="", squash_allowed=True, base="main"):
        self.files = list(files)
        self.head_content = head_content
        self.state = state
        self.merged = merged
        self.merge_status = merge_status
        self.merge_text = merge_text
        self.squash_allowed = squash_allowed
        self.base = base
        self.merge_calls: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith("/pulls/41"):
            return httpx.Response(200, json={
                "number": 41, "state": self.state, "title": "SEO: bmi",
                "html_url": "https://github.com/oryxen/mindtools/pull/41",
                "merged_at": "2026-10-03T00:00:00Z" if self.merged else None,
                "merge_commit_sha": "merged-sha" if self.merged else None,
                "head": {"sha": "head-sha-1"}, "base": {"ref": self.base},
            })
        if request.method == "GET" and path.endswith("/pulls/41/files"):
            return httpx.Response(200, json=[{"filename": name} for name in self.files])
        if request.method == "GET" and "/contents/" in path:
            assert request.url.params.get("ref") == "head-sha-1"
            encoded = base64.b64encode(self.head_content.encode()).decode()
            return httpx.Response(200, json={"encoding": "base64", "content": encoded, "sha": "blob"})
        if request.method == "PUT" and path.endswith("/pulls/41/merge"):
            body = json.loads(request.content)
            self.merge_calls.append(body)
            if body["merge_method"] == "squash" and not self.squash_allowed:
                return httpx.Response(405, json={"message": "Merge method squash merging is not allowed on this repository."})
            if self.merge_status != 200:
                return httpx.Response(self.merge_status, text=self.merge_text or "{}")
            return httpx.Response(200, json={"sha": "merge-sha-1", "merged": True})
        return httpx.Response(404)


async def merge_with(fake: FakePull):
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as client:
        adapter = GitHubDeploymentAdapter(client, TARGET, "token")
        return await adapter.merge(41, PATH, compute_content_hash(REVIEWED))


@pytest.mark.asyncio
async def test_the_reviewed_change_is_merged_pinned_to_the_checked_commit():
    fake = FakePull()
    result = await merge_with(fake)
    assert (result.merge_sha, result.already_merged) == ("merge-sha-1", False)
    [call] = fake.merge_calls
    assert call["sha"] == "head-sha-1" and call["merge_method"] == "squash"


@pytest.mark.asyncio
async def test_a_repository_without_squash_gets_a_plain_merge():
    fake = FakePull(squash_allowed=False)
    await merge_with(fake)
    assert [call["merge_method"] for call in fake.merge_calls] == ["squash", "merge"]


@pytest.mark.asyncio
async def test_an_already_merged_pull_request_is_not_merged_again():
    fake = FakePull(merged=True)
    result = await merge_with(fake)
    assert result.already_merged and fake.merge_calls == []


@pytest.mark.parametrize(
    ("fake", "reason"),
    [
        (FakePull(head_content=REVIEWED + "<!-- added later -->"), "changed_since_review"),
        (FakePull(files=(PATH, "CalcHive/other.html")), "pull_request_holds_other_changes"),
        (FakePull(state="closed"), "pull_request_closed"),
        (FakePull(base="release"), "pull_request_targets_another_branch"),
        (FakePull(merge_status=405, merge_text='{"message":"Required status check is expected."}'), "github_refused_merge"),
        (FakePull(merge_status=409), "changed_since_review"),
    ],
)
@pytest.mark.asyncio
async def test_anything_but_the_reviewed_change_is_refused(fake, reason):
    with pytest.raises(GitHubMergeRefused) as refused:
        await merge_with(fake)
    assert str(refused.value) == reason
    if reason != "github_refused_merge" and fake.merge_status != 409:
        assert fake.merge_calls == []
