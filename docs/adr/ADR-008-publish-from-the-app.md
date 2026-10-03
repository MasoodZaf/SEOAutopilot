# ADR-008 — Publish an approved change from the app

- **Status:** accepted; implemented and tested against a fake GitHub and PostgreSQL, not yet
  exercised against a real repository
- **Date:** 2026-10-03

## Context

Deploying has always meant opening a pull request. The GitHub adapter's docstring, the connector's
permission comment and the dashboard all said the product "only ever proposes": a person merged
every change on GitHub. That was a deliberate trust boundary, and it cost a context switch per
change -- approve here, then open GitHub, find the pull request, and merge it.

The owner asked for blogs, corrections and answer blocks to go live in one click after human
review. The connector already holds `contents:write` and `pull_requests:write`, which is enough for
GitHub's merge endpoint, so this is a change of policy, not of permission.

## Decision

An approved proposal can be **published** from the dashboard: one click opens its pull request if
needed and merges it. It is a person's act after review, never an unattended deployment.

- **Off by default.** A site allows it only when an owner or admin turns on *Publish from SEO
  Autopilot* (`site.publish_from_app`, audited in `governance.settings_updated`).
- **Who.** Owners and admins: the roles that may both approve and deploy. A developer may open a
  pull request and a SEO manager may approve one; neither publishes alone.
- **Every deployment gate still applies:** the global switch, site mode, emergency freeze,
  scheduled freeze, and the daily change budget.
- **Approvals are re-counted** for the proposal's current version at publish time. High-risk
  changes (new pages, AI-written answers, claim-changing edits) still need their two approvers.
- **Exactly the reviewed change.** Before merging, the pull request must target the base branch,
  touch only the proposal's file, and hold the approved content byte for byte at its head. The
  merge is pinned to that head commit (`sha`), so a push in between makes GitHub refuse it.
- **GitHub's rules are final.** Branch protection, required checks and reviews, and merge settings
  are never bypassed. A refusal leaves the pull request open, is recorded as
  `proposal.publish_refused`, and the dashboard says to merge on GitHub.
- **Some classes stay on GitHub, whatever the setting:** canonical, robots, noindex and redirect
  directives (recomputed from the proposal's content); robots.txt and sitemaps; redirect, host,
  CI and dependency files; and template-wide files (layouts, shared partials, headers, footers).
- **Trail.** `proposal.published` audit event and `proposal.published.v1` outbox event, with the
  merge commit and approvers; the receipt's manifest records the merge.

## Consequences

- The "proposes only" claim is now "proposes by default; merges on a person's click where the site
  allows it". The adapter, connector comment, tutorial and SECURITY.md say so.
- A merge goes live only if the site redeploys from its default branch. Post-deploy verification
  still decides whether the change is on the live page.
- Undoing a published change is still a revert pull request a person merges; publishing the revert
  in one click is a follow-up, not part of this decision.
- Batch pull requests (several files) are refused by the one-file check and are merged on GitHub.
