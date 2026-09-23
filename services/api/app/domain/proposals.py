import difflib
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import unquote
from uuid import UUID


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def generate_unified_diff(before: str, after: str, filepath: str = "target") -> str:
    """Generates a clean unified diff between before and after contents."""
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"a/{filepath}",
        tofile=f"b/{filepath}",
        lineterm="\n",
    )
    return "".join(diff)


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    name: str
    passed: bool
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


PROHIBITED_CLAIM_PATTERNS = [
    "guaranteed #1 ranking",
    "guaranteed ranking",
    "bypass robots",
    "hack search algorithms",
    "manipulate google",
]

CONTROL_DIRECTIVE_PATTERNS: dict[str, re.Pattern[str]] = {
    "canonical": re.compile(r"<link\b[^>]*\brel\s*=\s*[\"']?\s*canonical\b[^>]*>", re.IGNORECASE),
    "meta_robots": re.compile(r"<meta\b[^>]*\bname\s*=\s*[\"']?\s*robots\b[^>]*>", re.IGNORECASE),
    "meta_refresh": re.compile(
        r"<meta\b[^>]*\bhttp-equiv\s*=\s*[\"']?\s*refresh\b[^>]*>", re.IGNORECASE
    ),
    "x_robots_tag": re.compile(r"\bx-robots-tag\b[^\r\n]*", re.IGNORECASE),
}


def detect_control_directive_changes(before_content: str, after_content: str) -> list[str]:
    """Names the indexing-control directives a proposal introduces, edits, or removes.

    Canonical, robots, and refresh-redirect directives decide whether a page is
    indexed at all and where its authority points. A change to one is never a
    routine metadata edit however small the diff looks, so it is reported here
    and classified as high risk rather than being sized by character count.
    """
    changed: list[str] = []
    for name, pattern in CONTROL_DIRECTIVE_PATTERNS.items():
        before = [match.group(0).strip().lower() for match in pattern.finditer(before_content)]
        after = [match.group(0).strip().lower() for match in pattern.finditer(after_content)]
        if before != after:
            changed.append(name)
    return changed


def _check_target_path(target_type: str, target_path: str) -> ValidationCheck:
    """Rejects traversal and malformed targets, including percent-encoded forms.

    Decoding twice matters: a single decode turns `%252e%252e` into `%2e%2e`,
    which a naive `".." in path` test still reads as safe.
    """
    name = "target_path_safety"
    if not target_path:
        return ValidationCheck(name, False, "Target path is empty.")

    decoded = unquote(unquote(target_path))
    if "\x00" in decoded:
        return ValidationCheck(name, False, "Target path contains a null byte.")
    if "\\" in decoded:
        return ValidationCheck(name, False, "Target path contains a backslash separator.")
    if any(segment == ".." for segment in decoded.split("/")):
        return ValidationCheck(
            name, False, "Target path contains a traversal segment, including encoded forms."
        )
    if target_type == "github_file":
        if decoded.startswith("/"):
            return ValidationCheck(
                name, False, "Repository file paths must be relative to the repository root."
            )
    elif decoded.startswith("//"):
        return ValidationCheck(
            name, False, "URL target path must not begin with a protocol-relative prefix."
        )
    return ValidationCheck(name, True, "Target path is safe and well-formed.")


def validate_proposal_content(
    target_type: str,
    target_path: str,
    before_content: str,
    after_content: str,
) -> list[ValidationCheck]:
    """Validates proposal contents against syntax, structural, and claim safety rules."""
    checks: list[ValidationCheck] = []

    if before_content.strip() == after_content.strip():
        checks.append(ValidationCheck("non_empty_diff", False, "Proposed content is identical to base content."))
    else:
        checks.append(ValidationCheck("non_empty_diff", True, "Proposed changes contain actionable diff."))

    if target_type == "json_ld_schema":
        try:
            parsed = json.loads(after_content)
            if not isinstance(parsed, dict) or "@context" not in parsed:
                checks.append(ValidationCheck("json_ld_syntax", False, "JSON-LD schema must be a valid JSON object containing @context."))
            else:
                checks.append(ValidationCheck("json_ld_syntax", True, "JSON-LD structured data syntax is valid."))
        except json.JSONDecodeError as err:
            checks.append(ValidationCheck("json_ld_syntax", False, f"Invalid JSON syntax in schema proposal: {err}"))
    else:
        checks.append(ValidationCheck("syntax_check", True, "Target content format is valid."))

    lower_after = after_content.lower()
    found_prohibited = [pat for pat in PROHIBITED_CLAIM_PATTERNS if pat in lower_after]
    if found_prohibited:
        checks.append(
            ValidationCheck(
                "prohibited_claim_safety",
                False,
                f"Proposal contains disallowed deceptive/ranking claim: {found_prohibited[0]}.",
            )
        )
    else:
        checks.append(ValidationCheck("prohibited_claim_safety", True, "No prohibited ranking claims detected."))

    checks.append(_check_target_path(target_type, target_path))

    return checks


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    risk: str
    requires_approval: bool
    required_approver_count: int
    allowed_roles: list[str]
    separation_of_duties_enforced: bool
    can_auto_deploy: bool
    rejection_reasons: list[str]
    control_directive_changes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Risk decides the floor; a site may raise it, and may only lower it for the
# tiers where a single reviewer is a defensible reading of the change.
TIER_APPROVER_FLOOR = {"low": 1, "medium": 2, "high": 2, "prohibited": 2}
LOWERABLE_TIERS = {"low", "medium"}


def resolve_required_approver_count(risk: str, site_override: int | None) -> int:
    """How many distinct approvers this change needs, before the author.

    A tenant running a small site should not need three people to change a
    heading, and a tenant running a regulated one should be able to demand more
    than two. What a tenant may not do is talk its way below two approvers on a
    change to canonical, robots or redirect directives, which is why lowering
    stops at medium: those changes are forced to high risk precisely so that no
    per-site setting can reach them.
    """
    floor = TIER_APPROVER_FLOOR.get(risk, 2)
    if site_override is None:
        return floor
    if site_override >= floor:
        return site_override
    if risk in LOWERABLE_TIERS:
        return max(1, site_override)
    return floor


def evaluate_proposal_policy(
    target_type: str,
    target_path: str,
    before_content: str,
    after_content: str,
    validations: list[ValidationCheck],
    author_id: UUID | None = None,
    tenant_mode: str = "recommend",
    site_required_approver_count: int | None = None,
) -> PolicyDecision:
    """Evaluates risk classification, approver rules, and deployment eligibility."""
    rejection_reasons: list[str] = []

    for val in validations:
        if not val.passed:
            rejection_reasons.append(f"Validation failed: {val.name} ({val.message})")

    directive_changes = detect_control_directive_changes(before_content, after_content)

    if any(val.name == "prohibited_claim_safety" and not val.passed for val in validations):
        risk = "prohibited"
    elif "robots.txt" in target_path.lower() or "sitemap" in target_path.lower():
        risk = "high"
    elif directive_changes:
        # Canonical, robots, and redirect directives are never a low-risk edit.
        risk = "high"
    elif target_type in {"content_edit", "github_file"} and before_content == "":
        # A file that does not exist yet is a whole new page on the site,
        # however short: never less than high.
        risk = "high"
    elif target_type in {"html_meta", "json_ld_schema", "link_insertion"}:
        risk = "low"
    elif target_type in {"content_edit", "github_file"}:
        risk = "medium" if abs(len(after_content) - len(before_content)) < 300 else "high"
    else:
        risk = "medium"

    if risk == "prohibited":
        rejection_reasons.append("Prohibited change class cannot be deployed.")

    requires_approval = True
    required_approver_count = resolve_required_approver_count(risk, site_required_approver_count)
    allowed_roles = ["owner", "admin", "seo_manager", "editor"] if risk == "low" else ["owner", "admin", "seo_manager"]
    separation_of_duties_enforced = True
    # `not directive_changes` is redundant while they force high risk, and is
    # kept so that a later change to the risk ladder cannot quietly make an
    # indexing-control change auto-deployable.
    can_auto_deploy = (
        tenant_mode == "autopilot"
        and risk == "low"
        and not rejection_reasons
        and not directive_changes
    )

    return PolicyDecision(
        risk=risk,
        requires_approval=requires_approval,
        required_approver_count=required_approver_count,
        allowed_roles=allowed_roles,
        separation_of_duties_enforced=separation_of_duties_enforced,
        can_auto_deploy=can_auto_deploy,
        rejection_reasons=rejection_reasons,
        control_directive_changes=directive_changes,
    )
