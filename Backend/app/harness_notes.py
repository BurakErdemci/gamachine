"""Notes the agent searches on demand to answer questions about its own tools, features and limits.

The notes are Markdown files in the `harness_notes/` folder next to this module (no `__init__.py` there, so this
module wins the import name). Each starts with a front matter block of `title`, `keywords` (English and Turkish
search words, comma separated) and `covers` (exact tool names the note explains). The first body line is the
one-line summary search returns. `status()` reads live state; it never returns a key, token or secret, because
only the fields named in `_STATUS_CONTEXT_FIELDS` are copied from the run context.
"""
import contextvars
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)



def _notes_dir() -> Path:
    """Next to this module from source; under PyInstaller's data root (sys._MEIPASS) when packaged."""
    bundle = getattr(sys, "_MEIPASS", None)
    base = Path(bundle) if bundle else Path(__file__).resolve().parent
    return base / "harness_notes"


NOTES_DIR = _notes_dir()
TOOL_SEARCH = "harness_search"
TOOL_READ = "harness_read"
TOOL_STATUS = "harness_status"
HARNESS_TOOLS = (TOOL_SEARCH, TOOL_READ, TOOL_STATUS)

_FOLD = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iissgguuoocc")
_WORD = re.compile(r"[a-z0-9_]+")
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)

_WEIGHT_COVERS = 8
_WEIGHT_TITLE = 5
_WEIGHT_KEYWORD = 4
_WEIGHT_BODY = 1
_BODY_HITS_CAP = 3

_STATUS_CONTEXT_FIELDS = ("provider_type", "model_name", "language", "read_only")
_RUN_CONTEXT: contextvars.ContextVar = contextvars.ContextVar("harness_run_context", default={})


@dataclass(frozen=True)
class Note:
    id: str
    title: str
    keywords: tuple
    covers: tuple
    body: str
    summary: str


def fold(text: str) -> str:
    """Lower-case and map the Turkish letters to ASCII, so "Tarayıcı" matches "tarayici"."""
    return str(text).translate(_FOLD).lower()


def _words(text: str) -> list:
    return _WORD.findall(fold(text))


def _split_list(value: str) -> tuple:
    return tuple(item for item in (part.strip() for part in re.split(r"[,\[\]\n]", value)) if item)


def _summary(body: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return re.split(r"(?<=[.!?])\s", line, maxsplit=1)[0]
    return ""


def parse_note(note_id: str, text: str) -> Optional[Note]:
    match = _FRONT_MATTER.match(text)
    if match is None:
        return None
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            fields[key.strip()] = value.strip()
    if not fields.get("title") or "keywords" not in fields or "covers" not in fields:
        return None
    body = text[match.end():].strip()
    return Note(note_id, fields["title"], _split_list(fields["keywords"]),
                _split_list(fields["covers"]), body, _summary(body))


_notes: Optional[dict] = None


def notes() -> dict:
    """Every note by id, loaded once. A file that does not parse is logged and skipped."""
    global _notes
    if _notes is None:
        loaded = {}
        for path in sorted(NOTES_DIR.glob("*.md")):
            note = parse_note(path.stem, path.read_text(encoding="utf-8"))
            if note is None:
                logger.warning("[harness_notes] %s has no valid front matter", path.name)
                continue
            loaded[note.id] = note
        _notes = loaded
    return _notes


def _matches(word: str, tokens: set) -> bool:
    if word in tokens:
        return True
    return len(word) >= 4 and any(token.startswith(word) for token in tokens)


def _score(note: Note, words: list) -> int:
    title = set(_words(note.title))
    keywords = set(_words(" ".join(note.keywords)))
    covers = {fold(name) for name in note.covers}
    body = _words(note.body)
    body_set = set(body)
    score = 0
    for word in words:
        if word in covers:
            score += _WEIGHT_COVERS
        if _matches(word, title):
            score += _WEIGHT_TITLE
        if _matches(word, keywords):
            score += _WEIGHT_KEYWORD
        if _matches(word, body_set):
            score += _WEIGHT_BODY * min(_BODY_HITS_CAP, sum(1 for token in body if token.startswith(word)))
    return score


def search(query: str, limit: int = 5) -> list:
    """Best notes for the query: [{id, title, summary}], best first; empty when nothing matches."""
    words = [word for word in _words(query) if len(word) > 1]
    if not words:
        return []
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 5
    scored = [(_score(note, words), note) for note in notes().values()]
    scored = [(score, note) for score, note in scored if score > 0]
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [{"id": note.id, "title": note.title, "summary": note.summary} for _, note in scored[:limit]]


def read(note_id: str) -> dict:
    note = notes().get(str(note_id or "").strip())
    if note is None:
        return {"success": False,
                "error": f"No note with id {note_id!r}. Use harness_search to find an id.",
                "known_ids": sorted(notes())}
    return {"success": True, "id": note.id, "title": note.title, "content": note.body}


def format_search(results: list) -> str:
    if not results:
        return "No matching note. Try other words (Turkish or English), or a tool name."
    return "\n".join(f"- {item['id']}: {item['title']} - {item['summary']}" for item in results)


def set_run_context(**fields) -> contextvars.Token:
    """Runner state the tools cannot see by themselves (provider_type, model_name, language, read_only)."""
    return _RUN_CONTEXT.set({key: fields[key] for key in _STATUS_CONTEXT_FIELDS if key in fields})


def run_context() -> dict:
    return _RUN_CONTEXT.get()


def reset_run_context(token: contextvars.Token) -> None:
    try:
        _RUN_CONTEXT.reset(token)
    except ValueError:
        # An async generator closed from another task cannot reset in its own context; the value dies with it.
        logger.debug("[harness_notes] run context reset skipped in a foreign context")


async def status(context: Optional[dict] = None, tools: Optional[list] = None) -> dict:
    """Live state of this run. A field the caller could not supply is None, never guessed."""
    context = context if context is not None else _RUN_CONTEXT.get()
    result = {
        "provider": context.get("provider_type"),
        "model": context.get("model_name"),
        "language": context.get("language"),
        "read_only": context.get("read_only"),
        "approval_mode": None,
        "computer_use_enabled": None,
        "browser_use_enabled": None,
        "unity_mcp": None,
        "enabled_tools": sorted(tools) if tools is not None else None,
    }
    try:
        from agentic import approval_mode
        result["approval_mode"] = approval_mode.current_mode()
    except Exception:
        logger.exception("[harness_notes] approval mode unavailable")
    try:
        from computer_use import settings as computer_settings
        result["computer_use_enabled"] = computer_settings.enabled_now()
    except Exception:
        logger.exception("[harness_notes] computer use state unavailable")
    try:
        from browser_use import settings as browser_settings
        result["browser_use_enabled"] = browser_settings.enabled_now()
    except Exception:
        logger.exception("[harness_notes] browser state unavailable")
    try:
        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
        result["unity_mcp"] = (await unity_mcp_manager.get_status()).get("status")
    except Exception:
        logger.exception("[harness_notes] unity mcp state unavailable")
    return result
