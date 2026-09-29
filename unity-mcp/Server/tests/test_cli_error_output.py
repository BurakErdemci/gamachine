"""The CLI's text output of a failed command.

The Unity package answers a refused run_tests with a short error code ("compile") and the sentence that
says why in `message`; the text format used to print only the code.
"""

import pytest

from cli.utils.output import format_as_text


def test_error_code_is_followed_by_its_message():
    reply = {"success": False, "error": "compile",
             "message": "Scripts do not compile (data.compile lists the errors).",
             "data": {"reason": "compile_errors"}}

    text = format_as_text(reply)

    assert text == "❌ Error: compile - Scripts do not compile (data.compile lists the errors)."


@pytest.mark.parametrize("reply", [
    {"success": False, "error": "Unknown job_id."},
    {"success": False, "error": "same", "message": "same"},
])
def test_a_message_that_is_the_error_is_not_repeated(reply):
    assert format_as_text(reply) == f"❌ Error: {reply['error']}"


def test_message_only_reply_still_prints_the_message():
    assert format_as_text({"success": False, "message": "nothing came back"}) == "❌ Error: nothing came back"
