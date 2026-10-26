"""Local configuration for the independent computer-use server."""
import json
import os
from pathlib import Path


STATE_DIR = (Path(os.environ["GAMACHINE_CU_STANDALONE_DIR"])
             if "GAMACHINE_CU_STANDALONE_DIR" in os.environ else
             Path(os.environ["LOCALAPPDATA"]) / "Gamachine" / "computer-use-standalone")
WHEEL_URL = ("https://files.pythonhosted.org/packages/11/70/"
             "97a2aae9d289623151028ec1b8ba5a12763843bacf66b2ac9b3250f991b4/"
             "cua_driver-0.35.4-py3-none-win_amd64.whl")
WHEEL_SHA256 = "617ba777b0b711c55f8d7c7ec7d51b037e0c7b016140f9a819fcfe49d3ea9738"
EXE_SHA256 = "31d5f3df16c486d06b59c0b431ea2deed139ae8f992bee50153c8f666cbbee20"


def load():
    path = STATE_DIR / "config.json"
    defaults = {"enabled": False, "driver_path": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected a configuration object")
    except FileNotFoundError:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(defaults, indent=2), encoding="utf-8")
        return defaults
    except (OSError, ValueError):
        return {**defaults, "error": "config.json okunamadı"}
    return {"enabled": data.get("enabled") is True, "driver_path": data.get("driver_path")}


def journal_path():
    return STATE_DIR / "computer_use_actions.jsonl"


def log_path():
    return STATE_DIR / "standalone.log"
