from providers.agy_provider import AgyProvider


def test_prompt_lists_only_available_unity_tools(monkeypatch):
    provider = AgyProvider()
    monkeypatch.setattr(provider, "_launcher_path", lambda name: "test-launcher")
    monkeypatch.setattr(provider, "_ensure_exec", lambda path: None)

    prompt = provider._stream_instructions()

    assert "manage_gameobject" in prompt
    for tool in ("manage_fbx", "manage_animation", "run_tests"):
        assert tool not in prompt
