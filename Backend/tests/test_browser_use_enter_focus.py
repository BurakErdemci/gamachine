"""Enter is gated on the element that has focus, never on the last element the agent touched."""
import asyncio

from browser_use import gateway
from browser_use.gateway import BrowserGateway

RUNTIME = {"running": True, "port": 4711, "token": "ab" * 32}
STATE = {"enabled": True, "halted": False, "runtime": RUNTIME}
SEARCH = {"role": "searchbox", "name": "Search", "tag": "input", "type": "search",
          "submits_form": True, "form_method": "get", "form_action": "https://shop.example/s"}
PAY = {"role": "button", "name": "Pay now", "tag": "button", "type": "submit",
       "submits_form": True, "form_method": "post", "form_action": "https://shop.example/pay"}


class Host:
    def __init__(self, focused):
        self.focused = focused
        self.cmds = []
        self.cards = []

    async def send(self, runtime, op, conversation_id, args):
        self.cmds.append((op, dict(args)))
        if op == "describe":
            if args.get("focused") is not True or self.focused is None:
                return {"ok": False, "error": "focus_in_frame"}
            return {"ok": True, "element": self.focused, "url": "https://shop.example/cart"}
        return {"ok": True}

    def open_card(self, conversation_id, tool, params):
        self.cards.append((tool, params))
        return {"status": "ok", "gate_id": "g1"}

    def calls(self, *steps):
        handler = BrowserGateway(self.send, self.open_card, lambda gate_id: None, now=iter(range(0, 10**6, 5)).__next__)

        async def go():
            return [await handler.call(1, op, args, lambda: STATE) for op, args in steps]
        return asyncio.run(go())


def test_enter_after_tab_describes_the_focused_element_and_cards_a_post_submit_button():
    host = Host(PAY)
    replies = host.calls(("click", {"snapshot_id": "s", "ref": "e1"}), ("press", {"key": "Tab"}),
                         ("press", {"key": "Enter"}))
    assert replies[-1].get("needs_approval") is True
    assert ("describe", {"focused": True}) in host.cmds
    assert [tool for tool, _ in host.cards] == ["browser_press"]
    assert ("press", {"key": "Enter"}) not in host.cmds


def test_enter_on_a_focused_get_search_field_runs():
    host = Host(SEARCH)
    reply = host.calls(("press", {"key": "Enter"}))[0]
    assert reply["ok"] is True and host.cards == []
    assert host.cmds == [("describe", {"focused": True}), ("press", {"key": "Enter"})]


def test_an_agent_supplied_ref_is_not_described():
    host = Host(SEARCH)
    host.calls(("press", {"key": "enter", "snapshot_id": "s1", "ref": "e9"}))
    assert host.cmds[0] == ("describe", {"focused": True})


def test_describe_failure_cards_the_enter():
    host = Host(None)
    reply = host.calls(("press", {"key": "Enter"}))[0]
    assert reply.get("needs_approval") is True
    assert "no known target" in host.cards[0][1]["risk_reason"]


def test_other_keys_are_not_described():
    host = Host(PAY)
    assert host.calls(("press", {"key": "Tab"}))[0]["ok"] is True
    assert [op for op, _ in host.cmds] == ["press"]


def test_last_target_bookkeeping_is_gone():
    assert not hasattr(BrowserGateway(None, None, None), "_last_target")
    assert "_last_target" not in open(gateway.__file__, encoding="utf-8").read()
