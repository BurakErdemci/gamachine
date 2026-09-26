"""
Tool Registry — Agentic AI'ın kullanabileceği tüm araçları ve LLM function schemas'ını tutar.
"""
import json
import logging
from typing import Any, Dict, Callable

from tools.file_tools import read_file, write_file, list_directory, delete_file, run_command
from tools.search_tools import search_in_project, find_files
from tools.memory_tools import save_to_memory, recall_memory
from tools.screenshot_tool import capture_unity_screenshot
from tools.unity_mcp_tools import (
    call_unity_tool_with_arguments, get_unity_tool_definitions, get_unity_tool_functions,
    is_unity_tool,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════
# TOOL TANIMLARI (LLM'e gönderilecek schema)
# ══════════════════════════════════════════════

TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Unity projesindeki bir dosyayı okur. Dosya yolunu workspace root'a göre ver (örn: Assets/Scripts/Player/PlayerMovement.cs)",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Okunacak dosyanın workspace root'a göre yolu"
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "search_in_project",
        "description": "Projede metin/pattern arar. Tüm .cs dosyalarında arama yapar ve eşleşen satırları döndürür.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Aranacak metin veya pattern"
                },
                "file_extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Aranacak dosya uzantıları (varsayılan: .cs). Örnek: [\".cs\", \".shader\"]"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "find_files",
        "description": "Dosya adına göre proje içinde arama yapar. Örnek: 'Player' ile başlayan tüm scriptleri bul.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Dosya adında aranacak pattern (büyük/küçük harf duyarsız)"
                }
            },
            "required": ["pattern"]
        }
    },
    {
        "name": "list_directory",
        "description": "Belirtilen klasörün içeriğini listeler. Dosya ve alt klasörleri gösterir.",
        "parameters": {
            "type": "object",
            "properties": {
                "dir_path": {
                    "type": "string",
                    "description": "Listelenecek klasör yolu (workspace root'a göre). Örnek: Assets/Scripts"
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Sadece bu uzantılara sahip dosyaları göster. Boş bırakılırsa hepsini gösterir."
                }
            },
            "required": ["dir_path"]
        }
    },
    {
        "name": "write_file",
        "description": "Unity projesinde bir dosya oluşturur veya günceller. Tam dosya içeriğini yaz.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Yazılacak dosyanın workspace root'a göre yolu"
                },
                "content": {
                    "type": "string",
                    "description": "Dosyaya yazılacak tam içerik"
                }
            },
            "required": ["file_path", "content"]
        }
    },
    {
        "name": "save_to_memory",
        "description": "Önemli proje bilgilerini, mimari kararları veya kullanıcı talimatlarını hafızaya kaydeder. Sadece gerçekten kritik bilgileri kaydet.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Hafızaya kaydedilecek markdown formatında içerik"
                }
            },
            "required": ["content"]
        }
    },
    {
        "name": "recall_memory",
        "description": "Bu sohbet için daha önce kaydedilmiş olan proje hafızasını geri çağırır.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "delete_file",
        "description": "Unity projesindeki bir dosyayı siler. DİKKAT: Bu işlem kalıcıdır ve kullanıcı onayı gerektirir.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Silinecek dosyanın workspace root'a göre yolu (örn: Assets/Scripts/OldScript.cs)"
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "run_command",
        "description": "Terminalde bir sistem komutu çalıştırır. Örn: git status, npm install, unity-build. DİKKAT: Bu işlem kullanıcı onayı gerektirir.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Çalıştırılacak terminal komutu"
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "capture_unity_screenshot",
        "description": (
            "Unity Editor'ün ekran görüntüsünü alır ve görsel olarak analiz eder. "
            "Blend tree, animasyon, sahne veya materyal gibi görsel doğrulama gerektiren "
            "durumlarda çağır. Her adımda değil, yalnızca görsel kontrol gerektiğinde kullan."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
]


# ══════════════════════════════════════════════
# TOOL ÇALIŞTIRICI
# ══════════════════════════════════════════════

# Tool name → function mapping
_TOOL_FUNCTIONS: Dict[str, Callable] = {
    "read_file": read_file,
    "search_in_project": search_in_project,
    "find_files": find_files,
    "list_directory": list_directory,
    "write_file": write_file,
    "delete_file": delete_file,
    "run_command": run_command,
    "save_to_memory": save_to_memory,
    "recall_memory": recall_memory,
    "capture_unity_screenshot": capture_unity_screenshot,
}

# Hangi tool'lar conversation_id parametresi alıyor
_TOOLS_NEEDING_CONV_ID = {"save_to_memory", "recall_memory"}

# Hangi tool'lar doğrudan workspace_path parametresi alıyor
_TOOLS_NEEDING_WORKSPACE = {"search_in_project", "find_files", "read_file", "write_file", "list_directory", "delete_file", "run_command"}


def execute_tool(tool_name: str, arguments: Dict[str, Any], workspace_path: str, conversation_id: str = None) -> Dict[str, Any]:
    """Verilen tool'u güvenli şekilde çalıştırır. Unity MCP tool'larını da destekler."""

    # Unity MCP tool'u mu?
    if is_unity_tool(tool_name):
        if tool_name not in get_unity_tool_functions():
            return {"success": False, "error": f"Unity tool bulunamadı: {tool_name}"}
        try:
            result = call_unity_tool_with_arguments(tool_name, arguments, conversation_id)
            logger.info(f"  🎮 Unity Tool [{tool_name}] çalıştırıldı: success={result.get('success', '?')}")
            return result
        except Exception as e:
            logger.error(f"  🎮 Unity Tool [{tool_name}] HATA: {e}")
            return {"success": False, "error": str(e)}

    # Standart araç
    func = _TOOL_FUNCTIONS.get(tool_name)
    if not func:
        return {"success": False, "error": f"Bilinmeyen araç: {tool_name}"}

    try:
        if tool_name in _TOOLS_NEEDING_WORKSPACE:
            arguments["workspace_path"] = workspace_path
        if tool_name in _TOOLS_NEEDING_CONV_ID:
            arguments["conversation_id"] = conversation_id

        if "file_path" in arguments and not arguments["file_path"].startswith("/"):
            arguments["file_path"] = f"{workspace_path}/{arguments['file_path']}"
        if "dir_path" in arguments and not arguments["dir_path"].startswith("/"):
            arguments["dir_path"] = f"{workspace_path}/{arguments['dir_path']}"

        result = func(**arguments)
        logger.info(f"  🔧 Tool [{tool_name}] çalıştırıldı: success={result.get('success', '?')}")
        return result
    except Exception as e:
        logger.error(f"  🔧 Tool [{tool_name}] HATA: {e}")
        return {"success": False, "error": str(e)}


def _all_tool_definitions() -> list:
    """Standart + aktif Unity MCP tool'larını birleştirir."""
    return TOOL_DEFINITIONS + get_unity_tool_definitions()


# Gemini function-calling şeması OpenAPI 3.0 ALT KÜMESİDİR — pydantic/FastMCP'nin ürettiği
# $ref/$defs/anyOf/additionalProperties/title/default gibi alanları kabul etmez → 400.
# Bu yüzden Unity MCP şemalarını Gemini'ye vermeden önce sadeleştiriyoruz.
_GEMINI_TYPES = {"string", "number", "integer", "boolean", "array", "object"}


def _sanitize_gemini_schema(node: Any) -> Dict[str, Any]:
    """JSON Schema düğümünü Gemini'nin kabul ettiği alt kümeye indirger (recursive).
    Bilinmeyen/çözülemeyen ($ref) düğümler güvenli 'string'e düşürülür (400 yerine)."""
    if not isinstance(node, dict):
        return {"type": "string"}

    # anyOf/oneOf/allOf (Optional[X], Union) → ilk non-null tipi al, nullable işaretle
    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(node.get(key), list):
            opts = [o for o in node[key] if isinstance(o, dict) and o.get("type") != "null"]
            merged = _sanitize_gemini_schema(opts[0]) if opts else {"type": "string"}
            if any(isinstance(o, dict) and o.get("type") == "null" for o in node[key]):
                merged["nullable"] = True
            if node.get("description") and "description" not in merged:
                merged["description"] = str(node["description"])[:512]
            return merged

    out: Dict[str, Any] = {}
    t = node.get("type")
    if isinstance(t, list):          # ["string","null"] → string + nullable
        if "null" in t:
            out["nullable"] = True
        non_null = [x for x in t if x != "null"]
        t = non_null[0] if non_null else "string"
    if t not in _GEMINI_TYPES:       # $ref / bilinmeyen tip → güvenli string fallback
        t = "string"
    out["type"] = t

    if node.get("description"):
        out["description"] = str(node["description"])[:512]
    if isinstance(node.get("enum"), list):
        out["enum"] = [str(e) for e in node["enum"]]

    if t == "object":
        props = node.get("properties")
        if isinstance(props, dict) and props:
            out["properties"] = {k: _sanitize_gemini_schema(v) for k, v in props.items()}
            req = node.get("required")
            if isinstance(req, list):
                out["required"] = [r for r in req if r in out["properties"]]
        else:
            out["properties"] = {}
    elif t == "array":
        items = node.get("items")
        out["items"] = _sanitize_gemini_schema(items) if isinstance(items, dict) else {"type": "string"}

    return out


def get_gemini_tool_declarations() -> list:
    """Gemini formatında tool declarations (built-in + Unity MCP). Şemalar Gemini'nin kabul
    ettiği alt kümeye SANITIZE edilir (aksi halde Unity MCP şemaları 400 INVALID_ARGUMENT verir)."""
    return [
        {
            "function_declarations": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": _sanitize_gemini_schema(
                        t.get("parameters") or {"type": "object", "properties": {}}),
                }
                for t in _all_tool_definitions()
            ]
        }
    ]


def get_openai_tool_declarations() -> list:
    """OpenAI/Anthropic function calling formatında tool declarations döndürür."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            }
        }
        for t in _all_tool_definitions()
    ]
