"""stdio: an unnamed call with several Editors connected is refused, not guessed.

Audit 25 Sep 2026: UnityConnectionPool._resolve_instance_id sent an unnamed
call to the most recently active Editor, so a write could land in a project
the caller never meant. The HTTP transport already refused this case and the
server instructions say the server errors. One connected Editor stays
automatic, and UNITY_MCP_DEFAULT_INSTANCE still picks one explicitly.
"""

from datetime import datetime, timedelta, timezone

import pytest

from models.models import UnityInstanceInfo
from transport.legacy.unity_connection import UnityConnectionPool

NOW = datetime.now(timezone.utc)


def _instance(name, hash_value, port, age_s):
    return UnityInstanceInfo(
        id=f"{name}@{hash_value}", name=name, path=f"/projects/{name}/Assets",
        hash=hash_value, port=port, status="running",
        last_heartbeat=NOW - timedelta(seconds=age_s))


A = _instance("A", "aaaa1111", 6401, age_s=10)
B = _instance("B", "bbbb2222", 6402, age_s=0)


def _pool(default=None):
    pool = UnityConnectionPool.__new__(UnityConnectionPool)
    pool._default_instance_id = default
    return pool


def test_one_instance_is_used_without_a_name():
    assert _pool()._resolve_instance_id(None, [A]) is A


def test_several_instances_without_a_name_are_refused():
    with pytest.raises(ConnectionError) as excinfo:
        _pool()._resolve_instance_id(None, [A, B])
    message = str(excinfo.value)
    assert "A@aaaa1111" in message and "B@bbbb2222" in message
    assert "unity_instance" in message


def test_the_most_recent_instance_is_not_picked_silently():
    """The exact shape of the finding: B is newer, and must not win by default."""
    with pytest.raises(ConnectionError):
        _pool()._resolve_instance_id(None, [B, A])


def test_a_configured_default_instance_still_resolves():
    assert _pool(default="A@aaaa1111")._resolve_instance_id(None, [A, B]) is A


@pytest.mark.parametrize("identifier", ["B@bbbb2222", "B", "bbbb", "6402"])
def test_a_named_instance_resolves_with_several_connected(identifier):
    assert _pool()._resolve_instance_id(identifier, [A, B]) is B


def test_no_instance_is_still_its_own_error():
    with pytest.raises(ConnectionError, match="No Unity Editor instances found"):
        _pool()._resolve_instance_id(None, [])
