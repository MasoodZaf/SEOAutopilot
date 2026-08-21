import difflib
import hashlib


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
