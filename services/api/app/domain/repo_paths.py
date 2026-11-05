"""What kind of repository file a path names, for the rules that care.

Shared by the proposal risk ladder and by publish-from-app, so a file the app
may not publish is never also a file a proposal calls routine. Paths are
compared lower-cased with a leading slash; nothing here reads content.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Literal

PathKind = Literal["ci", "indexing", "configuration", "template"]

INDEXING_FILES = re.compile(r"(^|/)(robots\.txt|sitemap[^/]*\.xml)$", re.IGNORECASE)
CONFIGURATION_FILES = re.compile(
    r"(^|/)("
    r"\.htaccess|_redirects|_headers|netlify\.toml|vercel\.json|nginx[^/]*\.conf|"
    r"package\.json|package-lock\.json|pnpm-lock\.yaml|yarn\.lock|requirements[^/]*\.txt|"
    r"pyproject\.toml|poetry\.lock|gemfile(\.lock)?|go\.(mod|sum)|composer\.(json|lock)|"
    r"dockerfile|docker-compose[^/]*\.ya?ml|[^/]*\.config\.(js|cjs|mjs|ts)|_config\.ya?ml"
    r")$",
    re.IGNORECASE,
)
# Files that run code with the repository's secrets the moment they are pushed
# to any branch -- before a review, before a merge. No SEO change needs them.
CI_DIRS = ("/.github/workflows/", "/.github/actions/", "/.circleci/", "/.gitlab/", "/.gitlab-ci")
CONFIGURATION_DIRS = ("/.github/",)
TEMPLATE_STEMS = {
    "layout", "_layout", "base", "_app", "_document", "template", "header", "footer",
    "head", "nav", "navbar", "default",
}
TEMPLATE_DIRS = ("/_layouts/", "/_includes/", "/partials/", "/layouts/", "/templates/", "/includes/")


def path_kind(target_path: str) -> PathKind | None:
    path = "/" + target_path.strip().lstrip("/")
    lowered = path.lower()
    if any(part in lowered for part in CI_DIRS):
        return "ci"
    if INDEXING_FILES.search(path):
        return "indexing"
    if CONFIGURATION_FILES.search(path) or any(part in lowered for part in CONFIGURATION_DIRS):
        return "configuration"
    if PurePosixPath(path).stem.lower() in TEMPLATE_STEMS or any(
        part in lowered for part in TEMPLATE_DIRS
    ):
        return "template"
    return None
