"""Agent-browser gateway against a fake host and fake cards."""
import asyncio
import hashlib
import json
import time

import pytest

from browser_use import gateway, policy
from browser_use.gateway import BrowserGateway

RUNTIME = {"running": True, "port": 4711, "token": "ab" * 32}
STATE = {"enabled": True, "halted": False, "runtime": RUNTIME}
PAGE = "https://shop.example/cart"


class Fake:
    """answer: what an opened card resolves to. A dict is the immediate decision of the
    card function (a consumed grant or a denial); "wait" opens a card nobody answers;
    ("answer", result) opens a card that is answered during the wait."""

    def __init__(self, elements=None, state=None, answer=None):
        self.state = state if state is not None else STATE
        self.elements = elements or {}
        self.cmds = []
        self.cards = []
        self.answer = answer if answer is not None else {"status": "resolved", "approved": True}
        self.polls = 0

    async def host(self, runtime, op, conversation_id, args):
        assert runtime == RUNTIME
        self.cmds.append((op, conversation_id, args))
        if op == "describe":
            element = self.elements.get(args["ref"])
            if element is None:
                return {"ok": False, "error": "stale_snapshot", "url": PAGE}
            return {"ok": True, "url": PAGE, "title": "t", "element": element}
        if op == "screenshot":
            return {"ok": True, "url": PAGE, "title": "t", "image": "aGVsbG8="}
        return {"ok": True, "url": PAGE, "title": "t"}

    def open_card(self, conversation_id, tool, params):
        self.cards.append((conversation_id, tool, params))
        if isinstance(self.answer, dict):
            return self.answer
        return {"status": "ok", "gate_id": "g1"}

    def wait_card(self, gate_id):
        assert gate_id == "g1"
        self.polls += 1
        if isinstance(self.answer, tuple) and self.polls >= 3:
            return self.answer[1]
        return None

    def gateway(self):
        return BrowserGateway(self.host, self.open_card, self.wait_card)

    def ops(self):
        return [cmd[0] for cmd in self.cmds]


@pytest.fixture(autouse=True)
def short_wait(monkeypatch):
    monkeypatch.setattr(gateway, "APPROVAL_WAIT_S", 0.3)


def run(fake, op, args, handler=None):
    return asyncio.run((handler or fake.gateway()).call(3, op, args, lambda: fake.state))


def run_many(fake, *steps):
    handler = fake.gateway()

    async def go():
        return [await handler.call(3, op, args, lambda: fake.state) for op, args in steps]
    return asyncio.run(go())


BUY = {"role": "button", "name": "Buy now", "tag": "button"}
MORE = {"role": "button", "name": "Show more", "tag": "button"}
PASSWORD = {"role": "textbox", "name": "Password", "tag": "input", "type": "password", "is_password": True}
SEARCH_FORM = {"role": "textbox", "name": "Search", "tag": "input", "form_method": "get"}
POST_FORM = {"role": "textbox", "name": "Email", "tag": "input", "form_method": "post"}


def test_allowed_ops_pass_straight_to_the_host_with_the_chat_id():
    fake = Fake({"7": MORE})
    assert run(fake, "navigate", {"url": "https://opengameart.org/"})["ok"] is True
    assert run(fake, "snapshot", {})["ok"] is True
    reply = run(fake, "click", {"snapshot_id": "s1", "ref": "7"})
    assert reply["ok"] is True and json.loads(reply["text"])["url"] == PAGE
    assert fake.ops() == ["navigate", "snapshot", "describe", "click"]
    assert all(cmd[1] == 3 for cmd in fake.cmds)
    assert fake.cmds[-1][2] == {"snapshot_id": "s1", "ref": "7"}
    assert fake.cards == []


def test_only_the_documented_args_reach_the_host():
    fake = Fake({"7": MORE})
    run(fake, "click", {"snapshot_id": "s1", "ref": "7", "force": True})
    run(fake, "scroll", {"direction": "down", "amount": 2, "x": 1})
    assert fake.cmds[-2][2] == {"snapshot_id": "s1", "ref": "7"}
    assert fake.cmds[-1][2] == {"direction": "down", "amount": 2}


@pytest.mark.parametrize("op", ["describe", "halt", "eval", "", "cmd"])
def test_unknown_ops_never_reach_the_host(op):
    fake = Fake()
    reply = run(fake, op, {})
    assert reply["ok"] is False and "Unknown browser tool" in reply["error"]
    assert fake.cmds == []


@pytest.mark.parametrize("op,args", [
    ("click", {"snapshot_id": "s1"}), ("click", {"ref": "1"}), ("click", {"snapshot_id": "", "ref": "1"}),
    ("click", {"snapshot_id": "s1", "ref": True}), ("click", {"snapshot_id": 5, "ref": "1"}),
    ("fill", {"snapshot_id": "s1", "ref": "1"}), ("fill", {"snapshot_id": "s1", "ref": "1", "value": 5}),
    ("select", {"snapshot_id": "s1", "ref": "1", "option": None}), ("press", {}), ("press", {"key": 13}),
    ("snapshot", "junk"),
])
def test_malformed_args_are_refused_before_the_host(op, args):
    fake = Fake({"1": MORE})
    reply = run(fake, op, args)
    assert reply["ok"] is False and "Invalid arguments" in reply["error"]
    assert fake.cmds == []


@pytest.mark.parametrize("url", ["file:///etc/hosts", "http://127.0.0.1:8000/", "http://localhost/",
                                 "javascript:alert(1)", "http://192.168.1.2/", "data:text/html,x"])
def test_refused_urls_never_reach_the_host(url):
    fake = Fake()
    reply = run(fake, "navigate", {"url": url})
    assert reply["ok"] is False and reply["error"].startswith("Refused:")
    assert fake.cmds == []


@pytest.mark.parametrize("state,needle", [
    ({**STATE, "enabled": False}, "turned off"),
    ({**STATE, "runtime": {"running": False, "port": None, "token": None}}, "not running"),
    ({**STATE, "runtime": {"running": True, "port": "80", "token": "x"}}, "not running"),
    ({**STATE, "halted": True}, "stopped the browser"),
    ("junk", "turned off"),
])
def test_unusable_browser_is_refused_before_any_command(state, needle):
    fake = Fake({"9": BUY}, state=state)
    for op, args in (("snapshot", {}), ("click", {"snapshot_id": "s1", "ref": "9"})):
        reply = run(fake, op, args)
        assert reply["ok"] is False and needle in reply["error"]
    assert fake.cmds == [] and fake.cards == []


def test_critical_click_returns_needs_approval_within_the_wait_and_card_fields_come_from_describe(monkeypatch):
    monkeypatch.setattr(gateway, "APPROVAL_WAIT_S", 2.0)
    fake = Fake({"9": {**BUY, "name": "Buy‮ now\x00"}}, answer="wait")
    started = time.monotonic()
    reply = run(fake, "click", {"snapshot_id": "s1", "ref": "9"})
    assert 2.0 <= time.monotonic() - started < 3.5
    assert reply["ok"] is False and reply["needs_approval"] is True
    message = reply["error"]
    assert message.startswith("needs_approval: ") and "An approval card is open in the chat" in message
    assert "wait for their next message before trying again." in message
    assert "click" not in fake.ops()  # never reached the page
    conversation_id, tool, params = fake.cards[0]
    assert conversation_id == 3 and tool == "browser_click"
    assert params["element_name"] == "Buy now"
    assert params["page_url"] == PAGE
    assert params["value_preview"] == ""
    assert params["risky"] is True and "Buy now" in params["risk_reason"] and "shop.example" in params["risk_reason"]
    assert set(params) == {"element_name", "page_url", "value_preview", "risky", "risk_reason", "fingerprint", "value_hash"}
    assert params["fingerprint"] == policy.fingerprint(fake.elements["9"]) and params["value_hash"] == ""


def test_card_answered_during_the_wait_lets_the_step_run():
    fake = Fake({"9": BUY}, answer=("answer", {"status": "resolved", "approved": True}))
    reply = run(fake, "click", {"snapshot_id": "s1", "ref": "9"})
    assert reply["ok"] is True and fake.ops() == ["describe", "click"] and fake.polls == 3


def test_consumed_grant_lets_the_retry_through_without_waiting():
    fake = Fake({"9": BUY})
    reply = run(fake, "click", {"snapshot_id": "s1", "ref": "9"})
    assert reply["ok"] is True
    assert fake.ops() == ["describe", "click"]
    assert len(fake.cards) == 1 and fake.polls == 0


@pytest.mark.parametrize("answer,error", [
    ({"status": "resolved", "approved": False, "error": "nope"}, "nope"),
    (("answer", {"status": "resolved", "approved": False, "error": "rejected"}), "rejected"),
    ({"status": "resolved", "approved": False}, "did not approve"),
])
def test_rejection_stops_the_step_and_reports_the_reason(answer, error):
    fake = Fake({"9": BUY}, answer=answer)
    reply = run(fake, "click", {"snapshot_id": "s1", "ref": "9"})
    assert reply["ok"] is False and error in reply["error"] and "needs_approval" not in reply
    assert "click" not in fake.ops()


@pytest.mark.parametrize("answer", [
    {"status": "resolved", "approved": "true"}, {"status": "resolved", "approved": 1},
    {"status": "resolved"}, ("answer", {"approved": "yes"}), ("answer", "yes"), "junk",
])
def test_only_a_real_true_approves(answer):
    fake = Fake({"9": BUY}, answer=answer)
    assert run(fake, "click", {"snapshot_id": "s1", "ref": "9"})["ok"] is False
    assert "click" not in fake.ops()


def test_password_fill_needs_a_card_and_masks_the_value():
    fake = Fake({"2": PASSWORD}, answer="wait")
    reply = run(fake, "fill", {"snapshot_id": "s1", "ref": "2", "value": "hunter2"})
    assert reply["error"].startswith("needs_approval")
    params = fake.cards[0][2]
    assert params["value_preview"] == "••••" and "hunter2" not in json.dumps(params)


def test_fill_preview_is_capped_and_search_fill_is_free():
    fake = Fake({"2": {**POST_FORM, "name": "Order notes"}, "5": SEARCH_FORM}, answer="wait")
    run(fake, "fill", {"snapshot_id": "s1", "ref": "2", "value": "x" * 300})
    assert len(fake.cards[0][2]["value_preview"]) == 80
    fake = Fake({"5": SEARCH_FORM})
    assert run(fake, "fill", {"snapshot_id": "s1", "ref": "5", "value": "walk cycle"})["ok"] is True
    assert fake.cards == [] and fake.cmds[-1][2]["value"] == "walk cycle"


def test_value_hash_is_the_sha256_of_the_exact_value():
    fake = Fake({"2": {**POST_FORM, "name": "Order notes"},
                 "4": {"role": "combobox", "name": "Delete after", "tag": "select"}})
    run(fake, "fill", {"snapshot_id": "s1", "ref": "2", "value": "a"})
    run(fake, "fill", {"snapshot_id": "s1", "ref": "2", "value": "b"})
    run(fake, "select", {"snapshot_id": "s1", "ref": "4", "option": "30 days"})
    hashes = [params["value_hash"] for _, _, params in fake.cards]
    assert hashes == [hashlib.sha256(v.encode()).hexdigest() for v in ("a", "b", "30 days")]


def test_select_preview_uses_the_option():
    fake = Fake({"4": {"role": "combobox", "name": "Delete after", "tag": "select"}})
    run(fake, "select", {"snapshot_id": "s1", "ref": "4", "option": "30 days"})
    assert fake.cards[0][2]["value_preview"] == "30 days"


def test_long_element_names_are_capped_for_the_card():
    fake = Fake({"9": {**BUY, "name": "Buy " + "z" * 400}})
    run(fake, "click", {"snapshot_id": "s1", "ref": "9"})
    assert len(fake.cards[0][2]["element_name"]) <= 120


def test_stale_snapshot_on_describe_is_reported_not_run():
    fake = Fake({})
    reply = run(fake, "click", {"snapshot_id": "old", "ref": "1"})
    assert reply["ok"] is False and "stale_snapshot" in reply["error"]
    assert fake.ops() == ["describe"]


def test_focusing_a_post_form_field_is_free_but_enter_in_it_needs_a_card():
    field = {**POST_FORM, "type": "text", "submits_form": True}
    fake = Fake({"6": field})
    assert run(fake, "click", {"snapshot_id": "s1", "ref": "6"})["ok"] is True
    assert fake.cards == []
    run(fake, "press", {"key": "Enter", "snapshot_id": "s1", "ref": "6"})
    assert [tool for _, tool, _ in fake.cards] == ["browser_press"]


def test_enter_in_a_get_search_form_is_free_but_post_form_needs_a_card():
    fake = Fake({"5": SEARCH_FORM})
    assert run(fake, "press", {"key": "Enter", "snapshot_id": "s1", "ref": "5"})["ok"] is True
    assert fake.cmds[-1] == ("press", 3, {"key": "Enter"})  # classification ids are not forwarded
    fake = Fake({"6": POST_FORM})
    run(fake, "press", {"key": "Enter", "snapshot_id": "s1", "ref": "6"})
    assert fake.cards and fake.cards[0][1] == "browser_press"
    assert fake.cards[0][2]["value_hash"] == hashlib.sha256(b"Enter").hexdigest()


def test_enter_with_no_known_target_needs_a_card_but_other_keys_do_not():
    fake = Fake()
    run(fake, "press", {"key": "Enter"})
    assert fake.cards
    fake = Fake()
    assert run(fake, "press", {"key": "PageDown"})["ok"] is True
    assert fake.cards == [] and fake.ops() == ["press"]


def test_enter_after_a_fill_checks_the_filled_field_until_the_page_changes():
    fake = Fake({"6": SEARCH_FORM})
    replies = run_many(fake, ("fill", {"snapshot_id": "s1", "ref": "6", "value": "x"}),
                       ("press", {"key": "Enter"}))
    assert all(r["ok"] for r in replies) and fake.ops() == ["describe", "fill", "describe", "press"]
    fake = Fake({"6": SEARCH_FORM})
    run_many(fake, ("fill", {"snapshot_id": "s1", "ref": "6", "value": "x"}),
             ("navigate", {"url": "https://example.com/"}), ("press", {"key": "Enter"}))
    assert fake.cards and "no known target" in fake.cards[0][2]["risk_reason"]


def test_enter_after_a_post_form_fill_opens_a_submit_card():
    fake = Fake({"6": {**POST_FORM, "name": "Search"}})
    run_many(fake, ("fill", {"snapshot_id": "s1", "ref": "6", "value": "a@b.c"}), ("press", {"key": "Enter"}))
    assert "submits a form" in fake.cards[-1][2]["risk_reason"]


def test_last_target_is_per_chat():
    fake = Fake({"6": SEARCH_FORM})
    handler = fake.gateway()

    async def go():
        await handler.call(3, "fill", {"snapshot_id": "s1", "ref": "6", "value": "x"}, lambda: fake.state)
        return await handler.call(4, "press", {"key": "Enter"}, lambda: fake.state)
    asyncio.run(go())
    assert fake.cards and "no known target" in fake.cards[0][2]["risk_reason"]


def test_screenshot_returns_the_image_apart_from_the_text():
    reply = run(Fake(), "screenshot", {})
    assert reply["ok"] is True and reply["image"] == "aGVsbG8="
    assert "image" not in json.loads(reply["text"])


@pytest.mark.parametrize("broken,needle", [(OSError("refused"), "did not answer"), ("junk", "invalid reply")])
def test_host_failure_is_reported_as_an_error(broken, needle):
    fake = Fake()

    async def host(runtime, op, conversation_id, args):
        if isinstance(broken, Exception):
            raise broken
        return broken
    fake.host = host
    reply = run(fake, "snapshot", {})
    assert reply["ok"] is False and needle in json.loads(reply["text"])["error"]


def test_state_is_read_again_for_every_call():
    fake = Fake()
    reads = []

    def state():
        reads.append(1)
        return fake.state
    handler = fake.gateway()
    asyncio.run(handler.call(3, "snapshot", {}, state))
    asyncio.run(handler.call(3, "snapshot", {}, lambda: {**STATE, "halted": True}))
    assert len(reads) == 1 and fake.ops() == ["snapshot"]


def test_post_cmd_sends_the_bearer_token_to_the_host_port(monkeypatch):
    sent = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    class Client:
        def __init__(self, timeout):
            sent["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers, json):
            sent.update(url=url, headers=headers, json=json)
            return Response()
    monkeypatch.setattr(gateway.httpx, "AsyncClient", Client)
    assert asyncio.run(gateway.post_cmd(RUNTIME, "snapshot", 3, {})) == {"ok": True}
    assert sent["url"] == "http://127.0.0.1:4711/cmd"
    assert sent["headers"] == {"Authorization": f"Bearer {RUNTIME['token']}"}
    assert sent["json"] == {"op": "snapshot", "conversation_id": 3, "args": {}}
