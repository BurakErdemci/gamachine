import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import omnisharp.omnisharp_manager as om
from omnisharp.omnisharp_manager import _path_to_uri, _uri_to_path, _lsp_diag_to_problem


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(om, "_WINDOWS_PATHS", True)


@pytest.fixture
def posix(monkeypatch):
    monkeypatch.setattr(om, "_WINDOWS_PATHS", False)


def test_uri_roundtrip_windows(windows):
    p = "C:\\Unity Projeler\\TPS Shooter Game\\Assets\\Player.cs"
    uri = _path_to_uri(p)
    assert uri == "file:///C%3A/Unity%20Projeler/TPS%20Shooter%20Game/Assets/Player.cs"
    assert _uri_to_path(uri).replace("/", "\\") == p


@pytest.mark.parametrize("flag", [True, False])
def test_uri_roundtrip_posix_keeps_the_root(monkeypatch, flag):
    # Pure string work, so this runs the POSIX case on every OS.
    monkeypatch.setattr(om, "_WINDOWS_PATHS", flag)
    p = "/tmp/pytest-of-runner/pytest-0/ws/Assets/Scripts/New.cs"
    assert _uri_to_path(_path_to_uri(p)) == p
    assert _uri_to_path("file:///Users/u/My%20Game/A.cs") == "/Users/u/My Game/A.cs"


def test_uri_to_path_windows_forms(windows):
    assert _uri_to_path("file:///c%3A/Users/A.cs") == "c:/Users/A.cs"
    assert _uri_to_path("file:///C:/Users/A.cs") == "C:/Users/A.cs"
    assert _uri_to_path("file://server/share/A.cs") == "//server/share/A.cs"


def test_posix_drive_like_root_keeps_its_slash(posix):
    # `/a:/x` is an ordinary POSIX path; only Windows reads `/X:` as a drive.
    p = "/a:/workspace/File.cs"
    assert _uri_to_path(_path_to_uri(p)) == p
    assert _uri_to_path("file:///c%3A/Users/A.cs") == "/c:/Users/A.cs"


@pytest.mark.parametrize("flag", [True, False])
@pytest.mark.parametrize("path, uri", [
    (r"\\server\share\File.cs", "file://server/share/File.cs"),
    ("//server/share/File.cs", "file://server/share/File.cs"),
    (r"\\server\My Share\My Game\A #1.cs", "file://server/My%20Share/My%20Game/A%20%231.cs"),
    (r"\\localhost\c$\Game\A.cs", "file:////localhost/c%24/Game/A.cs"),
])
def test_unc_uses_the_authority_and_round_trips(monkeypatch, flag, path, uri):
    monkeypatch.setattr(om, "_WINDOWS_PATHS", flag)
    assert _path_to_uri(path) == uri
    assert _uri_to_path(uri) == path.replace("\\", "/")


def test_unc_decoded_path_keys_like_the_native_path(windows):
    # Diagnostics are stored under the decoded URI path and looked up under
    # the abspath the /lsp route hands in; on Windows both must give one key.
    if os.name != "nt":
        pytest.skip("ntpath key normalisation")
    native = r"\\server\share\Proj\Assets\A.cs"
    decoded = _uri_to_path(_path_to_uri(native))
    assert om._norm_key(decoded) == om._norm_key(os.path.abspath(native))
    assert om._source_key(decoded) == om._source_key(os.path.abspath(native))


def test_diag_conversion_one_based():
    d = {"range": {"start": {"line": 4, "character": 2}, "end": {"line": 4, "character": 9}},
         "message": "x", "severity": 1}
    prob = _lsp_diag_to_problem("Assets/Player.cs", d)
    assert prob == {"file": "Assets/Player.cs", "line": 5, "column": 3,
                    "endColumn": 10, "message": "x", "severity": "error"}
