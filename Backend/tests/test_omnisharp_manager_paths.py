import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from omnisharp.omnisharp_manager import _path_to_uri, _uri_to_path, _lsp_diag_to_problem


def test_uri_roundtrip_windows():
    p = "C:\\Unity Projeler\\TPS Shooter Game\\Assets\\Player.cs"
    uri = _path_to_uri(p)
    assert uri.startswith("file:///")
    assert _uri_to_path(uri).replace("/", "\\") == p


def test_uri_roundtrip_posix_keeps_the_root():
    # Pure string work, so this runs the POSIX case on every OS.
    p = "/tmp/pytest-of-runner/pytest-0/ws/Assets/Scripts/New.cs"
    assert _uri_to_path(_path_to_uri(p)) == p
    assert _uri_to_path("file:///Users/u/My%20Game/A.cs") == "/Users/u/My Game/A.cs"


def test_uri_to_path_windows_forms():
    assert _uri_to_path("file:///c%3A/Users/A.cs") == "c:/Users/A.cs"
    assert _uri_to_path("file://server/share/A.cs") == "//server/share/A.cs"


def test_diag_conversion_one_based():
    d = {"range": {"start": {"line": 4, "character": 2}, "end": {"line": 4, "character": 9}},
         "message": "x", "severity": 1}
    prob = _lsp_diag_to_problem("Assets/Player.cs", d)
    assert prob == {"file": "Assets/Player.cs", "line": 5, "column": 3,
                    "endColumn": 10, "message": "x", "severity": "error"}
