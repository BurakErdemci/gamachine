"""unityMCP salt-okuma muafiyetlerinin kütüğe bağlı olduğunu ve doğru
yönde ayarlandığını sınar.

Bu dosyanın var olma sebebi ölçülmüş bir arıza: muafiyet listesi
`claude_sdk_session.py` içine ELLE kopyalanmıştı ve üç yönden birden bozuktu —
hiçbiri çalışma anında görünmüyordu.

  · ÖLÜ  — `manage_scene` için `get_info` muafiyeti yazılıydı; `manage_scene`'in
           öyle bir action'ı yok, yani o satır hiçbir zaman eşleşmedi. Bir
           kural yalnızca yazılı olduğu için koruma sanılıyordu.
  · DAR  — `unity_reflect`, `find_in_file`, `get_sha` gibi 8 zararsız araç ve
           ~40 okuma action'ı listede yoktu. Bunlar C# yazmadan önce her turda
           çağrılan keşif araçları; her birinde kart çıkması kullanıcıyı
           refleks-onaya alıştırır, ki kapının kendi amacını yer.
  · GENİŞ — `read_console` koşulsuz muaftı, oysa `action="clear"` konsolu siler.

29 Tem denetimi bu dosyanın kendi iddialarından birini de çürüttü: `manage_scene`
ve `find_gameobjects` "zararsız keşif" sayılıyordu, oysa ikisi de koşulsuz
`preflight(refresh_if_dirty=True)` çağırıyor. Artık ikisi de kartlı.

Kütüğün kendi kaynağa sadakati ayrı bir testin işi
(`unity-mcp/Server/tests/test_tool_actions_ledger.py`). Burada sınanan, ürünün
o kütüğü GERÇEKTEN okuyabildiği ve doğru yorumladığı.
"""
import ast
import os
import sys

import pytest

_APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

import unity_tool_policy as policy  # noqa: E402

UNITY = policy.UNITY_MCP_PREFIX


@pytest.fixture(autouse=True)
def _fresh_cache():
    policy.reset_cache()
    yield
    policy.reset_cache()


def test_ledger_is_reachable_from_the_backend():
    """Yol çözümü gerçekten kütüğü buluyor mu — bu testin geri kalanı buna dayanıyor."""
    assert policy.ledger_available(), (
        "Kütük Backend'in yol çözümüyle bulunamadı. Bulunamazsa ürün fail-closed "
        f"davranır (her çağrı karta düşer). Denenen yollar: {policy._candidate_paths()}"
    )


def test_reader_module_depends_only_on_stdlib():
    """
    `unity_tool_policy` okuyucuyu dosya yolundan izole yüklüyor; bu ancak
    okuyucu sunucu paketinden hiçbir şey import etmiyorsa çalışır. Varsayımı
    yorumda bırakmak yetmez — sunucuya özgü tek bir import eklendiğinde ürün
    sessizce muafiyetsiz kalırdı (fail-closed, ama sebebi görünmez).
    """
    path = next(p for p in policy._candidate_paths() if os.path.isfile(p))
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    allowed = {"json", "pathlib", "typing", "__future__"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # göreli import = paket içi bağımlılık
                imported.add(f".{node.module or ''}")
            elif node.module:
                imported.add(node.module.split(".")[0])

    disallowed = sorted(imported - allowed)
    assert not disallowed, (
        f"tool_actions.py artık stdlib dışına çıkıyor: {disallowed}. "
        "Backend onu izole yükleyemez; ya bağımlılık kaldırılmalı ya da bu "
        "modülün yükleme stratejisi değişmeli."
    )


def test_the_ledger_is_never_loaded_from_a_user_writable_data_dir():
    """
    Kapı, bulduğu Python'u kendi sürecinde `exec_module` ile çalıştırıyor.
    Bu yüzden ARANAN YER'in kendisi bir güvenlik sınırı.

    Dış denetim (29 Tem 2026) `~/.unity_architect_ai/...` adayının canlı
    olduğunu gösterdi: dizin kullanıcı yazılabilir, oraya bırakılan bir modül
    kapının içinde koşuyor ve bütün unityMCP kartlarını kalıcı susturuyordu
    (dosya sonradan silinse bile, modül belleğe alınmış oluyor). Aday
    kaldırıldı; bu test geri gelmesini engelliyor.

    Not: bu testin kapsamadığı şey, birinci adayın kendi dizininin yazılabilir
    olması (Windows'ta per-user kurulum). O bütünlük doğrulaması ister ve
    ayrı bir karar.
    """
    forbidden = os.path.join(os.path.expanduser("~"), ".unity_architect_ai")
    for path in policy._candidate_paths():
        assert not os.path.abspath(path).startswith(os.path.abspath(forbidden)), (
            f"Kütük {path} yolundan yüklenebiliyor. Orası kullanıcı yazılabilir ve "
            "_load() bulduğu modülü kapının sürecinde çalıştırıyor — tek bir dosya "
            "yazımı bütün onay kartlarını susturur."
        )


def test_dead_exemption_is_gone():
    """Eski listedeki `get_info` muafiyeti — var olmayan bir action'a yazılmıştı."""
    assert not policy.is_unity_mcp_read_only(f"{UNITY}manage_scene", {"action": "get_info"})


def test_scene_reads_are_no_longer_exempt_because_of_the_preflight_refresh():
    """
    `manage_scene`'in get_* action'lari semantik olarak okuma, ama muaf DEĞİL.

    Sebep olculdu (29 Tem 2026, dis denetim): `manage_scene.py:90` her cagride
    kosulsuz `preflight(refresh_if_dirty=True)` yapiyor, o da proje disaridan
    kirliyse `refresh_unity(scope=all, compile=request)` calistirip asset import
    + derleme + domain reload tetikliyor. Yani "okuma" cagrisi, kutugun baska
    yerde `write` diye siniflandirdigi bir isi yaptiriyor.

    Kullanicinin karari (29 Tem): okuma dogru calismaya devam etsin ama adim
    modunda kart ciksin. Bedeli kabul edildi: `get_hierarchy` en sik cagrilan
    kesif araci ve artik her turda kart cikariyor.
    """
    for action in ("get_hierarchy", "get_active", "get_build_settings", "get_loaded_scenes"):
        assert not policy.is_unity_mcp_read_only(f"{UNITY}manage_scene", {"action": action}), action


def test_tools_that_do_not_refresh_keep_their_reads():
    """Kapinin gereginden genis olmadiginin kaniti: preflight cagirmayan araclarda okuma hala muaf."""
    assert policy.is_unity_mcp_read_only(f"{UNITY}manage_build", {"action": "status"})
    assert policy.is_unity_mcp_read_only(f"{UNITY}manage_material", {"action": "get_material_info"})
    assert policy.is_unity_mcp_read_only(f"{UNITY}manage_packages", {"action": "list_packages"})
    assert policy.is_unity_mcp_read_only(f"{UNITY}manage_physics", {"action": "raycast"})


def test_scene_mutations_are_not_exempt():
    for action in ("create", "load", "save", "close_scene", "set_active_scene", "move_to_scene"):
        assert not policy.is_unity_mcp_read_only(f"{UNITY}manage_scene", {"action": action}), action


def test_read_console_exemption_is_no_longer_unconditional():
    # Eski kural aracın TAMAMINI muaf tutuyordu; `clear` konsolu siler.
    assert policy.is_unity_mcp_read_only(f"{UNITY}read_console", {"action": "get"})
    assert policy.is_unity_mcp_read_only(f"{UNITY}read_console", {})  # varsayılan "get"
    assert not policy.is_unity_mcp_read_only(f"{UNITY}read_console", {"action": "clear"})


@pytest.mark.parametrize("tool,params", [
    # Action alan iki araç: action'sız çağrı BİLEREK muaf değil (fail-closed),
    # o yüzden gerçek bir action ile sınanıyorlar.
    ("unity_reflect", {"action": "search"}),
    ("unity_docs", {"action": "lookup"}),
    # Geri kalanının action parametresi yok; araç düzeyinde salt-okuma.
    ("find_in_file", {}),
    ("get_sha", {}),
    ("validate_script", {}),
    ("manage_script_capabilities", {}),
    ("get_test_job", {}),
    ("debug_request_context", {}),
])
def test_harmless_discovery_tools_are_exempt(tool, params):
    """Eski listede hiçbiri yoktu; her turda kart çıkarıyorlardı."""
    assert policy.is_unity_mcp_read_only(f"{UNITY}{tool}", params)


@pytest.mark.parametrize("tool", ["unity_reflect", "unity_docs"])
def test_action_taking_tools_need_an_action_to_be_exempt(tool):
    """Salt-okuma bir araçta bile, hangi action olduğu bilinmeden muafiyet verilmez."""
    assert not policy.is_unity_mcp_read_only(f"{UNITY}{tool}", {})


@pytest.mark.parametrize("tool,params", [
    ("execute_code", {"action": "execute"}),
    ("execute_code", {"action": "execute", "safety_checks": False}),
    ("manage_gameobject", {"action": "delete"}),
    ("manage_script", {"action": "create"}),
    ("manage_asset", {"action": "delete"}),
    ("refresh_unity", {}),
    ("run_tests", {"mode": "EditMode"}),
    ("execute_menu_item", {"menu_path": "File/Save"}),
])
def test_mutations_are_never_exempt(tool, params):
    assert not policy.is_unity_mcp_read_only(f"{UNITY}{tool}", params)


def test_batch_execute_is_only_exempt_when_every_inner_call_reads():
    # `manage_scene` artik okuma tarafinda kullanilamiyor: preflight refresh'i
    # butun action'larini write yapti.
    reads = {"commands": [
        {"tool": "unity_reflect", "params": {"action": "search"}},
        {"tool": "read_console", "params": {"action": "get"}},
    ]}
    mixed = {"commands": [
        {"tool": "unity_reflect", "params": {"action": "search"}},
        {"tool": "manage_gameobject", "params": {"action": "delete"}},
    ]}
    assert policy.is_unity_mcp_read_only(f"{UNITY}batch_execute", reads)
    assert not policy.is_unity_mcp_read_only(f"{UNITY}batch_execute", mixed)


@pytest.mark.parametrize("tool, params", [
    ("game_hooks", {"action": "get", "action_": "call", "name": "level.restart"}),
    ("manage_script", {"action": "read", "action_": "delete", "name": "X", "path": "Assets"}),
])
def test_colliding_action_keys_are_never_exempt(tool, params):
    """C# batch normalisation folds `action_` into `action` (later key wins), so
    the literal `action` is not what Unity runs (external audit 2026-09-25)."""
    assert not policy.is_unity_mcp_read_only(f"{UNITY}{tool}", params)
    batch = {"commands": [{"tool": tool, "params": params}]}
    assert not policy.is_unity_mcp_read_only(f"{UNITY}batch_execute", batch)


def test_non_unity_tools_are_never_exempted_here():
    """Bu politika yalnız unityMCP'yi sınıflandırır; Write/Bash kendi yollarından geçer."""
    assert not policy.is_unity_mcp_read_only("Write", {"file_path": "/tmp/x"})
    assert not policy.is_unity_mcp_read_only("Bash", {"command": "ls"})
    assert not policy.is_unity_mcp_read_only("mcp__unityai__read_file", {})


def test_unknown_unity_tool_is_not_exempt():
    assert not policy.is_unity_mcp_read_only(f"{UNITY}manage_teleporter", {"action": "get"})


def test_missing_ledger_fails_closed(monkeypatch):
    """Kütük bulunamazsa hiçbir muafiyet uygulanmamalı — sessizce serbest değil."""
    monkeypatch.setattr(policy, "_candidate_paths", lambda: ["/nonexistent/tool_actions.py"])
    policy.reset_cache()
    assert not policy.ledger_available()
    assert not policy.is_unity_mcp_read_only(f"{UNITY}manage_scene", {"action": "get_hierarchy"})
