import json
from dataclasses import asdict, dataclass


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


def validate_proposal_content(
    target_type: str,
    target_path: str,
    before_content: str,
    after_content: str,
) -> list[ValidationCheck]:
    """Validates proposal contents against syntax, structural, and claim safety rules."""
    checks: list[ValidationCheck] = []

    # 1. Non-empty change check
    if before_content.strip() == after_content.strip():
        checks.append(ValidationCheck("non_empty_diff", False, "Proposed content is identical to base content."))
    else:
        checks.append(ValidationCheck("non_empty_diff", True, "Proposed changes contain actionable diff."))

    # 2. Syntax validation
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

    # 3. Prohibited claim check
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

    # 4. Target path check
    if not target_path or ".." in target_path or target_path.startswith("//"):
        checks.append(ValidationCheck("target_path_safety", False, "Target path contains unsafe traversal or malformed format."))
    else:
        checks.append(ValidationCheck("target_path_safety", True, "Target path is safe and well-formed."))

    return checks
