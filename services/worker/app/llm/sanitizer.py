import html
import re

from app.llm.base import PromptInjectionError

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"disregard\s+(all\s+)?(previous|prior|system)\s+prompts?",
    r"system\s*:\s*you\s+are\s+now",
    r"you\s+are\s+now\s+in\s+admin\s+mode",
    r"override\s+all\s+safety\s+protocols",
    r"bypass\s+policy\s+and\s+auto-?deploy",
    r"grant\s+full\s+access\s+to\s+production",
    r"drop\s+table\s+",
    r"<script>.*?</script>",
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in INJECTION_PATTERNS]


def sanitize_untrusted_text(text: str, max_chars: int = 8000) -> str:
    """Sanitize untrusted text from crawled web pages and search queries."""
    if not text:
        return ""
    # Strip null bytes and control chars
    clean = "".join(ch for ch in text if ch == "\n" or ch == "\t" or (ord(ch) >= 32 and ord(ch) != 127))
    clean = clean.strip()
    if len(clean) > max_chars:
        clean = clean[:max_chars] + "...[truncated]"
    return clean


def wrap_untrusted_evidence(tag: str, content: str) -> str:
    """Encloses untrusted content in an explicit XML isolation boundary."""
    sanitized = sanitize_untrusted_text(content)
    escaped = html.escape(sanitized, quote=False)
    return f"<{tag} data-trust='untrusted'>\n{escaped}\n</{tag}>"


def detect_and_guard_injection(text: str, strict: bool = True) -> str:
    """Checks for prompt injection attempts in untrusted input.

    If strict is True, raises PromptInjectionError. Otherwise neutralizes.
    """
    for pattern in COMPILED_PATTERNS:
        if pattern.search(text):
            if strict:
                raise PromptInjectionError("Hostile instruction injection detected in page evidence.")
            text = pattern.sub("[REDACTED_HOSTILE_INSTRUCTION]", text)
    return text
