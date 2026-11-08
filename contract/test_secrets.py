"""Promises about saved secrets."""
from client import USER_ID

SECRET = "sk-contract-SECRET-0123456789abcdef"


def test_a_saved_api_key_is_never_echoed_back(api):
    """After POST /api-keys/save, no settings GET returns the key; at most its last four characters."""
    api.ok("POST", "/api-keys/save", json={"provider_type": "openrouter", "api_key": SECRET})
    keys = api.ok("GET", f"/api-keys/{USER_ID}")
    assert "openrouter" in keys["providers_with_keys"]
    bodies = [api.get(path).text for path in (
        f"/api-keys/{USER_ID}", f"/get-ai-config/{USER_ID}", "/me",
        f"/provider-ready/{USER_ID}?provider_type=openrouter&model_name=contract-text")]
    for body in bodies:
        assert SECRET not in body
        assert SECRET[:-4] not in body
    api.ok("DELETE", f"/api-keys/{USER_ID}/openrouter")
