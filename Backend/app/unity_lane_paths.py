"""Pick the workspace a Unity MCP call is scoped to: a valid lane folder for a lane sub-agent chat across project switches."""

import os
from typing import Optional

import unity_lanes


def scope_workspace(db, conversation_id: int, user_id: int) -> Optional[str]:
    main = db.get_last_workspace(user_id)
    try:
        if db.get_agent_of(conversation_id) is None:
            return main
        own = db.get_conversation_workspace(conversation_id)
        if not own or not isinstance(own, str):
            return main
        real_own = os.path.realpath(own)
        unity_lanes.validate_name(os.path.basename(real_own))
        lanes_dir = os.path.dirname(real_own)
        gamachine_dir = os.path.dirname(lanes_dir)
        project_dir = os.path.dirname(gamachine_dir)
        if (
            project_dir
            and project_dir != gamachine_dir
            and os.path.basename(lanes_dir) == "lanes"
            and os.path.basename(gamachine_dir) == ".gamachine"
        ):
            return own
    except Exception:
        pass
    return main
