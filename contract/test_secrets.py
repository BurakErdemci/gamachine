"""Promises about saved secrets."""
from client import USER_ID

ACTIVE_SECRET = "zq7k-ACTIVE-9f3a1c5e7b2d4068"
OTHER_SECRET = "wx2m-OTHER-8d6b4f0a2c9e1753"
SUITE_KEY = "sk-contract-fake-key"  # the key the backend fixture saved for the active provider
SHOWN = 4  # the mask may show this many trailing characters
WINDOW = 8


def _hidden_windows(secret):
    """Every 8-character piece of the secret outside the characters the mask may show."""
    hidden = secret[:-SHOWN]
    return [hidden[i:i + WINDOW] for i in range(len(hidden) - WINDOW + 1)]


def test_a_saved_api_key_is_never_echoed_back(api):
    """After POST /api-keys/save, no settings GET returns the key of the active provider or of another one; at most its last four characters."""
    active = api.ok("GET", f"/get-ai-config/{USER_ID}")["provider_type"]
    other = "openrouter"
    assert active != other
    try:
        api.ok("POST", "/api-keys/save", json={"provider_type": active, "api_key": ACTIVE_SECRET})
        api.ok("POST", "/api-keys/save", json={"provider_type": other, "api_key": OTHER_SECRET})
        keys = api.ok("GET", f"/api-keys/{USER_ID}")
        assert {active, other} <= set(keys["providers_with_keys"])
        assert api.ok("GET", f"/get-ai-config/{USER_ID}")["provider_type"] == active
        bodies = {path: api.get(path).text for path in (
            f"/api-keys/{USER_ID}", f"/get-ai-config/{USER_ID}", "/me", "/health/auth",
            f"/provider-ready/{USER_ID}?provider_type={active}&model_name=contract-text",
            f"/provider-ready/{USER_ID}?provider_type={other}&model_name=contract-text")}
        for path, body in bodies.items():
            for secret in (ACTIVE_SECRET, OTHER_SECRET):
                assert secret not in body, f"{path} returned a saved key"
                for piece in _hidden_windows(secret):
                    assert piece not in body, f"{path} returned part of a saved key: {piece!r}"
    finally:
        api.ok("DELETE", f"/api-keys/{USER_ID}/{other}")
        api.ok("POST", "/api-keys/save", json={"provider_type": active, "api_key": SUITE_KEY})
