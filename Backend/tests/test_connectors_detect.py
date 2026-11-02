"""Blender detection with a fake filesystem and a fake `--version`; no real Blender is started."""
import os
from pathlib import Path

import pytest

from connectors import detect


@pytest.fixture(autouse=True)
def _fresh_cache():
    detect._cache.clear()
    yield
    detect._cache.clear()


def _builds(tmp_path, versions):
    """Real empty files (probe stats them) mapped to the `--version` text each would print."""
    outputs = {}
    for name, version in versions.items():
        path = tmp_path / name / "blender.exe"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
        outputs[str(path)] = f"Blender {version} LTS\n\tbuild date: x\n" if version else "garbage\n"
    return outputs


@pytest.mark.parametrize("text,expected", [
    ("Blender 5.2.0 LTS\nbuild", "5.2.0"), ("Blender 4.2\n", "4.2"), ("  Blender 3.6.12", "3.6.12"),
    ("Error: no\nBlender 5.2.0", None), ("", None), (None, None),
])
def test_parse_version_reads_only_the_first_line(text, expected):
    assert detect.parse_version(text) == expected


def test_no_blender_found():
    result = detect.detect("4.2", candidates=lambda: [], run=lambda p: pytest.fail("nothing to run"))
    assert result == {"found": [], "chosen": None, "custom_path": None}


def test_only_too_old_builds_are_found_but_none_is_chosen(tmp_path):
    outputs = _builds(tmp_path, {"a": "3.6.5", "b": "4.1.1"})
    result = detect.detect("4.2", candidates=lambda: list(outputs), run=outputs.__getitem__)
    assert [f["version"] for f in result["found"]] == ["4.1.1", "3.6.5"]
    assert result["chosen"] is None


def test_newest_build_meeting_the_minimum_is_chosen(tmp_path):
    outputs = _builds(tmp_path, {"a": "4.2.3", "b": "5.2.0", "c": "3.6.0", "d": None})
    result = detect.detect("4.2", candidates=lambda: list(outputs), run=outputs.__getitem__)
    assert [f["version"] for f in result["found"]] == ["5.2.0", "4.2.3", "3.6.0"]
    assert result["chosen"]["version"] == "5.2.0"


def test_custom_path_wins_over_detection(tmp_path):
    outputs = _builds(tmp_path, {"a": "5.2.0", "custom": "4.4.1"})
    custom = next(p for p in outputs if Path(p).parent.name == "custom")
    result = detect.detect("4.2", custom, candidates=lambda: [p for p in outputs if p != custom],
                           run=outputs.__getitem__)
    assert result["chosen"] == {"version": "4.4.1", "path": custom}
    assert result["custom_path"] == custom


def test_unparseable_custom_path_chooses_nothing(tmp_path):
    outputs = _builds(tmp_path, {"a": "5.2.0", "custom": None})
    custom = next(p for p in outputs if Path(p).parent.name == "custom")
    result = detect.detect("4.2", custom, candidates=lambda: [], run=outputs.__getitem__)
    assert result["chosen"] is None and result["custom_path"] == custom


def test_version_is_cached_per_file(tmp_path):
    outputs = _builds(tmp_path, {"a": "5.2.0"})
    calls = []

    def run(path):
        calls.append(path)
        return outputs[path]
    for _ in range(3):
        detect.detect("4.2", candidates=lambda: list(outputs), run=run)
    assert len(calls) == 1


def test_windows_candidates_cover_program_files_and_steam_libraries():
    pf, x86 = r"C:\Program Files", r"C:\Program Files (x86)"
    vdf = os.path.join(x86, "Steam", "steamapps", "libraryfolders.vdf")
    present = {
        os.path.join(pf, "Blender Foundation", "Blender 5.2", "blender.exe"),
        os.path.join(pf, "Blender Foundation", "Blender 4.2", "blender.exe"),
        os.path.join("D:\\SteamLibrary", "steamapps", "common", "Blender", "blender.exe"),
        vdf,
    }

    def glob(pattern):
        if pattern.startswith(pf + os.sep):
            return [p for p in present if p.startswith(os.path.join(pf, "Blender Foundation"))]
        return []
    found = detect.candidate_paths(
        env={"ProgramFiles": pf, "ProgramFiles(x86)": x86}, platform="win32", glob=glob,
        isfile=present.__contains__,
        read_text=lambda p: '"libraryfolders"\n{\n "1"\n {\n  "path"\t\t"D:\\\\SteamLibrary"\n }\n}')
    assert sorted(found) == sorted(p for p in present if p != vdf)


def test_macos_candidates():
    app = "/Applications/Blender.app/Contents/MacOS/Blender"
    found = detect.candidate_paths(env={"HOME": "/Users/u"}, platform="darwin",
                                   glob=lambda pattern: [app] if pattern.startswith("/Applications") else [],
                                   isfile=lambda p: p == app)
    assert found == [app]


def test_user_addons_dir_per_platform_and_override():
    assert detect.user_addons_dir("5.2.0", env={"APPDATA": r"C:\U\Roaming"}, platform="win32") == \
        Path(r"C:\U\Roaming") / "Blender Foundation" / "Blender" / "5.2" / "scripts" / "addons"
    assert detect.user_addons_dir("4.2.1", env={"HOME": "/Users/u"}, platform="darwin") == \
        Path("/Users/u/Library/Application Support/Blender/4.2/scripts/addons")
    assert detect.user_addons_dir("5.2.0", env={"BLENDER_USER_SCRIPTS": "/tmp/s"}, platform="win32") == \
        Path("/tmp/s") / "addons"


def test_at_least_compares_numerically():
    assert detect.at_least("4.10.0", "4.2") and detect.at_least("4.2", "4.2.0")
    assert not detect.at_least("4.1.9", "4.2")
