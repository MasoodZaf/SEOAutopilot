"""Which approved changes may be published from the app, and which may not.

Publishing merges the change's pull request from the dashboard. It is a
person's click after review, not an unattended deployment, but it does put the
change live without anyone opening GitHub -- so the changes the repository's
rules never let deploy without a human at the repository stay there. For
those the answer is always "merge it on GitHub", whatever the site's setting:

* indexing control: robots, canonical, noindex and redirect directives, or
  the robots.txt and sitemap files themselves;
* site configuration: redirects files, server and host config, CI, and
  dependency manifests;
* template-wide files: layouts, shared partials, headers and footers, where
  one edit changes every page.

The directives are recomputed from the proposal's own before and after
content, never read back from the stored policy record, so a record written
by an older policy cannot widen what publishing reaches.
"""

from __future__ import annotations

from app.domain.proposals import detect_control_directive_changes
from app.domain.repo_paths import path_kind

_REASONS = {
    "ci": "site_configuration_file",
    "configuration": "site_configuration_file",
    "indexing": "indexing_control_file",
    "template": "template_wide_file",
}


def publish_blocked_reason(target_path: str, before_content: str, after_content: str) -> str | None:
    """Why this change must be merged on GitHub, or None when the app may publish it."""
    if detect_control_directive_changes(before_content, after_content):
        return "indexing_control_change"
    kind = path_kind(target_path)
    return _REASONS[kind] if kind else None
