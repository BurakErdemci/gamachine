import pytest

from computer_use.policy import risky_reason


QUIT_TEXT = "Uygulamadan çıkılsın mı? Kaydedilmemiş iş kaybolabilir."
DELETE_TEXT = "Riskli klavye kısayolu: cmd+delete"


@pytest.mark.parametrize("explicit", [False, True], ids=["shorthand", "tool-args"])
@pytest.mark.parametrize("keys,expected", [
    (["delete", "q"], DELETE_TEXT + " · " + QUIT_TEXT),
    (["q", "delete"], QUIT_TEXT + " · " + DELETE_TEXT),
    (["q", "delete", "q", "delete"], QUIT_TEXT + " · " + DELETE_TEXT),
    (["q", "q"], QUIT_TEXT),
    (["q"], QUIT_TEXT),
    (["s", "q"], QUIT_TEXT),
    (["delete"], DELETE_TEXT),
    (["s", "w"], ""),
    ([], ""),
])
def test_batch_names_all_distinct_risks_in_first_seen_order(explicit, keys, expected):
    steps = []
    for key in keys:
        args = {"keys": ["cmd", key]}
        steps.append({"tool": "hotkey", "args": args} if explicit else {"hotkey": args})

    assert risky_reason("computer_run_steps", {"steps": steps}) == expected
