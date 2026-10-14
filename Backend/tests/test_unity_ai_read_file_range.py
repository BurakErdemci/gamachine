"""read_file range support for the unityai MCP file tools."""
import asyncio
import os
import sys

import pytest

_APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from unity_ai_mcp.tools.file_tools import register_file_tools


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[kwargs.get("name", fn.__name__)] = fn
            return fn
        return deco


@pytest.fixture
def read_file(tmp_path):
    fake = _FakeMCP()
    register_file_tools(fake, lambda: str(tmp_path))
    fn = fake.tools["read_file"]

    def call(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return call


def _write(tmp_path, n, name="f.txt"):
    (tmp_path / name).write_text("".join(f"line {i}\n" for i in range(1, n + 1)), encoding="utf-8")


def test_short_file_default_returns_whole_file(read_file, tmp_path):
    _write(tmp_path, 10)
    assert read_file("f.txt") == "".join(f"line {i}\n" for i in range(1, 11))


def test_long_file_default_returns_first_500_with_footer(read_file, tmp_path):
    _write(tmp_path, 1200)
    out = read_file("f.txt")
    assert out.startswith("line 1\n")
    assert "line 500\n" in out and "line 501" not in out
    assert out.endswith("\n... (1-500 / 1200 satır gösterildi; devamı için start_line=501)")


def test_last_range_has_no_footer(read_file, tmp_path):
    _write(tmp_path, 1200)
    out = read_file("f.txt", start_line=1001, max_lines=500)
    assert out == "".join(f"line {i}\n" for i in range(1001, 1201))


def test_middle_range_footer(read_file, tmp_path):
    _write(tmp_path, 1200)
    out = read_file("f.txt", start_line=101, max_lines=50)
    assert out.endswith("\n... (101-150 / 1200 satır gösterildi; devamı için start_line=151)")


def test_start_beyond_end(read_file, tmp_path):
    _write(tmp_path, 1200)
    assert read_file("f.txt", start_line=1201) == "Hata: start_line 1201 dosyanın 1200 satırını aşıyor."


def test_empty_file_returns_empty(read_file, tmp_path):
    (tmp_path / "e.txt").write_text("", encoding="utf-8")
    assert read_file("e.txt") == ""


@pytest.mark.parametrize("kwargs", [{"max_lines": 0}, {"max_lines": 2001}, {"start_line": 0}])
def test_invalid_arguments(read_file, tmp_path, kwargs):
    _write(tmp_path, 10)
    out = read_file("f.txt", **kwargs)
    assert out.startswith("Hata:") and "\n" not in out


def test_outside_workspace_still_refused(read_file, tmp_path):
    with pytest.raises(PermissionError):
        read_file("../outside.txt")
