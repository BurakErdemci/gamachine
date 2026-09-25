import pytest

from .test_helpers import DummyContext


@pytest.mark.asyncio
async def test_manage_gameobject_uses_request_state(monkeypatch):
    """Tools route to the instance the middleware put in request state."""
    ctx = DummyContext()
    # What UnityInstanceMiddleware injects for this request
    await ctx.set_state("unity_instance", "SessionProj@AAAA1111", serializable=False)

    captured = {}

    # Monkeypatch transport to capture the resolved instance_id
    async def fake_send(command_type, params, **kwargs):
        captured["command_type"] = command_type
        captured["params"] = params
        captured["instance_id"] = kwargs.get("instance_id")
        return {"success": True, "data": {}}

    import services.tools.manage_gameobject as mg
    monkeypatch.setattr(
        "services.tools.manage_gameobject.async_send_command_with_retry",
        fake_send,
    )

    # Act: call tool - should use the request state from context
    res = await mg.manage_gameobject(
        ctx,
        action="create",
        name="SessionSphere",
        primitive_type="Sphere",
    )

    # Assert: uses the routed instance
    assert res.get("success") is True
    assert captured.get("command_type") == "manage_gameobject"
    assert captured.get("instance_id") == "SessionProj@AAAA1111"


@pytest.mark.asyncio
async def test_manage_gameobject_without_active_instance(monkeypatch):
    """Test that tools work with no active instance set (uses None/default)"""

    ctx = DummyContext()
    # Don't set any state in context: nothing was routed for this request

    captured = {}

    async def fake_send(command_type, params, **kwargs):
        captured["instance_id"] = kwargs.get("instance_id")
        return {"success": True, "data": {}}

    import services.tools.manage_gameobject as mg
    monkeypatch.setattr(
        "services.tools.manage_gameobject.async_send_command_with_retry",
        fake_send,
    )

    # Act: call without active instance
    res = await mg.manage_gameobject(
        ctx,
        action="create",
        name="DefaultSphere",
        primitive_type="Sphere",
    )

    # Assert: uses None (connection pool will pick default)
    assert res.get("success") is True
    assert captured.get("instance_id") is None
