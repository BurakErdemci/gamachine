"""Promises about what a backend restart keeps."""
from client import USER_ID, Api

REPLY = "Hello from the contract fake."


def test_data_survives_a_backend_restart(backend, api):
    """A conversation, its messages and a saved key are still there after the backend is killed and started again on the same data."""
    conv_id = api.new_conversation(title="Survives restart", model="contract-text")
    api.chat(conv_id, "Keep me.")
    # The first reply titles the chat after its message; the rename is the user's last word.
    api.ok("PUT", f"/conversations/{conv_id}", json={"title": "Survives restart"})
    api.ok("POST", "/api-keys/save", json={"provider_type": "groq", "api_key": "gsk-contract-restart-key"})
    backend.restart()
    after = Api(backend.url, backend.token)
    try:
        titles = {c["id"]: c["title"] for c in after.ok("GET", f"/conversations/{USER_ID}")}
        assert titles.get(conv_id) == "Survives restart"
        messages = [(m["role"], m["content"]) for m in after.ok("GET", f"/conversations/{conv_id}/messages")]
        assert messages[-2:] == [("user", "Keep me."), ("assistant", REPLY)]
        assert "groq" in after.ok("GET", f"/api-keys/{USER_ID}")["providers_with_keys"]
    finally:
        after.close()
