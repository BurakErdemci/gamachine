"""Joining an agent's separate text blocks into one answer.

A turn can carry several text blocks with tool calls between them ("Reading the
design doc." ... tool ... "The repo is open."). The providers appended them back
to back, so the chat showed "Reading the design doc.The repo is open." as one
run-on paragraph (measured 10 Oct 2026, a five-hour Claude run).
"""


def block_break(prev: str) -> str:
    """Separator to put before a new text block that follows `prev`."""
    if not prev or prev.endswith("\n\n"):
        return ""
    return "\n" if prev.endswith("\n") else "\n\n"
