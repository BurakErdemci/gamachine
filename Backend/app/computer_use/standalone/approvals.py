"""Serialized local consent, halt handling and action journal."""
import asyncio
import time

from .. import gate
from . import config, dialog


class LocalApprovals:
    def __init__(self, startup_refusal: str | None, hotkey, ask=dialog.ask,
                 journal=config.journal_path):
        self.startup_refusal = startup_refusal
        self.hotkey = hotkey
        self.ask = ask
        self.journal = journal
        self._lock = asyncio.Lock()
        self._session_grants = set()

    def _outcome(self, tool, params, decision, reason=""):
        gate.actions_log_append(self.journal(), {
            "conversation_id": None, "tool": tool,
            "bundle_id": params.get("bundle_id"), "pid": params.get("pid"),
            "decision": decision, "reason": reason,
        })
        approved = decision in {"allow", "card-approved"}
        return {"approved": True} if approved else {"approved": False, "error": reason}

    async def request(self, tool, params):
        async with self._lock:
            if self.startup_refusal:
                return self._outcome(tool, params, "deny", self.startup_refusal)
            if self.hotkey.halted_at is not None:
                if (time.monotonic() - self.hotkey.halted_at < 5
                        or not await self.ask("Gamachine", "Bilgisayar kullanımı durduruldu. Devam edilsin mi?")):
                    return self._outcome(tool, params, "deny", gate.HALTED_REASON)
                self.hotkey.clear()
            cfg = config.load()
            app_id = params.get("bundle_id")
            grants = {row["bundle_id"].casefold() for row in cfg["grants"]} | self._session_grants
            granted = isinstance(app_id, str) and app_id.casefold() in grants
            decision = gate.decide(tool, params, enabled=cfg["enabled"], granted=granted)
            if decision.kind != "card":
                return self._outcome(tool, params, decision.kind, decision.reason)
            text = (f"{params.get('app_name') or app_id}: {decision.reason}\n\n"
                    f"İşlem: {params.get('summary') or tool}\n\n"
                    + ("Evet = bu uygulamaya sunucu kapanana kadar izin ver, Hayır = reddet."
                       if decision.grant else "Evet = bu sefer izin ver, Hayır = reddet."))
            approved = await self.ask("Gamachine bilgisayar kullanımı", text)
            if approved and decision.grant:
                self._session_grants.add(app_id.casefold())
            return self._outcome(tool, params, "card-approved" if approved else "card-rejected",
                                 decision.reason if approved else "Kullanıcı reddetti.")

    async def batch_stop(self, params):
        if self.hotkey.halted_at is not None:
            return "halted"
        if config.load()["enabled"] is not True:
            return "disabled"
        return None
