"""
Tool Registry — Agentic AI'ın kullanabileceği tüm araçları ve LLM function schemas'ını tutar.
"""
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Callable

from tools import file_tools
from tools.file_tools import read_file, write_file, list_directory, delete_file
from agentic import shell_jobs, todo_list
from agentic.web_fetch import web_fetch
from tools.search_tools import search_in_project, find_files
from tools.screenshot_tool import capture_unity_screenshot
import harness_notes
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
        "description": "Terminalde bir sistem komutu çalıştırır. Örn: git status, npm install, unity-build. DİKKAT: Bu işlem kullanıcı onayı gerektirir. background=true ise komut arka planda başlar ve hemen bir job_id döner (uzun sunucu/derleme için); sonucu job_status / job_output ile izle, job_stop ile bitir.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Çalıştırılacak terminal komutu"
                },
                "background": {
                    "type": "boolean",
                    "description": "true: komutu arka planda başlat, job_id ile hemen dön. Varsayılan false."
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "job_status",
        "description": "Arka plan işinin durumunu verir: running, exited (çıkış koduyla) veya stopped.",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "run_command(background=true) çıktısındaki job_id"}
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "job_output",
        "description": "Arka plan işinin çıktısının (stdout+stderr) son kısmını döndürür.",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "run_command(background=true) çıktısındaki job_id"},
                "tail": {"type": "integer", "description": "Döndürülecek son karakter sayısı (varsayılan 4000, en çok 50000)"}
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "job_stop",
        "description": "Arka plan işini ve başlattığı tüm alt süreçleri durdurur.",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Durdurulacak işin job_id'si"}
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "web_fetch",
        "description": "Herkese açık bir web sayfasını (http/https) metin olarak okur. HTML okunur metne çevrilir. Yerel/özel ağ adresleri reddedilir; çerez veya kimlik bilgisi gönderilmez.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Okunacak tam adres (https://...)"},
                "max_chars": {"type": "integer", "description": "Döndürülecek en çok karakter (varsayılan 20000, en çok 200000)"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "todo_write",
        "description": "Bu sohbetin görev listesini yazar (tüm listeyi her seferinde gönder). Çok adımlı işte ilerlemeyi izlemek için kullan. Aynı anda en çok bir madde in_progress olabilir.",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "Tam görev listesi",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "Görevin kısa tanımı"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"],
                                       "description": "pending, in_progress veya completed"}
                        },
                        "required": ["content", "status"]
                    }
                }
            },
            "required": ["items"]
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
    {
        "name": "list_chats",
        "description": (
            "Kullanıcının bu uygulamadaki diğer sohbetlerini listeler (numara, başlık, dal mı, "
            "şu an çalışıyor mu). Dallar da geçerli hedeftir. Kullanıcı hedefi `@<numara>` ile "
            "andıysa o numarayı kullan. Aynı ya da benzer başlıklı sohbetler arasında tahmin "
            "etme; kullanıcıdan hedefi `@` ile seçmesini iste."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "send_chat_message",
        "description": (
            "Kullanıcının başka bir sohbetine ya da dalına (başka bir AI oturumuna) not bırakır. "
            "O sohbet boştaysa notla hemen uyanır, çalışıyorsa turu bitince okur. Adım modunda "
            "kullanıcı önce onaylar. Emin değilsen gönderme, kullanıcıdan hedefi `@` ile "
            "seçmesini iste. Yalnız gerçekten o sohbetin bilmesi gereken bir şey için kullan. "
            # Same text as agentic.mailbox.SEND_TOOL_REPLY_RULE (a test holds them equal).
            "Bir nota cevap veriyorsan: notu gönderen sohbet bu sohbette yazdıklarını "
            "GÖREMEZ; cevabın ona YALNIZCA bu araçla gönderirsen ulaşır. (The sender "
            "of a note cannot see your chat; a reply reaches it only through this tool.)"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to_chat_id": {
                    "type": "integer",
                    "description": (
                        "Hedef sohbetin numarası: kullanıcı `@<numara>` ile andıysa o numara, "
                        "yoksa list_chats'te açıkça uyan tek sohbetin numarası")
                },
                "message": {
                    "type": "string",
                    "description": "Not metni (en fazla 4000 karakter)"
                }
            },
            "required": ["to_chat_id", "message"]
        }
    },
    {
        "name": "show_image",
        "description": (
            "Bir görüntü dosyasını kullanıcıya bu sohbetin içinde gösterir (oyun ekran görüntüsü, "
            "ürettiğin efekt vb.). png, jpg, gif ya da webp, en fazla 20 MB. Kullanıcıya görüntü "
            "göstermek için dosya yolunu metne yazmak ya da başka bir yere yüklemek yerine bunu kullan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Görüntü dosyasının tam (mutlak) yolu"
                },
                "caption": {
                    "type": "string",
                    "description": "Görüntünün altında görünecek kısa açıklama (isteğe bağlı)"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "harness_search",
        "description": (
            "Gamachine'in kendi araçları, özellikleri, ayarları ve sınırları hakkındaki kısa notlarda arar "
            "(Türkçe ya da İngilizce kelime, ya da bir araç adı). Kullanıcı bunlardan birini sorduğunda "
            "cevaplamadan önce çağır; sonuçtaki bir notu harness_read ile oku."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Aranacak kelimeler ya da araç adı"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "harness_read",
        "description": "harness_search'ün döndürdüğü bir notun tamamını okur (note_id: sonuçtaki id).",
        "parameters": {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "Okunacak notun id'si"
                }
            },
            "required": ["note_id"]
        }
    },
    {
        "name": "harness_status",
        "description": (
            "Bu turun canlı durumunu verir: sağlayıcı ve model, onay modu, salt okunur mu, dil, bilgisayar "
            "ve tarayıcı kullanımı açık mı, Unity MCP durumu ve bu tur için açık araç adları. "
            "Anahtar ya da token içermez."
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

def _run_command(command: str, workspace_path: str, conversation_id=None, background=False) -> Dict[str, Any]:
    """The blocking run_command, or a background job. Approval is decided by the loop on `command` alone,
    before this runs, so both modes pass the same check; the refusal rule is shared through shell_exec."""
    if background is not True and str(background).strip().lower() != "true":
        return file_tools.run_command(command, workspace_path)
    try:
        job_id = shell_jobs.start(command, workspace_path, conversation_id)
    except shell_jobs.JobError as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "job_id": job_id, "status": shell_jobs.RUNNING,
            "content": f"Started background job {job_id}. Use job_status / job_output / job_stop."}


def _job_call(fn, job_id, conversation_id, **kwargs) -> Dict[str, Any]:
    try:
        return fn(job_id, conversation_id, **kwargs)
    except shell_jobs.JobError as e:
        return {"success": False, "error": str(e)}


def _job_status(job_id: str, conversation_id=None) -> Dict[str, Any]:
    return _job_call(lambda j, c: {"success": True, **shell_jobs.status(j, c)}, job_id, conversation_id)


def _job_output(job_id: str, conversation_id=None, tail=shell_jobs.DEFAULT_TAIL_CHARS) -> Dict[str, Any]:
    try:
        tail = int(tail)
    except (TypeError, ValueError):
        tail = shell_jobs.DEFAULT_TAIL_CHARS
    return _job_call(lambda j, c: {"success": True, **shell_jobs.status(j, c),
                                   "content": shell_jobs.output(j, c, tail)}, job_id, conversation_id)


def _job_stop(job_id: str, conversation_id=None) -> Dict[str, Any]:
    return _job_call(lambda j, c: {"success": True, **shell_jobs.stop(j, c)}, job_id, conversation_id)


def _todo_write(items=None, conversation_id=None) -> Dict[str, Any]:
    return todo_list.set_todos(conversation_id, items)


# Tool name → function mapping
_TOOL_FUNCTIONS: Dict[str, Callable] = {
    "read_file": read_file,
    "search_in_project": search_in_project,
    "find_files": find_files,
    "list_directory": list_directory,
    "write_file": write_file,
    "delete_file": delete_file,
    "run_command": _run_command,
    "job_status": _job_status,
    "job_output": _job_output,
    "job_stop": _job_stop,
    "web_fetch": web_fetch,
    "todo_write": _todo_write,
    "capture_unity_screenshot": capture_unity_screenshot,
    "harness_search": lambda query: {
        "success": True, "content": harness_notes.format_search(harness_notes.search(query))},
    "harness_read": lambda note_id: harness_notes.read(note_id),
}

# Hangi tool'lar conversation_id parametresi alıyor
_TOOLS_NEEDING_CONV_ID = {"list_chats", "send_chat_message", "show_image", "run_command", "job_status",
                          "job_output", "job_stop", "todo_write"}


async def _list_chats(conversation_id=None) -> Dict[str, Any]:
    from agentic import mailbox
    service = mailbox.get_service()
    if service is None:
        return {"success": False, "error": "Sohbet posta kutusu hazır değil."}
    return {"success": True, "content": mailbox.format_chat_list(service.list_chats(conversation_id))}


async def _send_chat_message(to_chat_id=None, message=None, conversation_id=None) -> Dict[str, Any]:
    from agentic import mailbox
    service = mailbox.get_service()
    if service is None:
        return {"success": False, "error": "Sohbet posta kutusu hazır değil."}
    # The injected id is the runner's own chat, never a model argument.
    result = await service.send_and_wait(conversation_id, to_chat_id, message)
    text = mailbox.describe_send_result(result)
    if result.get("status") in (mailbox.STATUS_QUEUED, mailbox.STATUS_DELIVERED):
        return {"success": True, "content": text}
    return {"success": False, "error": text}


async def _show_image(path=None, caption="", conversation_id=None) -> Dict[str, Any]:
    from unity_ai_mcp.tools import show_tools
    # The injected id is the runner's own chat, never a model argument.
    if type(conversation_id) is not int:
        return {"success": False, "error": show_tools._NO_CHAT}
    text = await show_tools.post_show_image(conversation_id, str(path or ""), str(caption or ""))
    if text == show_tools.SHOWN:
        return {"success": True, "content": text}
    return {"success": False, "error": text}


def _read_only_status_lists(name: str) -> bool:
    """A read-only run lists a tool only when it can run at least one call of it: a built-in the runner
    allows, or a Unity tool the ledger reads at tool level or gives a read action (execute_code's
    get_history counts). Other tools are not offered to a read-only turn."""
    from agentic.agent_runner import _BUILTIN_TOOL_NAMES, _read_only_tool_allowed
    from unity_tool_policy import has_read_call, strip_prefix
    if name in _BUILTIN_TOOL_NAMES:
        return _read_only_tool_allowed(name, {})
    return has_read_call(strip_prefix(name) or name)


async def _harness_status() -> Dict[str, Any]:
    definitions = _all_tool_definitions()
    if harness_notes.run_context().get("read_only"):
        definitions = [t for t in definitions if _read_only_status_lists(t["name"])]
    state = await harness_notes.status(tools=[t["name"] for t in definitions])
    return {"success": True, "content": json.dumps(state, ensure_ascii=False)}


# Awaited on the event loop, not in a worker thread: the step-mode card they
# wait on is resolved by a route on that same loop.
_ASYNC_TOOL_FUNCTIONS: Dict[str, Callable] = {
    "list_chats": _list_chats,
    "send_chat_message": _send_chat_message,
    "show_image": _show_image,
    "harness_status": _harness_status,
}


def is_async_tool(tool_name: str) -> bool:
    return tool_name in _ASYNC_TOOL_FUNCTIONS


async def execute_tool_async(tool_name: str, arguments: Dict[str, Any], workspace_path: str,
                             conversation_id=None) -> Dict[str, Any]:
    func = _ASYNC_TOOL_FUNCTIONS.get(tool_name)
    if func is None:
        return {"success": False, "error": f"Bilinmeyen araç: {tool_name}"}
    args = dict(arguments or {})
    args.pop("conversation_id", None)
    if tool_name in _TOOLS_NEEDING_CONV_ID:
        args["conversation_id"] = conversation_id
    try:
        return await func(**args)
    except TypeError as e:
        return {"success": False, "error": f"Geçersiz argüman: {e}"}
    except Exception as e:
        logger.error(f"  🔧 Tool [{tool_name}] HATA: {e}")
        return {"success": False, "error": str(e)}

# Hangi tool'lar doğrudan workspace_path parametresi alıyor
_TOOLS_NEEDING_WORKSPACE = {"search_in_project", "find_files", "read_file", "write_file", "list_directory", "delete_file", "run_command"}


def _is_absolute(path: str) -> bool:
    """A POSIX root, a Windows drive-absolute path or a UNC path; anything else is workspace-relative."""
    return path.startswith(("/", "\\\\")) or (len(path) > 2 and path[0].isalpha() and path[1] == ":" and path[2] in ("\\", "/"))


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

    if is_async_tool(tool_name):
        return {"success": False, "error": f"{tool_name} execute_tool_async ile çağrılmalı."}

    # Standart araç
    func = _TOOL_FUNCTIONS.get(tool_name)
    if not func:
        return {"success": False, "error": f"Bilinmeyen araç: {tool_name}"}

    try:
        if tool_name in _TOOLS_NEEDING_WORKSPACE:
            arguments["workspace_path"] = workspace_path
        if tool_name in _TOOLS_NEEDING_CONV_ID:
            arguments["conversation_id"] = conversation_id

        for key in ("file_path", "dir_path"):
            if key in arguments and not _is_absolute(arguments[key]):
                arguments[key] = f"{workspace_path}/{arguments[key]}"

        result = func(**arguments)
        logger.info(f"  🔧 Tool [{tool_name}] çalıştırıldı: success={result.get('success', '?')}")
        return result
    except Exception as e:
        logger.error(f"  🔧 Tool [{tool_name}] HATA: {e}")
        return {"success": False, "error": str(e)}


# Tools one API run brings with it (its own stdio servers). Context-scoped so the
# global registry, and every other chat reading it, never sees them.
_RUN_TOOL_DEFINITIONS: ContextVar[tuple] = ContextVar("run_tool_definitions", default=())


@contextmanager
def run_tool_definitions(definitions):
    token = _RUN_TOOL_DEFINITIONS.set(tuple(definitions))
    try:
        yield
    finally:
        _RUN_TOOL_DEFINITIONS.reset(token)


def _all_tool_definitions() -> list:
    """Standart + aktif Unity MCP tool'larını birleştirir."""
    return TOOL_DEFINITIONS + get_unity_tool_definitions() + list(_RUN_TOOL_DEFINITIONS.get())


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
