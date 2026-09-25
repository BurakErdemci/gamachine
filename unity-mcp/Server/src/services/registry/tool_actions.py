"""
Read/write classification for MCP tool calls, backed by ``tool_actions.json``.

The approval gate needs one answer per call: does this mutate anything? Tool
names alone cannot answer it -- most tools switch between reading and writing on
their ``action`` parameter, and four ``manage_build`` actions switch on a
*sibling* parameter instead. This module is the only place that decides.

Two properties are load-bearing:

* **Fail-closed.** Anything not positively proven to be a read is a write, so an
  unrecognised tool or action produces an approval card rather than a silent
  bypass. An upstream addition degrades into an extra prompt, never into a hole.
* **Recursive.** ``batch_execute`` carries arbitrary sub-calls, so classifying
  the outer name would let a whole batch of mutations through as one opaque
  call. The batch is a read only when every command inside it is a read.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

LEDGER_PATH = Path(__file__).with_name("tool_actions.json")

READ = "read"
WRITE = "write"

# Depth limit for batch_execute nesting. A batch inside a batch inside a batch is
# not a real workflow; refusing to recurse further and returning WRITE keeps a
# hand-crafted deep payload from exhausting the stack before the gate ever runs.
_MAX_DEPTH = 8

_ledger_cache: dict[str, Any] | None = None


def load_ledger(*, refresh: bool = False) -> dict[str, Any]:
    """Load and cache the ledger. ``refresh=True`` re-reads from disk (tests)."""
    global _ledger_cache
    if _ledger_cache is None or refresh:
        with LEDGER_PATH.open(encoding="utf-8") as handle:
            _ledger_cache = json.load(handle)
    return _ledger_cache


def ledger_tool_names() -> set[str]:
    """Every tool name the ledger classifies."""
    return set(load_ledger()["tools"].keys())


def tool_entry(tool_name: str) -> dict[str, Any] | None:
    """The ledger row for a tool, or None when it is not classified."""
    return load_ledger()["tools"].get(tool_name)


def _action_list(entry: Mapping[str, Any], key: str) -> tuple[str, ...]:
    """
    An action list from the ledger, or empty when the row is the wrong shape.

    ``action in entry["read_actions"]`` reads naturally and is a trap: if that
    field is a plain string instead of a list, ``in`` silently becomes a
    SUBSTRING test, so ``"list_packages"`` exempts the actions ``"list"``,
    ``"list_p"`` and even ``"s"``. Measured 2026-07-29 during an external audit
    of the ledger's trust assumptions. The ledger is hand-edited data granting
    exemptions from a security gate, so a wrong shape has to land on the write
    side rather than becoming a wildcard.
    """
    value = entry.get(key)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _key_folded(key: str) -> str:
    """
    One name for every spelling that some layer can turn into the same key.

    The layers between this gate and the C# handler rename keys in several ways:
    ``ParamNormalizerMiddleware`` (camelCase -> snake_case), C#
    ``BatchExecute.NormalizeParameterKeys`` (``StringCaseUtility.ToCamelCase``:
    drop each ``_``, upper-case the next letter, later key wins), and the
    ``NormalizeKey`` of ``manage_animation`` / ``manage_vfx`` (``action``
    matched case-insensitively, plus ToCamelCase). Every one of those only
    removes underscores and changes case, so two keys that can meet after any
    of them are equal once underscores are dropped and case is folded. The fold
    is deliberately looser than any single layer: it only decides which keys
    are suspects, and a suspect lands on the write side.
    """
    return key.replace("_", "").lower()


def _properties_container(params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """
    The ``properties`` objects that C# ``manage_animation`` / ``manage_vfx``
    flatten into the top level (``ExtractProperties``: key matched
    case-insensitively, value an object or a JSON string of one).
    """
    found: list[Mapping[str, Any]] = []
    for key, value in params.items():
        if not isinstance(key, str) or _key_folded(key) != "properties":
            continue
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if isinstance(value, Mapping):
            found.append(value)
    return found


def _spellings(params: Mapping[str, Any], param: str) -> list[tuple[bool, Any]]:
    """
    Every ``(is_literal_top_level_key, value)`` pair a layer could read as ``param``.

    Measured 2026-09-25 (external audit): ``{"action": "get", "action_": "call"}``
    was classified by the literal ``action`` while C# batch normalisation
    renamed ``action_`` to ``action`` and let it overwrite the first, so Unity
    ran the write with no approval card.
    """
    target = _key_folded(param)
    out: list[tuple[bool, Any]] = []
    for key, value in params.items():
        if isinstance(key, str) and _key_folded(key) == target:
            out.append((key == param, value))
    for container in _properties_container(params):
        for key, value in container.items():
            if isinstance(key, str) and _key_folded(key) == target:
                out.append((False, value))
    return out


def sole_value(params: Mapping[str, Any], param: str) -> Any:
    """
    ``params[param]`` when that literal key is the only spelling any layer
    could read as ``param``; otherwise None, which callers must treat as
    "unknown" rather than "absent".
    """
    spellings = _spellings(params, param)
    if len(spellings) == 1 and spellings[0][0]:
        return spellings[0][1]
    return None


def _is_read_by_param(rule: Mapping[str, Any], params: Mapping[str, Any]) -> bool:
    """
    Evaluate one ``param_dependent`` rule against a call's parameters.

    Every spelling of the pivot has to agree with the read side: which one a
    layer ends up reading depends on key order and on which normaliser runs, so
    one spelling saying "absent" proves nothing while another carries a value.
    The original hole here was ``manage_scene validate autoRepair=true``, which
    an exact-key lookup read as omitted (external audit 2026-07-29).
    """
    param = rule.get("param")
    if not isinstance(param, str) or not param:
        # A rule with no usable pivot cannot prove anything.
        return False
    when = rule.get("read_when", "omitted")
    values = [value for _, value in _spellings(params, param)]
    if when == "omitted":
        return all(value is None for value in values)
    if when == "falsy":
        return not any(values)
    # An unknown rule kind must not silently read as a permission. Treat it as
    # "cannot prove read" so the call is gated.
    return False


def classify(tool_name: str, params: Mapping[str, Any] | None = None, *, _depth: int = 0) -> str:
    """
    Return ``"read"`` or ``"write"`` for a single tool call.

    ``"write"`` is the answer whenever the call cannot be *proven* harmless:
    unknown tool, unknown action, missing action with no declared default,
    malformed batch payload, or nesting past ``_MAX_DEPTH``.
    """
    params = params or {}
    entry = tool_entry(tool_name)
    if entry is None:
        return WRITE

    # batch_execute and anything else that carries nested calls.
    recursive_field = entry.get("recursive_field")
    if recursive_field:
        if _depth >= _MAX_DEPTH:
            return WRITE
        commands = params.get(recursive_field)
        # A non-list or empty payload proves nothing about what will run.
        if not isinstance(commands, (list, tuple)) or not commands:
            return WRITE
        tool_key = entry.get("recursive_tool_key", "tool")
        params_key = entry.get("recursive_params_key", "params")
        for command in commands:
            if not isinstance(command, Mapping):
                return WRITE
            inner_name = command.get(tool_key)
            if not isinstance(inner_name, str):
                return WRITE
            inner_params = command.get(params_key) or {}
            if not isinstance(inner_params, Mapping):
                return WRITE
            if classify(inner_name, inner_params, _depth=_depth + 1) == WRITE:
                return WRITE
        return READ

    # Tools with no action parameter are classified whole.
    tool_level = entry.get("tool_level")
    if tool_level in (READ, WRITE):
        return tool_level

    action_param = entry.get("action_param")
    if not action_param:
        return WRITE

    spellings = _spellings(params, action_param)
    if len(spellings) > 1:
        # Which spelling wins differs per layer (key order, which normaliser
        # runs), so no single one of them is the action Unity will execute.
        return WRITE
    if not spellings:
        candidates: list[Any] = [None]
    elif spellings[0][0]:
        candidates = [spellings[0][1]]
    else:
        # A renamed or nested spelling: some layers read it as the action,
        # others ignore it and fall back to the default. Both must be reads.
        candidates = [spellings[0][1], None]
    for action in candidates:
        if _classify_action(entry, action, params) == WRITE:
            return WRITE
    return READ


def _classify_action(entry: Mapping[str, Any], action: Any, params: Mapping[str, Any]) -> str:
    """Classify one candidate value of the action parameter."""
    if action is None:
        action = entry.get("default_action")
    if not isinstance(action, str):
        return WRITE

    # Case is normalised because the TOOLS normalise it. Measured 31 Jul 2026:
    # `manage_packages` accepts `action="LIST_PACKAGES"` and lowercases it
    # internally, so the call runs as the read-only `list_packages` while the
    # classifier saw an unknown action and answered WRITE. That direction is
    # fail-closed - a read gets a card, nothing leaks - but it is still wrong,
    # and being wrong in the safe direction is how a gate trains people to
    # click through. Normalising here keeps the classifier's answer aligned
    # with what the tool actually does.
    #
    # Only case is folded, deliberately: the ledger's keys are exact action
    # names, and matching anything looser (prefixes, separators) would let an
    # unknown action borrow a known one's verdict - the failure direction that
    # actually leaks.
    action = action.lower()

    # Parameter-dependent actions are checked first: they appear in neither
    # read_actions nor write_actions, because the action name alone does not
    # determine the answer.
    for rule in entry.get("param_dependent", []):
        if rule.get("action") == action:
            return READ if _is_read_by_param(rule, params) else WRITE

    if action in _action_list(entry, "read_actions"):
        return READ
    if action in _action_list(entry, "write_actions"):
        return WRITE
    return WRITE


def nested_tool_names(tool_name: str, params: Mapping[str, Any] | None,
                      *, _depth: int = 0) -> list[str] | None:
    """
    Every sub-call name a nested-call tool (``batch_execute``) could dispatch,
    recursively; ``[]`` for a tool that carries no nested calls.

    ``classify`` can read the batch literally because it fails closed: a
    spelling it misses lands on the write side and still gets a card. A caller
    that ALLOWS or REFUSES by name (the URL tool profiles) has no such fallback,
    so here the command list, the tool key and the params key are matched with
    the collision fold (``_key_folded``): a spelling any layer might rename
    into the real key is included. Names themselves are exact, because both
    Python ``batch_execute`` and C# ``CommandRegistry`` look them up exactly.

    Returns None when nesting goes past ``_MAX_DEPTH``: the names cannot all be
    known, and a caller deciding by name must refuse.
    """
    entry = tool_entry(tool_name)
    recursive_field = entry.get("recursive_field") if entry else None
    if not recursive_field or not isinstance(params, Mapping):
        return []
    if _depth >= _MAX_DEPTH:
        return None
    tool_key = _key_folded(entry.get("recursive_tool_key", "tool"))
    params_key = _key_folded(entry.get("recursive_params_key", "params"))
    commands_key = _key_folded(recursive_field)
    names: list[str] = []
    for key, commands in params.items():
        if not (isinstance(key, str) and _key_folded(key) == commands_key):
            continue
        if not isinstance(commands, (list, tuple)):
            continue
        for command in commands:
            if not isinstance(command, Mapping):
                continue
            inner_names = [v for k, v in command.items()
                           if isinstance(k, str) and _key_folded(k) == tool_key
                           and isinstance(v, str)]
            inner_params = [v for k, v in command.items()
                            if isinstance(k, str) and _key_folded(k) == params_key
                            and isinstance(v, Mapping)]
            for inner_name in inner_names:
                names.append(inner_name)
                for inner in inner_params or [{}]:
                    deeper = nested_tool_names(inner_name, inner, _depth=_depth + 1)
                    if deeper is None:
                        return None
                    names.extend(deeper)
    return names


def is_read_only(tool_name: str, params: Mapping[str, Any] | None = None) -> bool:
    """Convenience wrapper for gate code that only wants a boolean."""
    return classify(tool_name, params) == READ


def _self_check() -> list[str]:
    """
    Internal consistency of the ledger itself, independent of the live registry.

    The registry cross-check (does every registered tool appear here, and does
    every declared action still exist upstream) lives in
    ``tests/test_tool_actions_ledger.py`` because it needs to import the tools.
    """
    problems: list[str] = []
    ledger = load_ledger(refresh=True)
    for name, entry in ledger["tools"].items():
        # Shape first. classify() already fails closed on a wrong shape, but a
        # silently degraded row is a ledger nobody notices is broken - and the
        # string-instead-of-list case turns membership into a substring match.
        for key in ("read_actions", "write_actions"):
            value = entry.get(key, [])
            if not isinstance(value, (list, tuple)):
                problems.append(
                    f"{name}: {key} is {type(value).__name__}, expected a list -- "
                    "membership would degrade to a substring test"
                )
            elif any(not isinstance(item, str) for item in value):
                problems.append(f"{name}: {key} contains a non-string entry")
        for rule in entry.get("param_dependent", []):
            if not isinstance(rule.get("param"), str) or not rule.get("param"):
                problems.append(f"{name}: a param_dependent rule has no usable 'param'")

        reads = set(_action_list(entry, "read_actions"))
        writes = set(_action_list(entry, "write_actions"))
        overlap = reads & writes
        if overlap:
            problems.append(f"{name}: action in both read and write: {sorted(overlap)}")

        has_actions = bool(reads or writes or entry.get("param_dependent"))
        if entry.get("action_param") and not has_actions:
            problems.append(f"{name}: declares action_param but classifies no actions")
        if not entry.get("action_param") and entry.get("tool_level") not in (READ, WRITE):
            problems.append(f"{name}: no action_param and no tool_level -- unclassifiable")
        if entry.get("action_param") and entry.get("tool_level") is not None:
            problems.append(f"{name}: has both action_param and tool_level -- ambiguous")

        for rule in entry.get("param_dependent", []):
            action = rule.get("action")
            if action in reads or action in writes:
                problems.append(
                    f"{name}: '{action}' is param-dependent but also listed as a fixed action"
                )
            if rule.get("read_when") not in ("omitted", "falsy"):
                problems.append(f"{name}: '{action}' has unsupported read_when {rule.get('read_when')!r}")

        default_action = entry.get("default_action")
        if default_action is not None and default_action not in reads | writes:
            problems.append(f"{name}: default_action '{default_action}' is not a declared action")

        if not entry.get("evidence"):
            problems.append(f"{name}: no evidence reference")
    return problems
