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


# Kimi Code CLI aliases. A logged-in config.toml names the subscription models
# `kimi-code/<id>` (managed provider `managed:kimi-code`; doc config-files
# example, kimi.exe 2.5.3 `managedModelKey`). Every alias starts with "kimi-",
# so it stays in the kimi family above. Ids saved before the alias form (the
# picker offered `kimi-k3` / `kimi-k2.7-code` until Nov 2026) map to the alias
# that replaced them; `-m kimi-k3` dies with `Model "kimi-k3" is not configured`.
KIMI_ALIAS_PREFIX = "kimi-code/"
_KIMI_LEGACY_IDS = {
    "kimi-k3": "kimi-code/k3",
    "kimi-k2.7-code": "kimi-code/kimi-for-coding",
}


def kimi_cli_model(model_name: Optional[str]) -> str:
    """The `-m` alias the Kimi Code CLI is started with for a Gamachine model id."""
    name = (model_name or "").strip()
    if name.lower().startswith(KIMI_ALIAS_PREFIX):
        return name
    return _KIMI_LEGACY_IDS.get(name.lower(), name or KIMI_ALIAS_PREFIX + "k3")


def kimi_thinks_with_effort(model_name: Optional[str]) -> bool:
    """K3-family aliases are the only Kimi Code models that declare effort
    levels (doc config-files: "only the k3 family currently declares effort
    levels"; catalog family "kimi-k3": k3, k3-256k, k3.1-pro, k3.1-pro-highspeed)."""
    alias = kimi_cli_model(model_name).lower()
    return alias.startswith(KIMI_ALIAS_PREFIX + "k3")
