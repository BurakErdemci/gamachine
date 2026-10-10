"""An agent's separate text blocks reach the chat as separate paragraphs.

Measured 10 Oct 2026: a five-hour Claude run showed every progress note glued to
the next ("...durumuna bakıyorum.Git deposu açıldı."), live and in the stored
answer, because each provider appended text blocks back to back.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from providers.text_blocks import block_break  # noqa: E402


def test_block_break_rules():
    assert block_break("") == ""
    assert block_break("Done.") == "\n\n"
    assert block_break("Done.\n") == "\n"
    assert block_break("Done.\n\n") == ""


# ── Claude ────────────────────────────────────────────────────────────────
def _claude():
    from providers.claude_sdk_session import ClaudeSDKSession

    sess = ClaudeSDKSession(conversation_id=9101)
    sess._begin_turn()
    sess._out_q = asyncio.Queue()
    return sess


def _texts(q: asyncio.Queue) -> str:
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev and ev.get("type") == "text":
            out.append(ev["content"])
    return "".join(out)


def _assistant(*blocks):
    from claude_agent_sdk import AssistantMessage
    return AssistantMessage(content=list(blocks), model="claude-opus-5")


def _stream(event: dict):
    from claude_agent_sdk import StreamEvent
    return StreamEvent(uuid="u", session_id="s", event=event)


def test_claude_whole_blocks_are_separated_live_and_in_the_answer():
    from claude_agent_sdk import TextBlock, ToolUseBlock

    sess = _claude()

    async def run():
        await sess._on_message(_assistant(TextBlock("Reading the doc."),
                                          ToolUseBlock(id="t1", name="Read", input={"file_path": "GDD.md"})))
        await sess._on_message(_assistant(TextBlock("The repo is open.")))

    asyncio.run(run())
    assert sess._final_text == "Reading the doc.\n\nThe repo is open."
    assert _texts(sess._out_q) == "Reading the doc.\n\nThe repo is open."


def test_claude_streamed_blocks_are_separated_live_and_in_the_answer():
    from claude_agent_sdk import TextBlock

    sess = _claude()

    async def message(text):
        await sess._on_message(_stream({"type": "message_start"}))
        await sess._on_message(_stream({"type": "content_block_start", "index": 0,
                                        "content_block": {"type": "text", "text": ""}}))
        await sess._on_message(_stream({"type": "content_block_delta", "index": 0,
                                        "delta": {"type": "text_delta", "text": text}}))
        await sess._on_message(_stream({"type": "content_block_stop", "index": 0}))
        await sess._on_message(_assistant(TextBlock(text)))

    async def run():
        await message("Reading the doc.")
        await message("The repo is open.")

    asyncio.run(run())
    assert _texts(sess._out_q) == "Reading the doc.\n\nThe repo is open."
    assert sess._final_text == "Reading the doc.\n\nThe repo is open."


def test_claude_a_single_block_gets_no_leading_break():
    from claude_agent_sdk import TextBlock

    sess = _claude()
    asyncio.run(sess._on_message(_assistant(TextBlock("Only answer."))))
    assert sess._final_text == "Only answer."


# ── Codex ─────────────────────────────────────────────────────────────────
def test_codex_agent_message_items_are_separated():
    from providers.codex_session import CodexSession

    sess = CodexSession(9102, model="gpt-test", cwd=os.path.dirname(__file__))
    sess._started = True
    sess.thread_id = "thread-1"
    q: asyncio.Queue = asyncio.Queue()
    sess._out_q = q

    async def run():
        for item, text in [("m1", "Reading "), ("m1", "the doc."), ("m2", "The repo is open.")]:
            await sess._handle_notification({"method": "item/agentMessage/delta",
                                             "params": {"itemId": item, "delta": text}})

    asyncio.run(run())
    assert sess._final_text == "Reading the doc.\n\nThe repo is open."
    assert _texts(q) == "Reading the doc.\n\nThe repo is open."
