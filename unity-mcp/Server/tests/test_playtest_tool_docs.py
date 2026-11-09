"""Agent-visible playtest/build tool docs carry the hints agents otherwise learn from errors."""
import typing

from services.registry import get_registered_tools
import services.tools.execute_menu_item  # noqa: F401  (registers the tool)
import services.tools.play_capture  # noqa: F401
import services.tools.play_run as play_run_mod
import services.tools.play_session  # noqa: F401
import services.tools.play_step as play_step_mod


def _description(name: str) -> str:
    return next(t for t in get_registered_tools() if t["name"] == name)["description"]


def _param_doc(func, param: str) -> str:
    hint = typing.get_type_hints(func, include_extras=True)[param]
    return " ".join(str(m) for m in getattr(hint, "__metadata__", ()))


def test_until_shape_documented():
    for func in (play_run_mod.play_run, play_step_mod.play_step):
        doc = _param_doc(func, "until")
        assert "hook" in doc and "value" in doc
        for op in ("==", "!=", "<", ">", "<=", ">="):
            assert op in doc


def test_sample_seconds_range_documented():
    assert "0.5..60" in _param_doc(play_run_mod.play_run, "sample_seconds")


def test_seed_semantics_documented():
    assert "UnityEngine.Random" in _description("play_session")
    assert "System.Random" in _description("play_session")
    assert "Deterministic" not in _description("play_session")


def test_capture_path_is_full_resolution():
    description = _description("play_capture")
    assert "full-resolution" in description and "path" in description


def test_hook_listing_hint():
    assert "game_hooks action=list" in _description("play_session")


def test_menu_item_points_to_manage_build():
    assert "manage_build" in _description("execute_menu_item")
