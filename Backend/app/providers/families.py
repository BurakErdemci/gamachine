from typing import Optional


def subscription_family(model_name: Optional[str]) -> str:
    """The CLI a subscription model id runs on: what `AgentRunner` dispatches.

    The one place a model id becomes a family. `spawn_env.env_family` is a table
    for binary names and also maps bare `codex`/`agy`/... to their CLIs, but
    the runner sends such an id to claude; every reader of a model id follows
    the runner, so readiness and the session key check the CLI that runs.
    """
    name = (model_name or "claude").lower()
    if name.startswith("cursor-"):
        return "cursor"
    if name.startswith("copilot-"):
        return "copilot"
    if name.startswith("opencode:"):
        return "opencode"
    if name.startswith("kimi-"):
        return "kimi"
    if name.startswith("gpt-"):
        return "codex"
    if name.startswith(("gemini", "agy-")):
        return "agy"
    return "claude"
