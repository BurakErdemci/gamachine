"""Pick the workspace a Unity MCP call is scoped to: the lane folder for a lane sub-agent chat."""

import os
from typing import Optional


def _real(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def scope_workspace(db, conversation_id: int, user_id: int) -> Optional[str]:
    main = db.get_last_workspace(user_id)
    try:
        if not main or db.get_agent_of(conversation_id) is None:
            return main
        own = db.get_conversation_workspace(conversation_id)
        if not own:
            return main
        lanes = _real(os.path.join(main, ".gamachine", "lanes"))
        # realpath on both sides: a link inside the lanes folder must not escape it
        if os.path.dirname(_real(own)) == lanes and os.path.isdir(own):
            return own
    except Exception:
        pass
    return main
