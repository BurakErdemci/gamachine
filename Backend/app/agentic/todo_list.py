"""Per-conversation task list for the API agent (the equivalent of Claude Code's TodoWrite).

The model sends the whole list every time; it is kept in memory only, keyed by the
chat id, and bounded in size and in the number of chats kept.
"""
import threading
from collections import OrderedDict

MAX_ITEMS = 200
MAX_CONTENT_CHARS = 500
MAX_CHATS = 200
STATUSES = ("pending", "in_progress", "completed")

_lock = threading.Lock()
_lists: "OrderedDict[object, list]" = OrderedDict()


def format_list(items: list) -> str:
    marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
    return "\n".join(f"{marks[i['status']]} {i['content']}" for i in items) or "(empty)"


def set_todos(conversation_id, items) -> dict:
    """Replaces the chat's list. Returns the tool result; nothing is stored when the input is refused."""
    if not isinstance(items, list):
        return {"success": False, "error": "items must be a list of {content, status}."}
    if len(items) > MAX_ITEMS:
        return {"success": False, "error": f"At most {MAX_ITEMS} items."}
    clean = []
    for n, item in enumerate(items, 1):
        if not isinstance(item, dict):
            return {"success": False, "error": f"Item {n} is not an object."}
        content = item.get("content")
        status = item.get("status", "pending")
        if not isinstance(content, str) or not content.strip():
            return {"success": False, "error": f"Item {n} has no content."}
        if status not in STATUSES:
            return {"success": False,
                    "error": f"Item {n} has status {status!r}; use one of {', '.join(STATUSES)}."}
        clean.append({"content": content.strip()[:MAX_CONTENT_CHARS], "status": status})
    if sum(1 for i in clean if i["status"] == "in_progress") > 1:
        return {"success": False, "error": "At most one item may be in_progress."}
    with _lock:
        _lists[conversation_id] = clean
        _lists.move_to_end(conversation_id)
        while len(_lists) > MAX_CHATS:
            _lists.popitem(last=False)
    return {"success": True, "content": format_list(clean), "todos": clean}


def get_todos(conversation_id) -> list:
    with _lock:
        return [dict(i) for i in _lists.get(conversation_id, [])]


def clear(conversation_id) -> None:
    with _lock:
        _lists.pop(conversation_id, None)
