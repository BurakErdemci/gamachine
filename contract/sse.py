"""Server-sent events: parse the backend's stream, normalize it for golden files, check its invariants."""
import json

TERMINAL = ("done", "error")
# The one frame the backend sends after the terminal event: the context gauge.
AFTER_TERMINAL = ("context_usage",)
# Heartbeats depend on timing, not on the turn; golden files leave them out.
VOLATILE_TYPES = ("status",)
VOLATILE_KEYS = {
    "id", "conversation_id", "message_id", "turn_id", "seq", "ts", "timestamp", "created_at",
    "updated_at", "duration_ms", "elapsed_ms", "cost_usd", "input_tokens", "output_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens", "tokens", "total_tokens",
}


def parse(lines):
    """Yield one dict per `data:` frame; `lines` is any iterable of text lines without line ends."""
    data = []
    for line in lines:
        if line == "":
            if data:
                yield json.loads("\n".join(data))
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        # `event:`, `id:`, `retry:` and `:` comments carry nothing this backend uses.
    if data:
        yield json.loads("\n".join(data))


def _placeholder(key, value):
    if key in VOLATILE_KEYS and value is not None:
        return f"<{key}>"
    if isinstance(value, dict):
        return {k: _placeholder(k, v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_placeholder(key, v) for v in value]
    return value


def _provider_error(event):
    """An error event with the provider SDK's own wording replaced: only the backend's prefix stays.

    The text after the first colon comes from the `openai` package (unpinned in requirements.txt),
    so an SDK update would change it with no change in the product.
    """
    prefix, colon, _ = str(event.get("message", "")).partition(":")
    return {**event, "message": f"{prefix}: <provider message>"} if colon else event


def normalize(events):
    """Events with volatile fields replaced by `<key>` placeholders, heartbeats dropped, keys sorted.

    Consecutive `text` events are merged: where the stream cuts the text depends on timing.
    The provider SDK's wording inside `error` messages is replaced by a placeholder.
    """
    merged = []
    for event in events:
        if event.get("type") in VOLATILE_TYPES:
            continue
        if event.get("type") == "text" and merged and merged[-1].get("type") == "text":
            merged[-1] = {**merged[-1], "content": merged[-1].get("content", "") + event.get("content", "")}
            continue
        merged.append(_provider_error(event) if event.get("type") == "error" else dict(event))
    return [_placeholder("", e) for e in merged]


def dumps(events):
    return "".join(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n" for e in events)


def terminal_problems(events):
    """Why the stream breaks the terminal rule, or [] when it holds.

    Exactly one `done` or `error`; after it nothing but the context gauge frame.
    """
    types = [e.get("type") for e in events]
    terminals = [i for i, t in enumerate(types) if t in TERMINAL]
    if len(terminals) != 1:
        return [f"expected exactly one terminal event, got {len(terminals)}: {types}"]
    trailing = [t for t in types[terminals[0] + 1:] if t not in AFTER_TERMINAL]
    if trailing:
        return [f"events after the terminal event: {trailing}"]
    if len(types) - terminals[0] - 1 > len(AFTER_TERMINAL):
        return [f"more than one trailing frame after the terminal event: {types}"]
    return []
