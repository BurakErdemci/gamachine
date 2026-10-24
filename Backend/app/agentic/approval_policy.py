"""Track running conversation turns and their mail depth."""

import logging
import threading


logger = logging.getLogger(__name__)
_LOCK = threading.Lock()


_AMBIENT_TOPLAM = 0
# conversation_id -> the mail depth of each turn of that chat running now, one
# entry per turn. Lets /mcp-approval-request accept a card's claimed owner only
# while that chat has a turn in flight, and gives a mail send the depth of the
# turn it comes from. The depth lives with the running turn, not with the chat:
# a per-chat value was reset by a user request arriving between a mail turn's
# route and its stream start (Codex mailverify, 27 Sep 2026).
_TURNS_BY_CONVERSATION: dict[int, list[int]] = {}


def _end_computer_activity(conversation_id: int) -> None:
    try:
        from computer_use import activity
        activity.end(conversation_id)
    except Exception:
        logger.exception("Failed to end computer activity for conversation %s", conversation_id)


class ambient_turn:
    """Track a running turn; always release its ownership on exit."""

    def __init__(self, workspace_path: str, generation_mode: str,
                 conversation_id: int | None = None, mail_depth: int = 0) -> None:
        self._conversation = (
            conversation_id if type(conversation_id) is int and conversation_id > 0 else None
        )
        self._mail_depth = max(0, int(mail_depth or 0))

    def __enter__(self) -> "ambient_turn":
        global _AMBIENT_TOPLAM
        with _LOCK:
            _AMBIENT_TOPLAM += 1
            if self._conversation is not None:
                _TURNS_BY_CONVERSATION.setdefault(self._conversation, []).append(
                    self._mail_depth)
        return self

    def __exit__(self, *_exc) -> None:
        global _AMBIENT_TOPLAM
        last_turn_exited = False
        with _LOCK:
            _AMBIENT_TOPLAM = max(0, _AMBIENT_TOPLAM - 1)
            if self._conversation is not None:
                depths = _TURNS_BY_CONVERSATION.get(self._conversation, [])
                if self._mail_depth in depths:
                    depths.remove(self._mail_depth)
                if not depths:
                    last_turn_exited = (
                        _TURNS_BY_CONVERSATION.pop(self._conversation, None) is not None
                    )
        # Presence ends with the last turn, even if an early-closed stream's runner exits later.
        if last_turn_exited:
            _end_computer_activity(self._conversation)
        return None


def conversation_turn_in_flight(conversation_id: int) -> bool:
    """Is a turn of this conversation running in AgentRunner.run right now?"""
    with _LOCK:
        return bool(_TURNS_BY_CONVERSATION.get(conversation_id))


def conversations_with_turn_in_flight() -> set[int]:
    """Snapshot of every conversation with a turn in AgentRunner.run now."""
    with _LOCK:
        return {cid for cid, depths in _TURNS_BY_CONVERSATION.items() if depths}


def running_mail_depth(conversation_id: int) -> int:
    """Highest mail depth among this conversation's running turns, 0 if none.

    The highest, because a send cannot tell which of two parallel turns of a
    chat it comes from; a user turn beside a depth-2 mail turn therefore
    cannot send either until the mail turn ends.
    """
    with _LOCK:
        return max(_TURNS_BY_CONVERSATION.get(conversation_id) or [0])
