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

import re
from pathlib import PurePosixPath

from app.domain.proposals import detect_control_directive_changes

_INDEXING_FILES = re.compile(r"(^|/)(robots\.txt|sitemap[^/]*\.xml)$", re.IGNORECASE)
_CONFIGURATION = re.compile(
    r"(^|/)("
    r"\.htaccess|_redirects|_headers|netlify\.toml|vercel\.json|nginx[^/]*\.conf|"
    r"package\.json|package-lock\.json|pnpm-lock\.yaml|yarn\.lock|requirements[^/]*\.txt|"
    r"pyproject\.toml|poetry\.lock|gemfile(\.lock)?|go\.(mod|sum)|composer\.(json|lock)|"
    r"dockerfile|docker-compose[^/]*\.ya?ml|[^/]*\.config\.(js|cjs|mjs|ts)|_config\.ya?ml"
    r")$",
    re.IGNORECASE,
)
_CONFIGURATION_DIRS = ("/.github/", "/.circleci/", "/.gitlab/")
_TEMPLATE_STEMS = {
    "layout", "_layout", "base", "_app", "_document", "template", "header", "footer",
    "head", "nav", "navbar", "default",
}
_TEMPLATE_DIRS = ("/_layouts/", "/_includes/", "/partials/", "/layouts/", "/templates/", "/includes/")


def publish_blocked_reason(target_path: str, before_content: str, after_content: str) -> str | None:
    """Why this change must be merged on GitHub, or None when the app may publish it."""
    path = "/" + target_path.strip().lstrip("/")
    if detect_control_directive_changes(before_content, after_content):
        return "indexing_control_change"
    if _INDEXING_FILES.search(path):
        return "indexing_control_file"
    if _CONFIGURATION.search(path) or any(part in path.lower() for part in _CONFIGURATION_DIRS):
        return "site_configuration_file"
    if PurePosixPath(path).stem.lower() in _TEMPLATE_STEMS or any(
        part in path.lower() for part in _TEMPLATE_DIRS
    ):
        return "template_wide_file"
    return None
