"""Agent-browser grant scope: one approval covers one element, one page visit and one moment."""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode, approval_policy, cards
from agentic.command_gates import GATE_OWNERS
from browser_use import gateway, settings
from computer_use import settings as computer_settings
from routes import conversation_routes
from routes.conversation_routes import create_conversation_router

SECRET = "browser-use-secret"
UI = {"X-Gamachine-UI-Secret": SECRET}
TOKEN = "ab" * 32
PAGE = "https://shop.example/cart"
BUY = {"role": "button", "name": "Buy now", "tag": "button"}
BUY_LATER = {"role": "button", "name": "Buy later", "tag": "button"}
NOTES = {"role": "textbox", "name": "Order notes", "tag": "input", "form_method": "post"}
MORE = {"role": "button", "name": "Show more", "tag": "button"}
LINK = {"role": "link", "name": "Other page", "tag": "a", "href": "https://shop.example/other"}
SLOW_LINK = {"role": "link", "name": "Slow page", "tag": "a", "href": "https://shop.example/other"}
OTHER = "https://shop.example/other"


class Host:
    """Electron's host as the gateway sees it."""

    def __init__(self):
        self.url = PAGE
        self.elements = {"9": BUY, "8": BUY_LATER, "4": NOTES, "7": MORE, "5": LINK, "6": SLOW_LINK}
        self.focused = NOTES
        self.cmds = []
        self.after_describe = None  # async (conversation_id) -> None, run once a describe is answered

    async def __call__(self, runtime, op, conversation_id, args):
        self.cmds.append((op, conversation_id, args, runtime))
        if op == "click" and args.get("ref") == "5":
            self.url = OTHER  # a plain link: no card, but the page changes
        if op == "click" and args.get("ref") == "6":
            self.url = OTHER  # the host gave up waiting for the load, yet the view moved
            return {"ok": False, "error": "navigation_timeout", "url": self.url}
        if op == "describe":
            element = self.focused if args.get("focused") else self.elements.get(args["ref"])
            if self.after_describe is not None:
                await self.after_describe(conversation_id)
            if element is None:
                return {"ok": False, "error": "stale_snapshot", "url": self.url}
            return {"ok": True, "url": self.url, "title": "t", "element": element}
        return {"ok": True, "url": self.url, "title": "t"}

    def ops(self):
        return [cmd[0] for cmd in self.cmds]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(approval_policy, "conversation_turn_in_flight", lambda cid: cid in (1, 2))
    monkeypatch.setattr(gateway, "APPROVAL_WAIT_S", 0.05)
    host = Host()
    monkeypatch.setattr(gateway, "post_cmd", host)
    rows = {}
    db = MagicMock()
    db.db_path = str(tmp_path / "db.sqlite")
    db.get_setting.side_effect = rows.get
    db.set_setting.side_effect = rows.__setitem__
    db.get_conversation_owner.return_value = 1
    db.get_conversation_parent.return_value = None
    db.get_side_of.return_value = None
    approval_mode.set_ui_secret(SECRET)
    approval_mode.set_mode("auto", source="test")
    settings.set_enabled(db, True)
    cards.reset()
    app = FastAPI()
    router = create_conversation_router(db, {})
    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(db=db, client=client, tmp=tmp_path, host=host, router=router)
    for card in cards.list_pending():
        GATE_OWNERS.pop(card["card_id"], None)
    cards.reset()
    approval_mode.set_ui_secret("")


def runtime_body():
    return {"running": True, "port": 4711, "token": TOKEN}


@pytest.fixture
def live(env):
    assert env.client.post("/browser/runtime", json=runtime_body(), headers=UI).status_code == 200
    return env


def command(env, op="click", ref="9", cid=1, **args):
    if op in {"click", "fill", "select"}:
        args = {"snapshot_id": "s1", "ref": ref, **args}
    return env.client.post("/browser/command", json={
        "tool": f"browser_{op}", "args": args, "conversation_id": cid}).json()


def open_card():
    return cards.list_pending()[-1]["card_id"]


def approve(env, **kwargs):
    assert command(env, **kwargs)["needs_approval"] is True
    assert env.client.post(f"/mcp-approval-respond/{open_card()}", json={"approved": True},
                           headers=UI).status_code == 200


def request_gate(env, gate_id):
    return env.client.post("/mcp-approval-request", json={
        "gate_id": gate_id, "tool": "manage_gameobject", "conversation_id": 1,
        "params": {"action": "create"}, "workspace_path": ""})


def closure_var(fn, name):
    return fn.__closure__[fn.__code__.co_freevars.index(name)].cell_contents


def browser_store(router, name):
    """A store of the gateway's card function (_browser_card): grants, gate keys, page generation."""
    endpoint = next(route.endpoint for route in router.routes if route.name == "browser_command")
    return closure_var(closure_var(endpoint, "_browser_gateway")._open_card, name)


def test_request_route_cannot_replace_an_open_browser_gate(live):
    assert command(live)["needs_approval"] is True
    gate_id = open_card()
    approval_mode.set_mode("step", source="test")
    assert request_gate(live, gate_id).status_code == 409
    assert cards.is_open(gate_id)
    assert live.client.post(f"/mcp-approval-respond/{gate_id}", json={"approved": True}).status_code == 403


def test_request_route_never_makes_browser_gates_or_reuses_ids(live):
    approval_mode.set_mode("step", source="test")
    assert request_gate(live, "browser-abc").status_code == 409
    assert request_gate(live, "plain-1").status_code == 200
    assert request_gate(live, "plain-1").status_code == 409
    cards.answer_card("plain-1", "reject", device="desktop")
    assert request_gate(live, "plain-1").status_code == 409


def test_halt_after_the_approval_still_stops_the_step():
    state = {"enabled": True, "halted": False,
             "runtime": {"running": True, "port": 4711, "token": TOKEN}}
    cmds = []

    async def host(runtime, op, conversation_id, args):
        cmds.append(op)
        return {"ok": True, "url": PAGE, "element": BUY}

    def wait_card(gate_id):
        state["halted"] = True  # Stop lands right after the user's approval
        return {"status": "resolved", "approved": True}

    handler = gateway.BrowserGateway(host, lambda cid, tool, params: {"status": "ok", "gate_id": "g"}, wait_card)
    reply = asyncio.run(handler.call(1, "click", {"snapshot_id": "s1", "ref": "9"}, lambda: state))
    assert reply["ok"] is False and reply["error"] == gateway.HALTED
    assert "click" not in cmds


def test_grant_covers_only_the_approved_ref(live):
    approve(live, ref="9")
    assert command(live, ref="8")["needs_approval"] is True
    assert command(live, ref="9")["ok"] is True
    assert live.host.ops().count("click") == 1


def test_grant_covers_only_the_approved_snapshot(live):
    approve(live)
    other = live.client.post("/browser/command", json={
        "tool": "browser_click", "args": {"snapshot_id": "s2", "ref": "9"}, "conversation_id": 1}).json()
    assert other["needs_approval"] is True


@pytest.mark.parametrize("nav", ["navigate", "back", "forward", "reload"])
def test_navigation_drops_the_grant(live, nav):
    approve(live)
    args = {"url": "https://shop.example/other"} if nav == "navigate" else {}
    assert command(live, nav, **args)["ok"] is True
    live.host.url = PAGE
    assert command(live)["needs_approval"] is True
    assert live.host.ops().count("click") == 0


def test_navigation_closes_open_cards(live):
    assert command(live)["needs_approval"] is True
    gate_id = open_card()
    gate_keys = browser_store(live.router, "_browser_gate_keys")
    assert command(live, "reload")["ok"] is True
    assert not gate_keys and not cards.is_open(gate_id)


def test_retry_does_not_attach_to_a_closed_card(live):
    assert command(live)["needs_approval"] is True
    gate_id = open_card()
    # Answered elsewhere (another thread) and not yet delivered: its pending entry is still there.
    pending = browser_store(live.router, "_browser_gate_keys")
    assert gate_id in pending
    cards.close_card(gate_id, "timed_out")
    assert command(live)["needs_approval"] is True
    assert open_card() != gate_id


def grants_of(env):
    return browser_store(env.router, "_browser_grants")


def test_enter_card_approved_after_a_reload_leaves_no_grant(live):
    assert command(live, "press", key="Enter")["needs_approval"] is True
    gate_id = open_card()
    assert command(live, "reload")["ok"] is True
    assert live.client.post(f"/mcp-approval-respond/{gate_id}", json={"approved": True},
                            headers=UI).status_code == 200
    assert not grants_of(live)
    assert command(live, "press", key="Enter")["needs_approval"] is True
    assert live.host.ops().count("press") == 0


def test_click_card_approved_after_navigation_leaves_no_grant(live):
    assert command(live)["needs_approval"] is True
    gate_id = open_card()
    assert command(live, "navigate", url="https://shop.example/other")["ok"] is True
    assert live.client.post(f"/mcp-approval-respond/{gate_id}", json={"approved": True},
                            headers=UI).status_code == 200
    assert not grants_of(live)


def test_approval_without_navigation_still_creates_the_grant(live):
    approve(live)
    assert len(grants_of(live)) == 1
    assert command(live)["ok"] is True


def test_reload_rejects_the_chats_open_browser_card(live):
    assert command(live, "press", key="Enter")["needs_approval"] is True
    gate_id = open_card()
    assert command(live, "reload")["ok"] is True
    assert not cards.is_open(gate_id)
    live.client.post(f"/mcp-approval-respond/{gate_id}", json={"approved": True}, headers=UI)
    assert not grants_of(live)


def test_identical_enter_retry_after_a_reload_opens_a_new_card(live):
    assert command(live, "press", key="Enter")["needs_approval"] is True
    old_gate = open_card()
    assert command(live, "reload")["ok"] is True
    assert command(live, "press", key="Enter")["needs_approval"] is True
    assert open_card() != old_gate and cards.is_open(open_card())
    assert live.host.ops().count("press") == 0


def test_another_chats_reload_closes_this_chats_open_card(live):
    # One browser view is shared by every chat, so chat 1's reload changes chat 2's page too.
    other = live.client.post("/browser/command", json={
        "tool": "browser_press", "args": {"key": "Enter"}, "conversation_id": 2}).json()
    assert other["needs_approval"] is True
    gate_id = open_card()
    assert command(live, "reload")["ok"] is True
    assert not cards.is_open(gate_id)
    assert not browser_store(live.router, "_browser_gate_keys")


# Rule A: one shared page; any page change closes every chat's cards and grants.

def test_a_link_click_that_changes_the_page_closes_every_open_card(live):
    approve(live, ref="8")
    assert command(live, "press", key="Enter")["needs_approval"] is True
    gate_id = open_card()
    assert command(live, ref="5")["ok"] is True
    assert not cards.is_open(gate_id)
    assert not browser_store(live.router, "_browser_grants")
    live.host.url = PAGE
    assert command(live, ref="8")["needs_approval"] is True
    assert live.host.ops().count("click") == 1


def test_a_step_on_the_same_page_keeps_the_open_card(live):
    assert command(live, "press", key="Enter")["needs_approval"] is True
    gate_id = open_card()
    assert command(live, "scroll", direction="down")["ok"] is True
    assert cards.is_open(gate_id)


# Rule B and C: the moment of the approval, driven through the route functions in one event loop.

def endpoints(env):
    return {route.name: route.endpoint for route in env.router.routes}


async def first_open_card():
    for _ in range(400):
        if cards.list_pending():
            return open_card()
        await asyncio.sleep(0.001)
    raise AssertionError("no card opened")


def run_with_answer(env, answer, op="click", **args):
    """The step and, while its card waits, answer(gate_id, endpoints); returns the step's reply."""
    routes = endpoints(env)
    if op == "click":
        args = {"snapshot_id": "s1", "ref": "9", **args}

    async def answer_card():
        await answer(await first_open_card(), routes)

    async def main():
        reply, _ = await asyncio.gather(routes["browser_command"](
            {"tool": f"browser_{op}", "args": args, "conversation_id": 1}, x_session_token=""), answer_card())
        return reply
    return asyncio.run(main())


async def desktop_approve(gate_id, routes):
    await routes["mcp_approval_respond"](gate_id, {"approved": True}, x_session_token="", x_ui_secret=SECRET)


@pytest.fixture
def waiting(live, monkeypatch):
    monkeypatch.setattr(gateway, "APPROVAL_WAIT_S", 2.0)
    monkeypatch.setattr(gateway, "_POLL_S", 0.01)
    return live


def test_an_approval_inside_the_wait_runs_the_step(waiting):
    assert run_with_answer(waiting, desktop_approve)["ok"] is True
    assert waiting.host.ops().count("click") == 1


def test_a_switch_cycle_during_the_wait_refuses_the_approved_step(waiting):
    async def approve_then_cycle(gate_id, routes):
        await desktop_approve(gate_id, routes)
        for on in (False, True):
            await routes["set_browser_settings"]({"enabled": on}, x_session_token="",
                                                 x_ui_secret=SECRET, x_maintenance="")

    reply = run_with_answer(waiting, approve_then_cycle)
    assert reply["ok"] is False and reply["error"] == gateway.PAGE_CHANGED
    assert waiting.host.ops().count("click") == 0


def test_deleting_the_chat_during_the_wait_refuses_the_approved_step(waiting, monkeypatch):
    waiting.db.delete_conversation_family_and_sides.return_value = ([1], [])
    monkeypatch.setattr(conversation_routes, "require_conversation_owner", lambda *args: None)
    closer = next(route.endpoint for route in waiting.router.routes if route.name == "delete_conversation")
    cell = closer.__closure__[closer.__code__.co_freevars.index("_close_conversation_sessions")]
    original = cell.cell_contents

    async def no_sessions(conv_id):
        return None

    async def approve_then_delete(gate_id, routes):
        await desktop_approve(gate_id, routes)
        assert (await routes["delete_conversation"](1, x_session_token=""))["deleted_ids"] == [1]
        waiting.db.get_conversation_owner.return_value = None  # the row is gone, as in the real DB

    cell.cell_contents = no_sessions
    try:
        reply = run_with_answer(waiting, approve_then_delete)
    finally:
        cell.cell_contents = original
    assert reply["ok"] is False
    assert waiting.host.ops().count("click") == 0


def test_a_focus_change_during_the_wait_refuses_the_approved_enter(waiting):
    waiting.host.focused = BUY

    async def move_focus_then_approve(gate_id, routes):
        waiting.host.focused = {"role": "button", "name": "Delete account", "tag": "button"}
        await desktop_approve(gate_id, routes)

    reply = run_with_answer(waiting, move_focus_then_approve, op="press", key="Enter")
    assert reply["ok"] is False and reply["error"] == gateway.FOCUS_CHANGED
    assert waiting.host.ops().count("press") == 0


def test_an_equal_looking_focused_element_needs_its_own_card(live):
    live.host.focused = dict(BUY, element_id="el1")
    approve(live, op="press", key="Enter")
    live.host.focused = dict(BUY, element_id="el2")
    assert command(live, "press", key="Enter")["needs_approval"] is True
    live.host.focused = dict(BUY, element_id="el1")
    assert command(live, "press", key="Enter")["ok"] is True
    assert live.host.ops().count("press") == 1


# Turn 6: every step is checked again right before it is sent (R1-R5).

def test_a_stop_during_the_describe_refuses_a_step_that_needs_no_card(live):
    async def stop(conversation_id):
        await live.router.halt_computer(conversation_id)

    live.host.after_describe = stop
    reply = command(live, ref="7")
    assert reply["ok"] is False and reply["error"] == gateway.HALTED
    assert live.host.ops().count("click") == 0


def test_a_chat_deleted_during_the_describe_sends_nothing(live):
    async def delete(conversation_id):
        live.db.get_conversation_owner.return_value = None

    live.host.after_describe = delete
    reply = command(live, ref="7")
    assert reply["ok"] is False and reply["error"] == gateway.CHAT_DELETED
    assert live.host.ops().count("click") == 0


def test_a_describe_that_sees_another_url_closes_the_open_card(live):
    assert command(live, "press", key="Enter")["needs_approval"] is True
    gate_id = open_card()
    live.host.url = OTHER  # the page moved on its own
    command(live, ref="7", cid=2)
    assert not cards.is_open(gate_id) and not browser_store(live.router, "_browser_gate_keys")


def test_a_failed_step_whose_reply_shows_another_url_closes_the_open_card(live):
    assert command(live, "press", key="Enter")["needs_approval"] is True
    gate_id = open_card()
    assert command(live, ref="6", cid=2)["ok"] is False
    assert not cards.is_open(gate_id)


def test_an_element_changed_during_the_wait_is_not_clicked(waiting):
    async def rename_then_approve(gate_id, routes):
        waiting.host.elements["9"] = {"role": "button", "name": "Delete account", "tag": "button"}
        await desktop_approve(gate_id, routes)

    reply = run_with_answer(waiting, rename_then_approve)
    assert reply["ok"] is False and reply["error"] == gateway.TARGET_CHANGED
    assert waiting.host.ops().count("click") == 0


def test_another_chats_stop_keeps_this_chats_card_and_the_page_generation(live):
    assert command(live, "press", key="Enter")["needs_approval"] is True
    gate_id = open_card()
    generation = browser_store(live.router, "_browser_page_gen")[0]
    asyncio.run(live.router.halt_computer(2))
    assert cards.is_open(gate_id) and browser_store(live.router, "_browser_page_gen")[0] == generation
    # The retry finds that card instead of opening a duplicate.
    assert command(live, "press", key="Enter")["needs_approval"] is True
    assert [card["card_id"] for card in cards.list_pending()] == [gate_id]


def test_a_browser_restart_closes_open_cards_and_drops_grants(live):
    approve(live)
    assert command(live, ref="8")["needs_approval"] is True
    gate_id = open_card()
    restarted = {"running": True, "port": 4712, "token": "cd" * 32}
    assert live.client.post("/browser/runtime", json=restarted, headers=UI).status_code == 200
    assert not cards.is_open(gate_id)
    assert not browser_store(live.router, "_browser_grants") and not browser_store(live.router, "_browser_gate_keys")


def test_a_browser_restart_during_the_wait_refuses_the_approved_step(waiting):
    async def approve_then_restart(gate_id, routes):
        await desktop_approve(gate_id, routes)
        await routes["set_browser_runtime"]({"running": True, "port": 4712, "token": "cd" * 32},
                                            x_session_token="", x_ui_secret=SECRET, x_maintenance="")

    reply = run_with_answer(waiting, approve_then_restart)
    assert reply["ok"] is False and reply["error"] == gateway.PAGE_CHANGED
    assert waiting.host.ops().count("click") == 0


def test_an_href_or_form_action_changed_during_the_wait_is_not_clicked(waiting):
    waiting.host.elements["9"] = {"role": "link", "name": "Get file", "tag": "a", "href": "https://shop.example/a.zip"}

    async def swap_then_approve(gate_id, routes):
        waiting.host.elements["9"] = dict(waiting.host.elements["9"], href="https://evil.example/a.exe")
        await desktop_approve(gate_id, routes)

    reply = run_with_answer(waiting, swap_then_approve)
    assert reply["error"] == gateway.TARGET_CHANGED and waiting.host.ops().count("click") == 0


def test_the_fingerprint_folds_role_and_name_but_keeps_urls_exact():
    from browser_use import policy
    base = {"role": "Button", "name": "Buy  NOW", "form_action": "https://shop.example/pay"}
    assert policy.fingerprint(base) == policy.fingerprint({**base, "role": "button", "name": "buy now"})
    assert policy.fingerprint(base) != policy.fingerprint({**base, "form_action": "https://shop.example/Pay"})
    link = {"role": "link", "name": "Get", "href": "https://shop.example/a"}
    assert policy.fingerprint(link) != policy.fingerprint({**link, "href": "https://shop.example/A"})


def test_a_new_element_id_in_the_wait_is_not_pressed(waiting):
    async def new_document_then_approve(gate_id, routes):
        waiting.host.focused = dict(waiting.host.focused, element_id="d2-el1")
        await desktop_approve(gate_id, routes)

    waiting.host.focused = dict(NOTES, element_id="d1-el1")
    reply = run_with_answer(waiting, new_document_then_approve, op="press", key="Enter")
    assert reply["error"] == gateway.FOCUS_CHANGED and waiting.host.ops().count("press") == 0


def test_the_chats_own_stop_closes_its_card_and_grant_without_a_session(live):
    approve(live)
    assert command(live, ref="8")["needs_approval"] is True
    gate_id = open_card()
    generation = browser_store(live.router, "_browser_page_gen")[0]
    assert asyncio.run(live.router.stop_chat(1)) == {"status": "no_session"}
    assert not cards.is_open(gate_id) and not browser_store(live.router, "_browser_grants")
    assert browser_store(live.router, "_browser_page_gen")[0] == generation


def test_a_stop_that_a_new_message_cleared_still_refuses_the_waiting_step(waiting):
    async def approve_stop_then_clear(gate_id, routes):
        await desktop_approve(gate_id, routes)
        await waiting.router.halt_computer(1)
        browser_store(waiting.router, "_computer_halted").discard(1)

    reply = run_with_answer(waiting, approve_stop_then_clear)
    assert reply["ok"] is False and reply["error"] == gateway.HALTED
    assert waiting.host.ops().count("click") == 0


def test_a_failed_chat_lookup_after_the_describe_refuses_the_step(live):
    async def lookup_breaks(conversation_id):
        live.db.get_conversation_owner.side_effect = RuntimeError("db locked")

    live.host.after_describe = lookup_breaks
    reply = command(live, ref="7")
    assert reply["ok"] is False and "click" not in live.host.ops()


def test_a_pipe_in_the_name_cannot_move_text_into_the_form_action():
    from browser_use import policy
    first = {"role": "button", "name": "Buy now|x", "form_action": "y"}
    second = {"role": "button", "name": "Buy now", "form_action": "x|y"}
    assert policy.fingerprint(first) != policy.fingerprint(second)


def test_a_pipe_shifted_between_name_and_form_action_in_the_wait_is_not_clicked(waiting):
    waiting.host.elements["9"] = {"role": "button", "name": "Buy now|x", "tag": "button", "form_action": "y"}

    async def shift_then_approve(gate_id, routes):
        waiting.host.elements["9"] = {"role": "button", "name": "Buy now", "tag": "button", "form_action": "x|y"}
        await desktop_approve(gate_id, routes)

    reply = run_with_answer(waiting, shift_then_approve)
    assert reply["error"] == gateway.TARGET_CHANGED and waiting.host.ops().count("click") == 0


def test_the_chats_stop_route_refuses_a_call_queued_behind_another_chats_wait(waiting):
    routes = endpoints(waiting)

    async def main():
        other = asyncio.ensure_future(routes["browser_command"](
            {"tool": "browser_click", "args": {"snapshot_id": "s1", "ref": "9"}, "conversation_id": 2},
            x_session_token=""))
        gate_id = await first_open_card()
        queued = asyncio.ensure_future(routes["browser_command"](
            {"tool": "browser_click", "args": {"snapshot_id": "s1", "ref": "7"}, "conversation_id": 1},
            x_session_token=""))
        await asyncio.sleep(0.05)
        await waiting.router.stop_chat(1)
        await desktop_approve(gate_id, routes)
        await other
        return await queued

    reply = asyncio.run(main())
    assert reply["ok"] is False
    assert all(not (cmd[0] == "click" and cmd[1] == 1) for cmd in waiting.host.cmds)


def test_a_new_click_after_the_chats_stop_is_refused_until_the_next_message(live):
    asyncio.run(live.router.stop_chat(1))
    assert 1 in browser_store(live.router, "_computer_halted")
    reply = command(live, ref="7")
    assert reply["ok"] is False and "click" not in live.host.ops()


def halted_ids(env):
    return env.client.get("/computer-use/settings").json()["halted"]


def test_a_stop_for_a_side_chat_or_a_missing_chat_halts_nothing(live):
    live.db.get_side_of.side_effect = lambda cid: 1 if cid == 50 else None
    live.db.get_conversation_owner.side_effect = lambda cid: None if cid == 99 else 1
    for cid in (50, 99):
        assert live.client.post(f"/chat-stop/{cid}").status_code == 200
    assert halted_ids(live) == []
    computer_settings.set_enabled(live.db, True)
    batch = live.client.post("/computer-use/batch-step", json={"params": {}}).json()
    assert batch["stop"] is None


def test_a_stop_whose_chat_lookup_fails_adds_no_halt(live):
    live.db.get_conversation_owner.side_effect = RuntimeError("db locked")
    assert live.client.post("/chat-stop/1").status_code == 200
    assert halted_ids(live) == []


def test_a_call_queued_before_a_stop_is_refused_after_the_next_message_cleared_the_halt(waiting):
    routes = endpoints(waiting)

    async def main():
        other = asyncio.ensure_future(routes["browser_command"](
            {"tool": "browser_click", "args": {"snapshot_id": "s1", "ref": "9"}, "conversation_id": 2},
            x_session_token=""))
        gate_id = await first_open_card()
        queued = asyncio.ensure_future(routes["browser_command"](
            {"tool": "browser_click", "args": {"snapshot_id": "s1", "ref": "7"}, "conversation_id": 1},
            x_session_token=""))
        await asyncio.sleep(0.05)
        await waiting.router.stop_chat(1)
        browser_store(waiting.router, "_computer_halted").discard(1)
        await desktop_approve(gate_id, routes)
        await other
        return await queued

    reply = asyncio.run(main())
    assert reply["ok"] is False and reply["error"] == gateway.HALTED
    assert all(not (cmd[0] == "click" and cmd[1] == 1) for cmd in waiting.host.cmds)


def test_a_wake_request_with_an_expired_ticket_leaves_the_halt(live):
    asyncio.run(live.router.stop_chat(1))
    live.db.add_message.side_effect = RuntimeError("stop here")
    with patch("routes.conversation_routes._check_chat_rate_limit"):
        live.client.post("/chat-stream", json={"conversation_id": 1, "message": "hi", "user_id": 1,
                                               "origin": "wake"}, headers={"X-Session-Token": ""})
    assert live.db.add_message.called
    assert halted_ids(live) == [1]
