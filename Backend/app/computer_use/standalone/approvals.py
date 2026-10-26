"""Serialized confirmation checks, halt handling and action journal."""
import asyncio

from .. import gate
from . import config


class LocalApprovals:
    def __init__(self, startup_refusal: str | None, hotkey, journal=config.journal_path):
        self.startup_refusal = startup_refusal
        self.hotkey = hotkey
        self.journal = journal
        self._lock = asyncio.Lock()

    def _outcome(self, tool, params, decision, reason=""):
        gate.actions_log_append(self.journal(), {
            "conversation_id": None, "tool": tool,
            "bundle_id": params.get("bundle_id"), "pid": params.get("pid"),
            "decision": decision, "reason": reason,
        })
        approved = decision in {"allow", "confirmed"}
        return {"approved": True} if approved else {"approved": False, "error": reason}

    def preflight(self):
        if self.startup_refusal:
            self._outcome("preflight", {}, "deny", self.startup_refusal)
            return self.startup_refusal
        return None

    async def request(self, tool, params):
        async with self._lock:
            if self.startup_refusal:
                return self._outcome(tool, params, "deny", self.startup_refusal)
            if not self.hotkey.registered:
                return self._outcome(tool, params, "deny",
                                     "Durdurma kısayolu (Ctrl+Alt+Esc) çalışmıyor; bilgisayar kullanımı durduruldu.")
            if self.hotkey.halted_at is not None:
                if params.get("confirmed") is not True:
                    return self._outcome(tool, params, "deny", gate.HALTED_REASON
                                         + " Devam için aynı çağrıyı args.confirm ile tekrarla.")
                self.hotkey.clear()
            decision = gate.decide(tool, params, enabled=True, granted=True)
            if decision.kind == "deny":
                return self._outcome(tool, params, "deny", decision.reason)
            critical = params.get("critical_reason") or (decision.reason if decision.kind == "card" else "")
            if tool == "computer_confirm_risky_step":
                return self._outcome(tool, params, "confirmed", critical)
            if critical and params.get("confirmed") is not True:
                return self._outcome(tool, params, "deny", f"{critical} — bu kritik bir işlem: aynı çağrıyı "
                                     "args.confirm = '<ne yapılacağı>' ile tekrarla; kullanıcıya sorulacak.")
            return self._outcome(tool, params, "confirmed" if critical else "allow", critical)

    async def batch_stop(self, params):
        if self.hotkey.halted_at is not None:
            return "halted"
        if config.load()["enabled"] is not True:
            return "disabled"
        return None
