"""Computer-use authorization decisions and best-effort action journal."""
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from .policy import TOOLS, is_hard_denied

logger = logging.getLogger(__name__)
INACTIVE_REASON = "Bilgisayar kullanımı kapalı ya da çalışma modu Otomatik değil."


@dataclass(frozen=True)
class Decision:
    kind: str
    reason: str = ""
    grant: bool = False


def decide(tool, params, *, enabled, mode, granted):
    if not (enabled and mode == "auto"):
        return Decision("deny", INACTIVE_REASON)
    if tool in ("computer_list_apps", "computer_list_windows"):
        return Decision("allow")
    if tool not in TOOLS:
        return Decision("deny", "Bilinmeyen bilgisayar kullanımı aracı.")
    bundle_id = params.get("bundle_id")
    if not isinstance(bundle_id, str) or not bundle_id.strip():
        return Decision("deny", "Hedef uygulama tanınamadı.")
    if is_hard_denied(bundle_id):
        return Decision("deny", "Bu uygulama bilgisayar kullanımına kapalı.")
    risk = str(params.get("risk_reason") or "Riskli adım için onay gerekiyor.") if params.get("risky") else ""
    if not granted:
        return Decision("card", "Bu uygulamaya ilk erişim için onay gerekiyor." + (" " + risk if risk else ""), True)
    if risk:
        return Decision("card", risk)
    return Decision("allow")


def actions_log_append(path, record):
    try:
        row = {key: record.get(key) for key in ("conversation_id", "tool", "bundle_id", "pid", "decision", "reason")}
        row["ts"] = datetime.now(timezone.utc).isoformat()
        data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            if os.write(fd, data) != len(data):
                logger.warning("Incomplete computer-use journal write")
        finally:
            os.close(fd)
    except Exception:
        logger.exception("Could not append computer-use action journal")
