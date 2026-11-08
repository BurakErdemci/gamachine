"""The suite's only way to talk to the backend: HTTP with the session token, and SSE for chat turns."""
import httpx

import sse

TIMEOUT = httpx.Timeout(15.0, connect=5.0)
STREAM_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
USER_ID = 1  # The backend's single local user.


class Api:
    def __init__(self, base_url, token):
        self.base_url = base_url
        self.token = token
        self.http = httpx.Client(base_url=base_url, timeout=TIMEOUT, trust_env=False,
                                 headers={"X-Session-Token": token, "X-UI-Lang": "en"})

    def close(self):
        self.http.close()

    def request(self, method, path, **kwargs):
        return self.http.request(method, path, **kwargs)

    def get(self, path, **kwargs):
        return self.http.get(path, **kwargs)

    def post(self, path, **kwargs):
        return self.http.post(path, **kwargs)

    def ok(self, method, path, **kwargs):
        """The JSON body of a 2xx answer; anything else fails with the body in the message."""
        response = self.http.request(method, path, **kwargs)
        assert response.is_success, f"{method} {path} -> {response.status_code}: {response.text[:500]}"
        return response.json()

    def anonymous(self):
        """A client that sends no session token."""
        return httpx.Client(base_url=self.base_url, timeout=TIMEOUT, trust_env=False)

    # ── flows used by several promises ──────────────────────────────────────
    def new_conversation(self, title="Contract chat", model=None):
        conv = self.ok("POST", "/conversations", json={"user_id": USER_ID, "title": title})
        if model is not None:
            self.ok("POST", "/save-ai-config", json={
                "user_id": USER_ID, "provider_type": "openai", "model_name": model,
                "api_key": "sk-contract-fake-key", "conversation_id": conv["id"]})
        return conv["id"]

    def chat(self, conversation_id, message, on_event=None):
        """Send one chat turn and return its SSE events; `on_event(event, response)` may stop early by returning True."""
        events = []
        body = {"conversation_id": conversation_id, "user_id": USER_ID, "message": message,
                "language": "en"}
        with self.http.stream("POST", "/chat-stream", json=body, timeout=STREAM_TIMEOUT) as response:
            assert response.status_code == 200, response.read().decode("utf-8", "replace")[:500]
            assert response.headers["content-type"].startswith("text/event-stream")
            for event in sse.parse(response.iter_lines()):
                events.append(event)
                if on_event is not None and on_event(event, response):
                    break
        return events
