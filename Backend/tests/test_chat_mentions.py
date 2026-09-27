"""`@<id>` chat mentions: the user names another chat by its stable number.

Contract under test: a user turn gets one framing block that resolves each
`@<id>` to the user's own non-side chat (a branch with its parent); any other
id reads "bulunamadı" without a title; the current chat is not a target; the
stored user message stays exactly what the user typed; the tool texts tell the
model to use the mention and never guess between same-titled chats.
"""
import asyncio

import pytest

import routes.conversation_routes as cr
from agentic import mailbox
from tests.test_mailbox import H, _chat, _FakeRunner, env  # noqa: F401  (fixture)


# ── parsing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, ids", [
    ("@12", [12]),
    ("bunu @12 sohbetine yaz", [12]),
    ("(@12), @13. @14! @15? \"@16\" '@17' @18:", [12, 13, 14, 15, 16, 17, 18]),
    ("@12'ye yaz", [12]),
    ("satır\n@12\tve @13", [12, 13]),
    ("mail@12.com", []),
    ("ali.veli@12", []),
    ("@12abc", []),
    ("@12_x", []),
    ("@@12", []),
    ("@12@13", []),
    ("x@12 ve @", []),
    ("@0 ve @007", [7]),
    ("@1234567890", []),
    ("@12 @12 @13 @12", [12, 13]),
    ("", []),
    (None, []),
])
def test_parse_mentions(text, ids):
    assert mailbox.parse_mentions(text) == ids


# Codex mentionaudit, 27 Sep 2026: `@12` quoted in code or a URL resolved to a
# chat title. The renderer's chips follow the same table
# (`__tests__/chat-mentions.test.tsx`, LITERAL_CASES); keep the two identical.
LITERAL_CASES = [
    ("Use the literal code `@12` in the example.", []),
    ("See https://example.invalid/docs/@12 for syntax.", []),
    ("https://a.b/@12 ve @13", [13]),
    ("ftp://h/x,@12", []),
    ("docs/@12", []),
    ("path/to/@12 ve @13", [13]),
    ("`x`@12", [12]),
    ("``a `@12` b`` @13", [13]),
    ("a `b @12", [12]),
    ("```\n@12\n```\n@13", [13]),
    ("~~~py\n@12\n~~~~\n@13", [13]),
    ("   ```\n@12\n``` \n@13", [13]),
    ("```\n@12 unclosed fence", []),
    ("```js `x`\n@12", [12]),
    ("```\n@12\n~~~\n@13", []),
]


@pytest.mark.parametrize("text, ids", LITERAL_CASES)
def test_mentions_in_code_and_urls_are_not_resolved(text, ids):
    assert mailbox.parse_mentions(text) == ids


def test_mention_block_ignores_literal_contexts():
    chats = [{"id": 12, "title": "Private planning chat", "parent_id": None}]
    for text in ("Use the literal code `@12` in the example.",
                 "See https://example.invalid/docs/@12 for syntax."):
        assert mailbox.mention_block(text, 7, chats) == ""
    assert "Private planning chat" in mailbox.mention_block("`@12` yerine @12", 7, chats)


def test_mention_block_is_empty_without_a_mention():
    assert mailbox.mention_block("selam @ herkese a@1", 5, [{"id": 1, "title": "x"}]) == ""


def test_mention_block_caps_and_reports_the_rest():
    text = " ".join(f"@{i}" for i in range(1, 14))
    block = mailbox.mention_block(text, 99, [])
    lines = block.splitlines()
    assert lines[0].startswith("[Gamachine:")
    resolved = [ln for ln in lines if ln.startswith("@")]
    assert len(resolved) == mailbox.MAX_MENTIONS
    assert "@11" not in block and "+3" in block


def test_mention_block_labels_roots_branches_self_and_unknown():
    chats = [{"id": 12, "title": "Selam", "parent_id": None},
             {"id": 15, "title": "UI  dalı\nyeni", "parent_id": 12}]
    block = mailbox.mention_block("@12 @15 @7 @99", 7, chats)
    assert '@12 = sohbet #12 "Selam" (ana sohbet)' in block
    assert '@15 = sohbet #15 "UI dalı yeni" (dal, ana sohbeti #12)' in block
    assert "@7 = bu sohbetin kendisi" in block
    assert "@99 = bulunamadı" in block


# ── the route ────────────────────────────────────────────────────────────────

def _user_turn(client, path, conv_id, message):
    body = {"conversation_id": conv_id, "message": message, "user_id": 1}
    return client.post(path, json=body, headers=H)


@pytest.mark.parametrize("path", ["/chat-stream", "/chat"])
def test_mentions_resolve_in_the_turn_but_not_in_the_stored_message(env, monkeypatch, path):
    _FakeRunner.messages = []
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    here = _chat(env.db, "Selam")
    root = _chat(env.db, "Selam")
    branch = env.db.create_branch(root)["id"]
    env.db.rename_conversation(branch, "UI dalı")
    side = env.db.create_side_chat(root, 1)
    foreign = _chat(env.db, "Gizli başlık", user_id=2)
    typed = f"@{root} ve @{branch} ile konuş; @{side} @{foreign} @{here} @424242 mail@{root}.com"

    assert _user_turn(env.client, path, here, typed).status_code == 200

    turn = _FakeRunner.messages[-1]
    assert turn.startswith(typed + "\n\n[Gamachine:")
    assert f'@{root} = sohbet #{root} "Selam" (ana sohbet)' in turn
    assert f'@{branch} = sohbet #{branch} "UI dalı" (dal, ana sohbeti #{root})' in turn
    assert f"@{side} = bulunamadı" in turn
    assert f"@{foreign} = bulunamadı" in turn
    assert "Gizli başlık" not in turn and "Yan soru" not in turn
    assert f"@{here} = bu sohbetin kendisi" in turn
    assert f'sohbet #{here} "' not in turn
    assert "@424242 = bulunamadı" in turn
    assert turn.count("[Gamachine:") == 1

    users = [m["content"] for m in env.db.get_conversation_messages(here) if m["role"] == "user"]
    assert users == [typed]


@pytest.mark.parametrize("path", ["/chat-stream", "/chat"])
def test_a_message_without_mentions_runs_unchanged(env, monkeypatch, path):
    _FakeRunner.messages = []
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    here = _chat(env.db, "A")
    _chat(env.db, "B")
    assert _user_turn(env.client, path, here, "benim adresim a@b.com, @ işareti").status_code == 200
    assert _FakeRunner.messages[-1] == "benim adresim a@b.com, @ işareti"


def test_the_first_message_title_is_the_typed_text_not_the_framing(env, monkeypatch):
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    here, other = _chat(env.db, "Yeni Sohbet"), _chat(env.db, "B")
    assert _user_turn(env.client, "/chat-stream", here, f"@{other} selam").status_code == 200
    assert env.db.get_conversation_title(here) == f"@{other} selam"


def test_a_mail_wake_turn_gets_no_mention_block(env, monkeypatch):
    from agentic import approval_mode, wake_queue
    from tests.test_mailbox import _send, _wake_turn
    monkeypatch.setattr(approval_mode, "is_auto", lambda: True)
    _FakeRunner.messages = []
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    assert _send(env.client, a, b, body=f"@{a} diyor ki").status_code == 200
    wake_queue.issue_ticket(b, wake_queue.drain(b))
    assert _wake_turn(env.client, b).status_code == 200
    assert "[Gamachine: kullanıcının" not in _FakeRunner.messages[-1]


# ── what the model is told ───────────────────────────────────────────────────

def test_chat_list_names_a_branch_parent_and_warns_on_shared_titles(env):
    a, b = _chat(env.db, "Selam"), _chat(env.db, "selam")
    branch = env.db.create_branch(b)["id"]
    r = env.client.get("/mailbox/chats", headers=H)
    text = r.json()["text"]
    assert f"(dal, ana sohbeti #{b})" in text
    assert "tahmin etme" in text and "`@`" in text
    listed = {c["id"]: c for c in r.json()["chats"]}
    assert listed[branch]["parent_id"] == b and listed[a]["parent_id"] is None


def test_chat_list_without_shared_titles_has_no_warning():
    text = mailbox.format_chat_list([{"id": 1, "title": "A"}, {"id": 2, "title": "B"}])
    assert "tahmin etme" not in text


def test_every_carrier_tells_the_model_to_use_the_mention_and_not_guess():
    from tools import tool_registry
    from unity_ai_mcp.mail_server import create_server as mail_server
    from unity_ai_mcp.server import create_server as unityai_server

    async def descriptions(server):
        return {t.name: t.description for t in await server.list_tools()
                if t.name in (mailbox.TOOL_LIST, mailbox.TOOL_SEND)}

    carriers = [asyncio.run(descriptions(mail_server())),
                asyncio.run(descriptions(unityai_server(".")))]
    carriers.append({t["name"]: t["description"] for t in tool_registry.TOOL_DEFINITIONS
                     if t["name"] in (mailbox.TOOL_LIST, mailbox.TOOL_SEND)})
    for described in carriers:
        assert set(described) == {mailbox.TOOL_LIST, mailbox.TOOL_SEND}
        assert "`@<numara>`" in described[mailbox.TOOL_LIST]
        assert "tahmin etme" in described[mailbox.TOOL_LIST]
        assert "Dallar" in described[mailbox.TOOL_LIST]
        assert "`@`" in described[mailbox.TOOL_SEND]
