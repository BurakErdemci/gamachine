"""Promises about the conversation list."""
from client import USER_ID


def _listed(api):
    return {c["id"]: c for c in api.ok("GET", f"/conversations/{USER_ID}")}


def test_a_created_conversation_is_listed_with_its_title(api):
    """POST /conversations returns an id, and GET /conversations/{user} lists it with the title given."""
    conv_id = api.new_conversation(title="Listed chat")
    assert _listed(api)[conv_id]["title"] == "Listed chat"


def test_a_renamed_conversation_is_listed_with_its_new_title(api):
    """PUT /conversations/{id} with a title renames the chat in the list."""
    conv_id = api.new_conversation(title="Before rename")
    api.ok("PUT", f"/conversations/{conv_id}", json={"title": "After rename"})
    assert _listed(api)[conv_id]["title"] == "After rename"


def test_a_deleted_conversation_is_gone(api):
    """DELETE /conversations/{id} removes the chat: it leaves the list and its messages answer 404."""
    conv_id = api.new_conversation(title="To delete")
    assert conv_id in api.ok("DELETE", f"/conversations/{conv_id}")["deleted_ids"]
    assert conv_id not in _listed(api)
    assert api.get(f"/conversations/{conv_id}/messages").status_code == 404
    assert api.request("DELETE", f"/conversations/{conv_id}").status_code == 404
