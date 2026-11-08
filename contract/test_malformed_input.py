"""Promises about bad requests."""


def test_a_malformed_request_body_gets_a_422_without_a_stack_trace(api):
    """A body of the wrong shape answers 422 with a JSON `detail` naming the field, and no traceback or source path."""
    response = api.post("/conversations", json={"user_id": "not-a-number", "title": 7})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(item["loc"][-1] == "user_id" for item in detail)
    for leak in ("Traceback", 'File "', ".py\"", "site-packages"):
        assert leak not in response.text


def test_a_body_that_is_not_json_gets_a_422(api):
    """A chat request whose body is not JSON answers 422, not 500."""
    response = api.post("/chat-stream", content=b"{not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert "Traceback" not in response.text
