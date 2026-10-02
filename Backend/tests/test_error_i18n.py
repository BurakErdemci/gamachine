"""HTTP errors must follow the renderer language without changing their shape."""
import ast
import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from error_i18n import EN, PATTERNS, localized_http_exception_handler, translate_detail


APP_DIR = Path(__file__).resolve().parents[1] / "app"


@pytest.mark.parametrize("exception_type", [HTTPException, StarletteHTTPException])
@pytest.mark.parametrize("lang,expected", [
    ("en", "Chat not found."), ("tr", "Sohbet bulunamadı."),
    (None, "Sohbet bulunamadı."), ("de", "Sohbet bulunamadı."),
])
async def test_handler_language_status_and_headers(exception_type, lang, expected):
    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, localized_http_exception_handler)

    @app.get("/error")
    def error():
        raise exception_type(404, "Sohbet bulunamadı.", headers={"X-Error": "kept"})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/error", headers={} if lang is None else {"X-UI-Lang": lang})
    assert response.status_code == 404
    assert response.headers["x-error"] == "kept"
    assert response.json() == {"detail": expected}


async def test_handler_preserves_structured_detail():
    detail = {"code": "unknown_device", "message": "Sohbet bulunamadı."}
    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, localized_http_exception_handler)

    @app.get("/error")
    def error():
        raise HTTPException(409, detail)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/error", headers={"X-UI-Lang": "en"})
    assert response.status_code == 409
    assert response.json() == {"detail": detail}
    assert translate_detail(detail, "en") is detail


@pytest.mark.parametrize("detail,expected", [
    ("db.chats yok", "db.chats is unavailable"),
    ("görev bulunamadı", "görev not found"),
    ("Bu görev size ait değil", "This görev does not belong to you"),
    ("'codex' için otomatik kurulum desteklenmiyor.", "Automatic installation is not supported for 'codex'."),
    ("'codex' için giriş akışı desteklenmiyor.", "Sign-in is not supported for 'codex'."),
    ("Analiz sırasında bir hata oluştu: first\nsecond", "An error occurred during analysis: first\nsecond"),
    ("Güvenlik Riski: Yüklemeye çalıştığınız dosya şüpheli talimatlar içeriyor ve engellendi. (audit\nresult)",
     "Security risk: The file contains suspicious instructions and was blocked. (audit\nresult)"),
])
def test_patterns_keep_variable_parts(detail, expected):
    assert translate_detail(detail, "en") == expected
    assert translate_detail(detail, "tr") == detail


@pytest.mark.parametrize("detail", [None, [], 17, "unknown", "stt_busy", "prefix Sohbet bulunamadı."])
def test_unmatched_details_are_unchanged(detail):
    assert translate_detail(detail, "en") is detail


def test_every_turkish_http_exception_literal_is_translated():
    # Include positional detail arguments and literal fallbacks inside expressions.
    turkish = re.compile(r"[çğıöşüÇĞİÖŞÜâîû]|\b(?:gerekli|yok|ya da|Desteklenen)\b")
    missing = []
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            name = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", None)
            if name != "HTTPException":
                continue
            detail = next((kw.value for kw in call.keywords if kw.arg == "detail"), None)
            if detail is None and len(call.args) > 1:
                detail = call.args[1]
            if detail is None:
                continue
            # Render f-strings with a sentinel so their complete template is checked.
            values = []
            if isinstance(detail, ast.JoinedStr):
                values.append("".join(part.value if isinstance(part, ast.Constant) else "VARIABLE"
                                      for part in detail.values))
            else:
                values.extend(node.value for node in ast.walk(detail)
                              if isinstance(node, ast.Constant) and isinstance(node.value, str))
            for value in values:
                if turkish.search(value) and value not in EN and not any(p.fullmatch(value) for p, _ in PATTERNS):
                    missing.append(f"{path.name}:{call.lineno}: {value}")
    assert not missing, "Untranslated HTTPException details:\n" + "\n".join(missing)


async def test_main_registers_handler_and_allows_language_in_cors():
    # Read only app wiring: importing main starts databases and provider services.
    tree = ast.parse((APP_DIR / "main.py").read_text(encoding="utf-8-sig"))
    registration = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == "add_exception_handler"]
    assert any([getattr(arg, "id", None) for arg in call.args] ==
               ["StarletteHTTPException", "localized_http_exception_handler"] for call in registration)
    cors = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == "add_middleware"
                and node.args and getattr(node.args[0], "id", None) == "CORSMiddleware")
    options = {kw.arg: ast.literal_eval(kw.value) for kw in cors.keywords if kw.arg != "allow_origins"}
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["http://test"], **options)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options("/error", headers={"Origin": "http://test",
            "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "X-UI-Lang,X-Session-Token"})
    assert response.status_code == 200
    assert "x-ui-lang" in response.headers["access-control-allow-headers"].lower()
