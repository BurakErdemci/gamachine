"""GET/DELETE /conversations/{id}/queue: a chat's pending user messages
(turn_inputs) listed in run order and withdrawn before a turn takes them.
Every runner is the fake of test_subagents; `network_guard` fails a test on
any connection it did not stub."""
import asyncio

import pytest
from fastapi import HTTPException

from agentic import turn_runner
from tests.test_subagents import (DONE, _Runner, _ep, _http_turn, _idle, _main, _rows,  # noqa: F401
                                  env, network_guard)


def _queue(env):
    return _ep(env.router, "/conversations/{conv_id}/queue", "GET")


def _withdraw(env):
    return _ep(env.router, "/conversations/{conv_id}/queue/{input_id}", "DELETE")


async def test_the_queue_lists_only_user_inputs_in_run_order(env):
    cid = _main(env)
    first = env.db.add_turn_input(cid, "user", "ilk", "user")
    env.db.add_turn_input(cid, "system", "[SUB-AGENT RESULT] #1", "subagent")
    second = env.db.add_turn_input(cid, "user", "ikinci", "user")
    got = await _queue(env)(conv_id=cid, x_session_token="")
    assert got["conversation_id"] == cid
    assert [(q["id"], q["text"], q["order"]) for q in got["queue"]] == [(first, "ilk", 1),
                                                                          (second, "ikinci", 2)]
    assert all(isinstance(q["created_at"], str) and q["created_at"] for q in got["queue"])


async def test_withdraw_removes_the_message_and_its_mark_and_refuses_after_a_claim(env):
    cid = _main(env)
    a = env.db.add_turn_input(cid, "user", "geri al", "user")
    b = env.db.add_turn_input(cid, "user", "kalsın", "user")
    result = env.db.add_turn_input(cid, "system", "sonuç", "subagent")
    done = await _withdraw(env)(conv_id=cid, input_id=a, x_session_token="")
    assert done["status"] == "withdrawn" and [q["id"] for q in done["queue"]] == [b]
    assert ("user", "geri al") not in _rows(env.db, cid)
    assert env.db.pending_turn_input_ids(cid) == [b, result]
    # Not a user message: refused, left pending.
    with pytest.raises(HTTPException) as exc:
        await _withdraw(env)(conv_id=cid, input_id=result, x_session_token="")
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "not_user"
    # A turn took it: 409, and the message stays.
    env.db.claim_turn_inputs(cid)
    with pytest.raises(HTTPException) as exc:
        await _withdraw(env)(conv_id=cid, input_id=b, x_session_token="")
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "claimed"
    assert ("user", "kalsın") in _rows(env.db, cid)
    # Gone, or another chat's message: 404.
    for conv, mid in ((cid, a), (_main(env, "Başka"), b)):
        with pytest.raises(HTTPException) as exc:
            await _withdraw(env)(conv_id=conv, input_id=mid, x_session_token="")
        assert exc.value.status_code == 404


async def test_a_withdrawn_message_never_runs(env):
    cid = _main(env)
    _Runner.scripts[cid] = ["hold", ("response", {"content": "ilk cevap"}), DONE]
    user = asyncio.create_task(_http_turn(env, cid, "kullanıcı turu"))
    await asyncio.sleep(0.02)
    assert turn_runner.start_background_turn(cid, "GERI ALINAN", "user") == "queued"
    assert turn_runner.start_background_turn(cid, "KALAN", "user") == "queued"
    listed = (await _queue(env)(conv_id=cid, x_session_token=""))["queue"]
    assert [q["text"] for q in listed] == ["GERI ALINAN", "KALAN"]
    await _withdraw(env)(conv_id=cid, input_id=listed[0]["id"], x_session_token="")
    _Runner.scripts[cid] = None
    _Runner.holds[cid].set()
    await user
    await _idle(cid)
    assert _Runner.of(cid) == ["kullanıcı turu", "KALAN"]
    assert not any("GERI ALINAN" in c for _, c in _rows(env.db, cid))
    assert (await _queue(env)(conv_id=cid, x_session_token=""))["queue"] == []


async def test_the_queue_needs_an_owned_ordinary_chat(env):
    cid = _main(env)
    with pytest.raises(HTTPException) as exc:
        await _queue(env)(conv_id=cid + 999, x_session_token="")
    assert exc.value.status_code == 404
    side = env.db.create_side_chat(cid, 1)
    with pytest.raises(HTTPException) as exc:
        await _queue(env)(conv_id=side, x_session_token="")
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await _withdraw(env)(conv_id=side, input_id=1, x_session_token="")
    assert exc.value.status_code == 400
