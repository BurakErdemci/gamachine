"""The provider/model a chat runs with.

Owner request: switching chats used to carry one global selection over, so a
chat picked up whatever another chat last chose. Each chat now keeps its own
provider/model; the global ai_configs row only means "the default for a new
chat = the last model the user picked". API keys stay per provider.

The backend decides: every turn of a chat (typed, queued, woken, or sent from
the phone) resolves its model here, whatever a window shows. Writers (the
desktop's /save-ai-config, the phone bridge) go through `pick_chat_model`,
which validates through `set_chat_model`.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Tuple

from providers import model_catalog
from providers.families import subscription_family  # noqa: F401  (re-exported)

logger = logging.getLogger(__name__)

LOCAL_PROVIDERS = ("subscription", "ollama")
MODEL_NAME_MAX = 200


class ChatModelError(ValueError):
    """A refused model choice. `code` is short and stable; the renderer and
    the phone word it themselves (the backend has no translations)."""

    def __init__(self, code: str, **extra: Any):
        super().__init__(code)
        self.code = code
        self.extra = extra


def known_provider(provider_type: Any) -> bool:
    return isinstance(provider_type, str) and (
        provider_type in LOCAL_PROVIDERS or provider_type in model_catalog.supported_providers())


def check_model_choice(provider_type: Any, model_name: Any) -> Tuple[str, str]:
    """The checks every model write goes through (/save-ai-config, phone).

    No catalog membership: Settings takes a typed model id and the cloud
    lists are live network reads, so membership would refuse ids the desktop
    accepts. An empty model is what Settings saves for "provider default".
    """
    if not known_provider(provider_type):
        raise ChatModelError("unknown_provider")
    if not isinstance(model_name, str) or len(model_name) > MODEL_NAME_MAX or (
            model_name and not model_catalog.usable_model_id(model_name)):
        raise ChatModelError("bad_model")
    return provider_type, model_name


def cli_family(provider_type: str, model_name: str) -> Optional[str]:
    """The CLI family a subscription model runs on (None for API/local providers)."""
    if provider_type != "subscription":
        return None
    return subscription_family(model_name)


def agent_label(provider_type: str, model_name: str) -> str:
    """How a message names the agent: the CLI family, or `api-<provider>`."""
    return cli_family(provider_type, model_name) or f"api-{provider_type or 'unknown'}"


def _pair(value: Any) -> Optional[Tuple[str, str]]:
    if (isinstance(value, (tuple, list)) and len(value) == 2 and isinstance(value[0], str)
            and value[0] and isinstance(value[1], str)):
        return value[0], value[1]
    return None


def message_pair(agent: Any, model: Any) -> Optional[Tuple[str, str]]:
    """A stored message's (provider, model) as an ai_configs pair, or None.

    Messages name the CLI family for subscription turns and `api-<provider>`
    for API loops (conversation_routes._message_agent). A pair only counts
    when it maps back without guessing: the family must be the one the model
    id itself selects.
    """
    if not isinstance(agent, str) or not isinstance(model, str) or not model:
        return None
    if not model_catalog.usable_model_id(model):
        return None
    if agent.startswith("api-"):
        provider = agent[4:]
        if provider == "subscription" or not known_provider(provider):
            return None
        return provider, model
    if subscription_family(model) != agent:
        return None
    return "subscription", model


def _resolve(db, user_id: int, conversation_id: int) -> Tuple[Tuple[str, str], bool]:
    stored = _pair(db.get_conversation_model(conversation_id))
    if stored:
        return stored, True
    latest = _pair(db.get_latest_message_agent(conversation_id))
    fallback = message_pair(*latest) if latest else None
    if fallback:
        return fallback, False
    provider_type, model_name, _, _ = db.get_ai_config(user_id)
    return (provider_type, model_name), False


def chat_model(db, user_id: int, conversation_id: int) -> Dict[str, str]:
    """The chat's model: stored, else its latest message's, else the global default."""
    (provider_type, model_name), _ = _resolve(db, user_id, conversation_id)
    return {"provider_type": provider_type, "model_name": model_name}


def turn_model(db, user_id: int, conversation_id: int) -> Tuple[str, str]:
    """`chat_model` for a turn that is starting. A chat with nothing stored is
    stamped with what it resolved to, so its first turn fixes its model; the
    stamp never overwrites a model set meanwhile, and a lost stamp means the
    turn runs the model that won."""
    (provider_type, model_name), stored = _resolve(db, user_id, conversation_id)
    if not stored and not db.set_conversation_model(
            conversation_id, provider_type, model_name, only_if_unset=True):
        winner = _pair(db.get_conversation_model(conversation_id))
        if winner:
            return winner
    return provider_type, model_name


def _ollama_up() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2.0) as r:
            return r.status == 200
    except Exception:
        return False


def _cli_state(family: str) -> dict:
    # Spawns nothing: login stays unknown (None), which counts as ready below,
    # as it does in /provider-ready for every CLI it cannot measure.
    from providers.oneshot_cli import installed_clis
    return {"installed": installed_clis().get(family, False), "loggedIn": None}


def provider_readiness(db, user_id: int, provider_type: str, model_name: str, *,
                       cli_state: Optional[Callable[[str], dict]] = None,
                       ollama_up: Optional[Callable[[], bool]] = None) -> dict:
    """Is what backs this provider/model there now? /provider-ready's answer.

    Codes, not text: `needs` = None | "apikey" | "install" | "login" | "service",
    `kind` = "api" | "cli" | "local". The route passes probes it measured
    asynchronously; the defaults are synchronous and spawn nothing.
    """
    if provider_type == "ollama":
        up = (ollama_up or _ollama_up)()
        return {"ready": up, "kind": "local", "provider": "ollama",
                "needs": None if up else "service"}
    if provider_type != "subscription":
        has_key = bool(db.get_api_key(user_id, provider_type))
        return {"ready": has_key, "kind": "api", "provider": provider_type,
                "needs": None if has_key else "apikey"}
    family = cli_family(provider_type, model_name)
    state = (cli_state or _cli_state)(family) or {}
    if not state.get("installed"):
        return {"ready": False, "kind": "cli", "provider": family, "needs": "install"}
    # None = not measured, False = measured logged out; not knowing is not absence.
    if state.get("loggedIn") is False:
        return {"ready": False, "kind": "cli", "provider": family, "needs": "login"}
    return {"ready": True, "kind": "cli", "provider": family, "needs": None}


def check_chat(db, user_id: int, conversation_id: Any) -> None:
    """Refuse (`unknown_chat`) a chat that is not this user's or is a side chat,
    before anything is written."""
    if (isinstance(conversation_id, bool) or not isinstance(conversation_id, int)
            or db.get_conversation_owner(conversation_id) != user_id
            or db.get_side_of(conversation_id) is not None):
        raise ChatModelError("unknown_chat")


def set_chat_model(db, user_id: int, conversation_id: int, provider_type: str,
                   model_name: str, *, require_ready: bool = True,
                   readiness: Optional[Callable[..., dict]] = None) -> Dict[str, str]:
    """Store a chat's model after the checks every writer shares.

    Raises ChatModelError (a ValueError) with `code`:
      unknown_chat     - no such chat of this user, or a side chat
      unknown_provider - provider type the app does not know
      bad_model        - model id empty-invalid, too long or with control chars
      not_ready        - `require_ready` and the provider is not usable now;
                         `extra["needs"]` says what is missing
    The desktop passes require_ready=False: its picker is optimistic by design
    (a keyless cloud pick switches, then opens Settings for the key). Only
    this chat changes; a pick as a user makes it goes through `pick_chat_model`.
    """
    check_chat(db, user_id, conversation_id)
    provider_type, model_name = check_model_choice(provider_type, model_name)
    if require_ready:
        state = (readiness or provider_readiness)(db, user_id, provider_type, model_name)
        if not state.get("ready"):
            raise ChatModelError("not_ready", needs=state.get("needs"))
    if not db.set_conversation_model(conversation_id, provider_type, model_name):
        raise ChatModelError("unknown_chat")
    return {"provider_type": provider_type, "model_name": model_name}


# Who wants to hear of a pick (the phone bridge, to tell paired phones).
# Called as (conversation_id, provider_type, model_name) on the thread that
# picked, after the pick is stored.
_pick_listeners: list = []


def add_pick_listener(listener: Callable[[int, str, str], None]) -> None:
    if listener not in _pick_listeners:
        _pick_listeners.append(listener)


def remove_pick_listener(listener: Callable[[int, str, str], None]) -> None:
    if listener in _pick_listeners:
        _pick_listeners.remove(listener)


def _tell_listeners(conversation_id: int, provider_type: str, model_name: str) -> None:
    for listener in list(_pick_listeners):
        try:
            listener(conversation_id, provider_type, model_name)
        except Exception:
            logger.exception("[chat_model] a pick listener failed")


def pick_chat_model(db, user_id: int, conversation_id: int, provider_type: str,
                    model_name: str, *, require_ready: bool = True,
                    readiness: Optional[Callable[..., dict]] = None) -> Dict[str, str]:
    """A user's model pick for one chat, with every effect the desktop's picker
    has: the chat stores it (`set_chat_model`, same refusals) and the global
    row follows, because "a new chat opens on the last model picked"
    (`_resolve` falls back to it). /save-ai-config and the phone's `set_model`
    both end here, so they cannot drift apart. Listeners are told afterwards;
    a failing one never undoes the pick.

    The two writes are not one transaction. Once the chat row has changed the
    listeners are told even when the default write raises (which then
    propagates), so a phone that gets an error re-reads the model the chat
    really has instead of showing the one it left.
    """
    result = set_chat_model(db, user_id, conversation_id, provider_type, model_name,
                            require_ready=require_ready, readiness=readiness)
    try:
        db.save_ai_config(user_id, result["provider_type"], result["model_name"], "")
    finally:
        _tell_listeners(conversation_id, result["provider_type"], result["model_name"])
    return result
