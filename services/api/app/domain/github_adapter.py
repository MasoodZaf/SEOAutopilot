"""A deployment adapter that opens a real pull request.

Everything guarding deployment has been tested against `MockDeploymentAdapter`,
which changes nothing outside the process. That makes the gate provably correct
and practically inert: it protects a mock. This adapter is the first one whose
`deploy` has an effect someone else can see, so the properties the mock could
only assert now have to hold against a service that has its own opinions.

Three of them matter, and each is a QA scenario:

  11. The same idempotency key deploys once. The branch name is derived from the
      key, and GitHub refuses a second pull request from the same head branch,
      so the uniqueness lives in the service rather than in a check this code
      performs and then races against itself.
  12. Drift is refused. The file's current content is fetched and hashed, and a
      hash that does not match the proposal's `base_hash` means the file moved
      under the proposal -- the diff would apply to something the reviewer never
      saw.
  13. A failure leaves a trail. Every error carries the GitHub status and the
      branch, because a deployment that half happened is worse than one that did
      not, and the receipt is the only place anyone will look.

Nothing here force-pushes, merges, or deletes. The adapter's whole authority is
to propose: it writes one commit on a new branch and opens a pull request for a
human to accept or close.
"""

import base64
from dataclasses import dataclass
from typing import Any

import httpx

from app.domain.deployments import (
    DeploymentRequest,
    DeploymentResult,
    DriftDetectedError,
    RollbackRequest,
    RollbackResult,
)
from app.domain.proposals import compute_content_hash

GITHUB_API = "https://api.github.com"
API_VERSION = "2022-11-28"

# A branch this adapter owns, so nothing it creates can be confused with a
# human's work and a stale one is obvious in the branch list.
BRANCH_PREFIX = "seo-autopilot"


class GitHubDeploymentError(Exception):
    """A GitHub call failed in a way the caller has to see."""


@dataclass(frozen=True, slots=True)
class GitHubTarget:
    owner: str
    repository: str
    base_branch: str = "main"

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repository}"


def branch_name(request: DeploymentRequest) -> str:
    """Derive the branch from the idempotency key.

    A retry has to land on the same branch, or GitHub will happily open a second
    pull request for the same change. The proposal id is included so a branch is
    identifiable without looking anything up.
    """
    key = compute_content_hash(request.idempotency_key)[:12]
    return f"{BRANCH_PREFIX}/{request.proposal_id}-{key}"


def commit_message(request: DeploymentRequest) -> str:
    manifest = request.manifest
    approvers = ", ".join(manifest.approver_ids) or "none recorded"
    return (
        f"seo: {request.target_path}\n"
        f"\n"
        f"Proposal {manifest.proposal_id} version {manifest.version}.\n"
        f"Author {manifest.author_id}. Approved by {approvers}.\n"
        f"Base hash {manifest.base_hash}.\n"
    )


class GitHubDeploymentAdapter:
    """Opens one pull request per approved proposal.

    The client is injected rather than built here so the caller owns its
    timeout, and so the tests can drive a transport instead of the network.
    """

    connector_type = "github"

    def __init__(
        self,
        client: httpx.AsyncClient,
        target: GitHubTarget,
        token: str,
    ) -> None:
        self._client = client
        self._target = target
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        try:
            return await self._client.request(
                method, f"{GITHUB_API}{path}", headers=self._headers, json=json
            )
        except httpx.TimeoutException as error:
            raise GitHubDeploymentError("github_timeout") from error
        except httpx.HTTPError as error:
            raise GitHubDeploymentError("github_unavailable") from error

    async def _existing_pull_request(self, head: str) -> dict[str, Any] | None:
        """The pull request already open for this branch, if there is one."""
        response = await self._request(
            "GET",
            f"/repos/{self._target.slug}/pulls"
            f"?head={self._target.owner}:{head}&state=all&per_page=1",
        )
        if response.status_code != 200:
            raise GitHubDeploymentError(
                f"github_pulls_lookup_failed:{response.status_code}"
            )
        found = response.json()
        return found[0] if isinstance(found, list) and found else None

    async def read_file(self, path: str) -> str | None:
        """The file's content on the base branch, or None if it is not there.

        Drafting a proposal needs the file the change will be applied to, and
        the base branch is the only honest source for it: a diff built against
        anything else would fail its own drift check at deploy time.
        """
        current = await self._current_file(path)
        return current[0] if current else None

    async def _current_file(self, path: str) -> tuple[str, str] | None:
        """The file's decoded content and blob sha on the base branch."""
        response = await self._request(
            "GET",
            f"/repos/{self._target.slug}/contents/{path}?ref={self._target.base_branch}",
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GitHubDeploymentError(
                f"github_contents_read_failed:{response.status_code}"
            )
        body = response.json()
        if body.get("encoding") != "base64" or "content" not in body:
            # A directory, a symlink, or a file too large for the contents API.
            raise GitHubDeploymentError("github_target_not_a_file")
        content = base64.b64decode(body["content"]).decode("utf-8")
        return content, str(body["sha"])

    async def _base_sha(self) -> str:
        response = await self._request(
            "GET", f"/repos/{self._target.slug}/git/ref/heads/{self._target.base_branch}"
        )
        if response.status_code != 200:
            raise GitHubDeploymentError(f"github_base_ref_failed:{response.status_code}")
        return str(response.json()["object"]["sha"])

    async def _create_branch(self, head: str, base_sha: str) -> None:
        response = await self._request(
            "POST",
            f"/repos/{self._target.slug}/git/refs",
            json={"ref": f"refs/heads/{head}", "sha": base_sha},
        )
        # 422 means the ref already exists, which is what a retry looks like.
        if response.status_code not in (201, 422):
            raise GitHubDeploymentError(f"github_branch_failed:{response.status_code}")

    async def deploy(self, request: DeploymentRequest) -> DeploymentResult:
        head = branch_name(request)

        # A retry that already produced a pull request returns it untouched.
        # Checked before anything is written, so a repeated call is a read.
        existing = await self._existing_pull_request(head)
        if existing is not None:
            return self._result(request, existing)

        current = await self._current_file(request.target_path)
        live_content = current[0] if current else ""
        if compute_content_hash(live_content) != request.base_hash:
            raise DriftDetectedError(
                f"Drift detected: {request.target_path} in {self._target.slug} no longer "
                f"matches the proposal base hash {request.base_hash}."
            )

        await self._create_branch(head, await self._base_sha())

        payload: dict[str, Any] = {
            "message": commit_message(request),
            "content": base64.b64encode(request.after_content.encode("utf-8")).decode(),
            "branch": head,
        }
        if current is not None:
            # Without the blob sha GitHub treats this as a create and refuses.
            payload["sha"] = current[1]
        written = await self._request(
            "PUT", f"/repos/{self._target.slug}/contents/{request.target_path}", json=payload
        )
        if written.status_code not in (200, 201):
            raise GitHubDeploymentError(
                f"github_commit_failed:{written.status_code}:{head}"
            )

        opened = await self._request(
            "POST",
            f"/repos/{self._target.slug}/pulls",
            json={
                "title": f"SEO: {request.target_path}",
                "head": head,
                "base": self._target.base_branch,
                "body": pull_request_body(request),
                "maintainer_can_modify": True,
            },
        )
        if opened.status_code == 422:
            # Another caller opened it between the lookup and here. Theirs is
            # the same change on the same branch, so it is the right answer.
            concurrent = await self._existing_pull_request(head)
            if concurrent is not None:
                return self._result(request, concurrent)
        if opened.status_code != 201:
            raise GitHubDeploymentError(f"github_pull_failed:{opened.status_code}:{head}")
        return self._result(request, opened.json())

    async def rollback(self, request: RollbackRequest) -> RollbackResult:
        """Undo a deployment, by the only two routes GitHub actually offers.

        An open pull request has changed nothing yet, so closing it is a
        complete rollback and the honest thing to report. A merged one is in
        the base branch, and undoing it means another commit -- so this opens a
        revert pull request restoring the proposal's `before_content` and says
        so plainly. It does not merge that itself: this adapter has never had
        merge authority and rollback is not the moment to grant it.
        """
        number = request.manifest_json.get("pull_request_number")
        if not isinstance(number, int):
            raise GitHubDeploymentError("github_rollback_no_pull_request")

        response = await self._request("GET", f"/repos/{self._target.slug}/pulls/{number}")
        if response.status_code != 200:
            raise GitHubDeploymentError(f"github_pull_read_failed:{response.status_code}")
        pull = response.json()

        if not pull.get("merged_at"):
            closed = await self._request(
                "PATCH",
                f"/repos/{self._target.slug}/pulls/{number}",
                json={"state": "closed"},
            )
            if closed.status_code != 200:
                raise GitHubDeploymentError(
                    f"github_pull_close_failed:{closed.status_code}:{number}"
                )
            return RollbackResult(
                status="applied",
                external_ref=str(pull.get("html_url") or request.external_ref),
                restored_hash=compute_content_hash(request.before_content),
                detail="pull_request_closed_before_merge",
            )

        return await self._open_revert(request, number)

    async def _open_revert(self, request: RollbackRequest, number: int) -> RollbackResult:
        head = f"{BRANCH_PREFIX}/revert-{request.proposal_id}-{number}"

        existing = await self._existing_pull_request(head)
        if existing is not None:
            return RollbackResult(
                status="applied",
                external_ref=str(existing.get("html_url") or ""),
                restored_hash=compute_content_hash(request.before_content),
                detail="revert_pull_request_already_open",
            )

        await self._create_branch(head, await self._base_sha())
        current = await self._current_file(request.target_path)
        payload: dict[str, Any] = {
            "message": (
                f"Revert seo: {request.target_path}\n\n"
                f"Restores the content proposal {request.proposal_id} replaced.\n"
                f"Reverts #{number}. {request.notes}".strip()
            ),
            "content": base64.b64encode(request.before_content.encode("utf-8")).decode(),
            "branch": head,
        }
        if current is not None:
            payload["sha"] = current[1]
        written = await self._request(
            "PUT", f"/repos/{self._target.slug}/contents/{request.target_path}", json=payload
        )
        if written.status_code not in (200, 201):
            raise GitHubDeploymentError(
                f"github_revert_commit_failed:{written.status_code}:{head}"
            )

        opened = await self._request(
            "POST",
            f"/repos/{self._target.slug}/pulls",
            json={
                "title": f"Revert SEO: {request.target_path}",
                "head": head,
                "base": self._target.base_branch,
                "body": (
                    f"Rolls back proposal `{request.proposal_id}`, deployed in #{number}.\n\n"
                    f"Restores `{request.target_path}` to the content recorded on the "
                    f"proposal before it was changed.\n\n"
                    f"**This is not merged.** The change is only undone once someone "
                    f"merges this.\n\n{request.notes}"
                ).strip(),
                "maintainer_can_modify": True,
            },
        )
        if opened.status_code != 201:
            raise GitHubDeploymentError(f"github_revert_pull_failed:{opened.status_code}:{head}")
        return RollbackResult(
            status="applied",
            external_ref=str(opened.json().get("html_url") or ""),
            restored_hash=compute_content_hash(request.before_content),
            detail="revert_pull_request_opened_not_merged",
        )


    def _result(self, request: DeploymentRequest, pull: dict[str, Any]) -> DeploymentResult:
        return DeploymentResult(
            connector_type=self.connector_type,
            external_ref=str(pull.get("html_url") or ""),
            status="applied",
            manifest_json={
                **request.manifest.to_dict(),
                "repository": self._target.slug,
                "base_branch": self._target.base_branch,
                "head_branch": branch_name(request),
                "pull_request_number": pull.get("number"),
                "pull_request_state": pull.get("state"),
            },
        )


def pull_request_body(request: DeploymentRequest) -> str:
    """What a reviewer needs to judge the change without opening the tool."""
    manifest = request.manifest
    approvers = "\n".join(f"- {approver}" for approver in manifest.approver_ids)
    return (
        f"Proposed by SEO Autopilot from proposal `{manifest.proposal_id}`.\n\n"
        f"**Target** `{request.target_path}` ({request.target_type})\n"
        f"**Site** `{manifest.site_id}`\n"
        f"**Author** `{manifest.author_id}`\n"
        f"**Approved by**\n{approvers or '- none recorded'}\n\n"
        f"**Base hash** `{manifest.base_hash}`\n"
        f"**Proposal hash** `{manifest.proposal_hash}`\n\n"
        f"The base hash was verified against the file on the base branch immediately "
        f"before this branch was created. If the file has changed since, close this and "
        f"re-propose rather than merging: the diff was reviewed against the old content.\n"
    )
