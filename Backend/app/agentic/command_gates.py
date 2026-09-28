"""
Komut onay gate'leri — AgentRunner ve CLIProvider tarafından ortaklaşa kullanılır.
Circular import olmadan her iki modülün de import edebileceği paylaşılan store.
"""
import asyncio
from typing import Dict, Optional

from agentic import cards

# Onay kapısının kullanıcı cevabını bekleme süresi — TEK KAYNAK.
# Ölçüldü (2026-07-28): `agent_runner` üç ayrı yerde 60.0 ile beklerken
# `claude_sdk_session` ve `codex_session` 300.0 ile bekliyordu. 60 sn dolunca
# backend `approved=False` yapıp gate'i siliyordu → 90. saniyede "Onayla" diyen
# kullanıcının onayı SESSİZCE reddedilmiş oluyordu (hata yok, kart kaybolmuyor).
# Arızanın şekli "uyuşması gereken N yer", o yüzden literal tekrar EDİLMEZ.
# Evi burası: gate store'unu zaten hem AgentRunner hem sağlayıcılar buradan
# alıyor ve bu modül circular-import'suz olacak şekilde tasarlandı — sabiti
# `agent_runner`'da tutmak sağlayıcıdan agentic'e yeni bir import yönü açıyordu.
APPROVAL_TIMEOUT_S = 300.0

# gate_id → asyncio.Event
APPROVAL_GATES: Dict[str, asyncio.Event] = {}
# gate_id → bool (True=onaylandı, False=reddedildi)
APPROVAL_RESULTS: Dict[str, bool] = {}
GATE_OWNERS: Dict[str, int] = {}

# AskUserQuestion (Claude SDK) için soru gate'leri — Opus kullanıcıya A/B/C soruyor,
# frontend seçimi /question-answer ile bildirir, can_use_tool callback'i burada bekler.
# gate_id → asyncio.Event
QUESTION_GATES: Dict[str, asyncio.Event] = {}
# gate_id → dict (soru metni → seçilen label(lar))
QUESTION_RESULTS: Dict[str, dict] = {}

# ── Gate lifecycle: the single entry and exit point ──────────────────────────
# A gate's event and its owner are written in the SAME call. Measured reason:
# four creation sites forgot the owner across two audit rounds (agent_runner,
# the Claude approval and question gates, the Codex approval, the direct MCP
# request) and none of them crashed. `_wake_blocked` counts an unowned gate as
# blocking EVERY conversation - the safe direction - so the only symptom was
# AUTO-WAKE silently dying for everyone. The owner is therefore a required
# argument: "unknown" is a decision someone wrote down, not a line they forgot.
UNKNOWN_OWNER: Optional[int] = None


def _in_process_resolver(gate_id: str, kind: str):
    """What an answer does to an in-process gate: the waiter's result, then wake."""
    def resolve(result) -> None:
        if kind == "question":
            event = QUESTION_GATES.get(gate_id)
            if event is not None:
                QUESTION_RESULTS[gate_id] = result
                event.set()
            return
        event = APPROVAL_GATES.get(gate_id)
        if event is not None:
            APPROVAL_RESULTS[gate_id] = result is True
            event.set()
    return resolve


def register_gate(
    gate_id: str, owner: Optional[int], kind: str = "approval", *,
    tool: Optional[str] = None, summary=None, params=None, risk: Optional[str] = None,
    questions=None,
) -> Optional[asyncio.Event]:
    """Create a gate and record its owner together; returns the event to wait on.

    kind="external" records ownership only. The MCP approval cards keep their
    own pending record in `conversation_routes` and have no event to wait on,
    but they still need an owner so Stop and AUTO-WAKE can tell them apart;
    that module registers their card itself.

    tool / summary / params / risk describe the card for the all-cards list
    (`agentic.cards`); params are only hashed there.
    """
    event: Optional[asyncio.Event] = None
    if kind == "question":
        event = asyncio.Event()
        QUESTION_GATES[gate_id] = event
    elif kind != "external":
        event = asyncio.Event()
        APPROVAL_GATES[gate_id] = event
        # Fail-closed seed: a waiter released without a decision (session
        # close, Stop) reads a rejection, never an approval.
        APPROVAL_RESULTS[gate_id] = False
    if owner is None:
        GATE_OWNERS.pop(gate_id, None)
    else:
        GATE_OWNERS[gate_id] = owner
    if kind != "external":
        cards.open_card(gate_id, conversation_id=owner,
                        kind="question" if kind == "question" else "command",
                        tool=tool, summary=summary, params=params, risk=risk,
                        resolver=_in_process_resolver(gate_id, kind), questions=questions)
    return event


def mark_timed_out(gate_id: str) -> None:
    """The waiter gave up; call before `release_gate`, which would say cancelled."""
    cards.close_card(gate_id, "timed_out")


def cancel_gate(gate_id: str) -> None:
    """Stop: an open gate is released as a rejection. A gate someone already
    answered keeps that answer - Stop does not overwrite the winner."""
    if not cards.close_card(gate_id, "cancelled") and cards.get(gate_id) is not None:
        return
    if gate_id in APPROVAL_GATES:
        APPROVAL_RESULTS[gate_id] = False
        APPROVAL_GATES[gate_id].set()
    if gate_id in QUESTION_GATES:
        QUESTION_RESULTS[gate_id] = None
        QUESTION_GATES[gate_id].set()


def release_gate(gate_id: str, wake: bool = False) -> None:
    """Drop a gate's event, its result and its owner in one call.

    wake=True sets the event before dropping it, so a caller still blocked in
    `wait_for` returns at once instead of sitting out APPROVAL_TIMEOUT_S; the
    result is dropped with the gate, so that waiter reads "no decision".

    A card still open here was neither answered nor timed out (session closed,
    stream closed, chat deleted), so it is ledgered as cancelled.
    """
    cards.close_card(gate_id, "cancelled")
    event = APPROVAL_GATES.pop(gate_id, None)
    question_event = QUESTION_GATES.pop(gate_id, None)
    APPROVAL_RESULTS.pop(gate_id, None)
    QUESTION_RESULTS.pop(gate_id, None)
    GATE_OWNERS.pop(gate_id, None)
    if wake:
        for ev in (event, question_event):
            if ev is not None:
                ev.set()
