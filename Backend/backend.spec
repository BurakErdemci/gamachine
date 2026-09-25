# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — Gamachine backend
# Kullanım: cd Backend && pyinstaller backend.spec

from PyInstaller.utils.hooks import collect_all

# claude-agent-sdk: data + submodule'leri topla (yoksa frozen build'de session
# açılmaz). ⚠️ "CLI binary'si de gerekiyor" kısmı YANLIŞTI — aşağıya bak.
_cas_datas, _cas_binaries, _cas_hidden = collect_all('claude_agent_sdk')

# ⛔ SDK'nın vendor'ladığı `_bundled/claude.exe` PAKETE GİRMİYOR (~253 MB).
#
# Ürün kararı (8 Ağu 2026): kullanıcıya HABERSİZ üçüncü taraf bir AI aracı
# dağıtmıyoruz. İnsanlar Claude kullanmak istemeyebilir; ürünün işi, kullanıcının
# ZATEN sahip olduğu aracı entegre etmek.
#
# Teknik gerekçe de aynı yöne bakıyor (ölçüldü, `subprocess_cli.py:150-161`):
# SDK'nın arama sırası `options.cli_path` → **GÖMÜLÜ** → PATH. Yani gömülü ikili
# paketteyken kullanıcının kendi Claude Code kurulumu HİÇ kullanılmıyordu —
# ürünün "PC'dekine bağlan" tasarımının tam tersi. Çıkarınca sıra PATH'e düşüyor.
#
# ⚠️ `_cas_datas`'ın GERİ KALANI şart (paket verisi + submodule'ler); burada
# yalnız `_bundled` ayıklanıyor. Ölçüm: collect_all 28 data girdisi döndürüyor,
# bunların 2'si `_bundled` (claude.exe + .gitignore).
# ⚠️ Bunun karşılığı: Claude yolunu kullanacak kişide Claude Code KURULU olmalı.
# O yüzden bu değişiklik tek başına gitmiyor — sohbet, sağlayıcı bağlanana kadar
# kilitli (`/provider-ready`), yani kullanıcı bozuk bir sohbete düşmüyor.
_cas_datas = [_t for _t in _cas_datas
              if "_bundled" not in str(_t[0]).replace("\\", "/")]

# vosk (offline speech-to-text). collect_all is REQUIRED, not a precaution: vosk's
# __init__.py calls os.add_dll_directory on its OWN package directory at import time,
# so without the package data the exe compiles and then crashes with FileNotFoundError.
# Measured 3 Sep 2026: with collect_all the frozen probe loads the TR model in 237 ms.
# The MODELS are not here — they are data, shipped by electron-builder (extraResources).
_vosk_datas, _vosk_binaries, _vosk_hidden = collect_all('vosk')

# Video: ffmpeg + yt-dlp binary'lerini bundle'a ekle (VARSA). Yoksa build KIRILMAZ —
# binary'ler Backend/vendor/bin/win/ altına konunca otomatik dahil olur.
# Frozen'da _internal/bin/ altına düşer → providers/video_bin.py resolver oradan bulur.
import os as _os
import sys as _sys
if _sys.platform == "win32":
    _bin_os, _video_exes = "win", ("ffmpeg.exe", "yt-dlp.exe")
elif _sys.platform == "darwin":
    _bin_os, _video_exes = "mac", ("ffmpeg", "yt-dlp")
else:
    _bin_os, _video_exes = "linux", ("ffmpeg", "yt-dlp")
_video_bins = []
for _exe in _video_exes:
    _vp = _os.path.join("vendor", "bin", _bin_os, _exe)
    if _os.path.exists(_vp):
        _video_bins.append((_vp, "bin"))

a = Analysis(
    ['app/main.py'],
    pathex=['app'],
    binaries=_cas_binaries + _video_bins + _vosk_binaries,
    # NOT: Launcher scriptleri (run_mcp_server.sh, unityai) PyInstaller datas'ı ile
    # GÖMÜLMEZ — PyInstaller 6.x datas'ı _internal/ altına koyuyor, oysa scriptlerin
    # frozen 'backend' binary'sinin YANINDA (Backend kökünde) olması gerekiyor.
    # Bu yüzden electron-builder.yml extraResources ile doğrudan Backend köküne kopyalanır.
    datas=_cas_datas + _vosk_datas,
    hiddenimports=[
        # Claude Agent SDK (kalıcı interaktif session)
        'claude_agent_sdk',
        # uvicorn — otomatik bulunamayan modüller
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',
        # pydantic v2
        'pydantic.deprecated.class_validators',
        'pydantic.v1',
        # cryptography / bcrypt
        'cryptography.fernet',
        'cryptography.hazmat.primitives.kdf.pbkdf2',
        'cryptography.hazmat.backends.openssl',
        '_cffi_backend',
        # multipart (FastAPI form desteği)
        'multipart',
        'python_multipart',
        # email (httpx/oauth)
        'email.mime.text',
        'email.mime.multipart',
        # OS keystore
        'keyring',
        'keyring.backends',
        'keyring.backends.Windows',
        'keyring.backends.macOS',
        # AI provider kütüphaneleri
        'anthropic',
        'openai',
        'ollama',
        'httpx',
        'httpcore',
        # httpx2: the transport of anthropic >= 1 and of the MCP SDK 2.x client
        'httpx2',
        'httpcore2',
        # google-generativeai + grpc
        'google.generativeai',
        'google.ai.generativelanguage',
        'grpc',
        'grpc._cython',
        'grpc._cython.cygrpc',
        # Frozen binary subcommand dispatch hedefleri (main.py bunları çağırır):
        #   backend mcp-server        → unity_ai_mcp.server.main
        #   backend unityai           → unityai_cli.main
        #   backend codex-mcp-bridge  → providers.codex_unitymcp_bridge.main
        'unity_ai_mcp.server',
        'unity_ai_mcp.approval_bridge',
        'unity_ai_mcp.tools.file_tools',
        'unity_ai_mcp.tools.bash_tool',
        'unityai_cli',
        'providers.codex_unitymcp_bridge',
        # MCP SDK 2.x: MCPServer (was mcp.server.fastmcp in 1.x; that module now
        # raises on import) and the wire types, a separate top-level package.
        'mcp',
        'mcp.server',
        'mcp.server.mcpserver',
        'mcp_types',
    ] + _cas_hidden + _vosk_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Gereksiz büyük paketleri dışla.
        # DİKKAT: buraya bir paket eklemeden önce `grep -rn "import <paket>" app/`
        # koş — 'PIL' burada duruyordu ve tools/screenshot_tool.py onu import
        # ediyor; frozen build'de screenshot aracı "Pillow kurulu değil" diyerek
        # sessizce ölüyordu (kullanıcı bunu eksik özellik sanıyor). Excludes bir
        # boyut optimizasyonu; çalışan bir kod yolunu kesiyorsa yanlıştır.
        'tkinter',
        'matplotlib',
        # numpy KALIYOR: app/ altında hiçbir import'u yok (doğrulandı) ve tek
        # zorunlu gereksinen paketler faiss-cpu/sentence-transformers'tı — ikisi de
        # requirements.txt'ten kaldırıldı. Pillow numpy'a bağlı DEĞİL. Bayat bir
        # venv'de kalmış numpy ~50MB'ı bundle'a sokmasın diye savunma amaçlı duruyor.
        'numpy',
        'cv2',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='backend',
)
