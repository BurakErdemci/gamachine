"""Serialized confirmation checks, halt handling and action journal."""
import asyncio

from .. import gate
from . import config


class LocalApprovals:
    accepts_confirm = True

    def __init__(self, startup_refusal: str | None, hotkey, journal=config.journal_path, confirm_available=False):
        self.startup_refusal = startup_refusal
        self.hotkey = hotkey
        self.journal = journal
        self.confirm_available = confirm_available
        self._lock = asyncio.Lock()

    def _outcome(self, tool, params, decision, reason=""):
        record = {
            "conversation_id": None, "tool": tool,
            "bundle_id": params.get("bundle_id"), "pid": params.get("pid"),
            "decision": decision, "reason": reason,
        }
        if decision == "confirmed":
            record["confirm"] = params.get("confirm_text")
        gate.actions_log_append(self.journal(), record)
        approved = decision in {"allow", "confirmed"}
        return {"approved": True} if approved else {"approved": False, "error": reason}

    def preflight(self):
        if self.startup_refusal:
            self._outcome("preflight", {}, "deny", self.startup_refusal)
            return self.startup_refusal
        return None

    async def request(self, tool, params):
        async with self._lock:
            if self.hotkey.halted_at is not None:
                return self._outcome(tool, params, "deny", gate.HALTED_REASON
                                     + " Bilgisayar kullanımını sürdürmek için sunucuyu yeniden başlat (Claude Code: /mcp).")
            if self.startup_refusal:
                return self._outcome(tool, params, "deny", self.startup_refusal)
            if not self.hotkey.registered:
                return self._outcome(tool, params, "deny",
                                     "Durdurma kısayolu (Ctrl+Alt+Esc) çalışmıyor; bilgisayar kullanımı durduruldu.")
            decision = gate.decide(tool, params, enabled=True, granted=True)
            if decision.kind == "deny":
                return self._outcome(tool, params, "deny", decision.reason)
            critical = params.get("critical_reason") or (decision.reason if decision.kind == "card" else "")
            confirmed = params.get("confirmed") is True and self.confirm_available
            if tool == "computer_confirm_risky_step":
                if not self.confirm_available:
                    return self._outcome(tool, params, "deny",
                                         "Onay hook'u kurulu değil: kritik işlemler bu oturumda yapılamaz.")
                return self._outcome(tool, params, "confirmed", critical)
            if critical and not confirmed:
                if not self.confirm_available:
                    return self._outcome(tool, params, "deny",
                                         "Onay hook'u kurulu değil: kritik işlemler bu oturumda yapılamaz.")
                return self._outcome(tool, params, "deny", f"{critical} — bu kritik bir işlem: aynı çağrıyı "
                                     "args.confirm = '<ne yapılacağı>' ile tekrarla; kullanıcıya sorulacak.")
            return self._outcome(tool, params, "confirmed" if critical else "allow", critical)

    async def batch_stop(self, params):
        if self.hotkey.halted_at is not None or not self.hotkey.registered:
            return "halted"
        if config.load()["enabled"] is not True:
            return "disabled"
        return None
