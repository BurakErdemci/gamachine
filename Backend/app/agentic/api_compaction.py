"""History and compaction for the API-key agent loop.

The API loop sends the conversation as real provider messages, so it needs
three things the CLI paths get from their own sessions: the stored rows turned
into user/assistant turns, a summariser that runs on the chat's own model, and
the rule for where a long transcript may be cut. They live here so the loop,
its auto-compaction and the manual compaction route use one copy.
"""
from __future__ import annotations

import logging
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# Providers that run on the API-key loop (agent_runner._run_inner dispatch).
API_PROVIDERS = ("google", "anthropic", "openai", "openrouter", "deepseek", "groq",
                 "moonshot", "z-ai", "nvidia")

# Compact before the next request once the last request used this share of the
# context window; the same 85 % the chat's gauge marks as "should compact".
AUTO_COMPACT_SHARE = 0.85
# Assistant exchanges kept verbatim after the summary.
KEEP_EXCHANGES = 3
# Each rendered item is capped for the summariser only; the conversation sent to
# the model is never cut.
_RENDER_CAP = 8000

# The row database.compact_conversation writes at the boundary.
SUMMARY_ROW_PREFIX = "📝 **Sohbet özetlendi.**"
_MAIL_LABEL = "MAIL (another chat's AI, not the user)"


def history_turns(rows: list, drop_last: bool = True) -> List[dict]:
    """Stored rows as alternating `{"role", "text", "id"}` turns.

    Same filters as the CLI handoff transcript (`_build_handoff_context`): wake
    notices and UI rows (role `system`) are left out, mail rows ride as labelled
    user text. The summary row a compaction leaves is left out too: the same
    summary is the chat's stored memory, which the caller sends once. The last
    row is the current input, which the loop sends itself (`drop_last`).
    Consecutive turns of one role are joined: Gemini and Anthropic expect the
    roles to alternate.
    """
    from agentic import mailbox

    rows = list(rows or [])
    if drop_last and rows:
        rows = rows[:-1]
    turns: List[dict] = []
    for row in rows:
        role = (row.get("role") or "").lower()
        text = (row.get("content") or "").strip()
        if not text or text.startswith(SUMMARY_ROW_PREFIX):
            continue
        if role == "system":
            if text.startswith("⌨️") or not mailbox.is_mail_message(text):
                continue
            role, text = "user", f"{_MAIL_LABEL}: {text}"
        elif role != "assistant":
            role = "user"
        if turns and turns[-1]["role"] == role:
            turns[-1]["text"] += "\n\n" + text
            turns[-1]["id"] = row.get("id") or turns[-1]["id"]
        else:
            turns.append({"role": role, "text": text, "id": row.get("id")})
    return turns


def render_turns(turns: List[dict]) -> List[str]:
    return [f"{'USER' if t['role'] == 'user' else 'ASSISTANT'}: {t['text']}" for t in turns]


def cap(text: str) -> str:
    return text if len(text) <= _RENDER_CAP else text[:_RENDER_CAP] + f"… [truncated {len(text) - _RENDER_CAP} chars]"


def summary_prompt(rendered: List[str], prior: str = "", budget_chars: Optional[int] = None) -> str:
    """The summariser's prompt. Oldest items go first when over `budget_chars`."""
    items = [cap(x) for x in rendered]
    dropped = 0
    if budget_chars:
        while len(items) > 1 and sum(len(x) for x in items) > budget_chars:
            items.pop(0)
            dropped += 1
    head = f"[{dropped} older items did not fit and are not shown]\n" if dropped else ""
    prior_block = f"Earlier summary (carry its decisions and details forward):\n{prior}\n\n" if prior else ""
    return (
        "Summarise the conversation below so that work can continue from the summary alone. "
        "Keep: what the user asked for and their latest request, decisions taken, files and "
        "paths touched, commands run and their outcome, errors still open, and the next step. "
        "Write in the language the conversation uses. At most 400 words.\n\n"
        f"{prior_block}CONVERSATION:\n{head}" + "\n\n".join(items) + "\n\nSUMMARY:"
    )


def summary_message(summary: str) -> str:
    return f"[Summary of the earlier conversation]\n{summary}"


def plan_cut(items: list, is_assistant: Callable[[object], bool], start: int = 0,
             keep: int = KEEP_EXCHANGES) -> Optional[int]:
    """Index where the verbatim tail starts, or None when there is nothing old to fold.

    The tail starts at an assistant item that follows a non-assistant one, so
    the summary (a user message) is followed by an assistant turn on every
    provider and no tool call is separated from its result.
    """
    starts = [i for i in range(start + 1, len(items))
              if is_assistant(items[i]) and not is_assistant(items[i - 1])]
    if len(starts) <= keep:
        return None
    return starts[-keep]


async def summarize(provider_type: str, api_key: str, model_name: str, prompt: str) -> str:
    """One plain request to the chat's own model; returns the summary text."""
    from agentic import agent_runner as ar

    if provider_type == "anthropic":
        client = ar.anthropic.AsyncAnthropic(api_key=api_key)
        reply = await client.messages.create(
            model=model_name, max_tokens=ar._anthropic_loop_max_tokens(model_name),
            messages=[{"role": "user", "content": prompt}], timeout=ar._SAGLAYICI_ZAMAN_ASIMI)
        return "".join(getattr(b, "text", "") or "" for b in getattr(reply, "content", None) or []
                       if getattr(b, "type", None) == "text").strip()
    if provider_type == "google":
        client = ar.genai.Client(api_key=api_key)
        reply = await client.aio.models.generate_content(model=model_name, contents=prompt)
        return (getattr(reply, "text", None) or "").strip()
    client = ar.openai.AsyncOpenAI(api_key=api_key, base_url=ar.OPENAI_BASE_URLS.get(provider_type))
    if ar._uses_responses_api(provider_type, model_name):
        reply = await client.responses.create(model=model_name, input=prompt, store=False,
                                              timeout=ar._SAGLAYICI_ZAMAN_ASIMI)
        return (getattr(reply, "output_text", None) or "").strip()
    reply = await client.chat.completions.create(
        model=model_name, messages=[{"role": "user", "content": prompt}],
        timeout=ar._SAGLAYICI_ZAMAN_ASIMI)
    choices = getattr(reply, "choices", None) or []
    return ((choices[0].message.content if choices else "") or "").strip()
