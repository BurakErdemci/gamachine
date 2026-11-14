"""A turn on a model the account lacks ends with one coded, translated error.

Measured on agy 1.5.9 (14 Nov 2026): an unknown model slug exits rc 1 with one
ERROR result line and the same text on stderr. The fake child replays that
text; no agy runs.
"""
import asyncio
import json
import unittest

from providers import agy_session
from tests.test_agy_stream_session import TURNS
from tests.test_agy_turn_end import QUOTA_TEXT, _Harness, failed_result

AVAILABLE = ["Gemini 4 Argon (Medium)", "Gemini 4 Argon (High)", "GPT-OSS 120B (Medium)"]
UNKNOWN_MODEL_TEXT = (
    'invalid model selection (--model "gemini-4-carbon-high" --effort ""): model '
    "gemini-4-carbon-high is not recognized as a known model or custom model in settings\n"
    "Available models:\n" + "".join(f"  {name}\n" for name in AVAILABLE)).rstrip("\n")


class TestModelUnavailable(_Harness):
    async def run_turn(self, error, language, stderr=None):
        stderr = f"error: {error}\n" if stderr is None else stderr
        self.stderr_by_spawn = [stderr.encode()]
        self.plans = [{"turns": [[failed_result(error, num_turns=0)]], "crash": True}]
        return await asyncio.wait_for(
            self.collect(model="gemini-4-carbon", language=language), 5)

    async def test_turkish_message_names_the_model_and_the_accounts_models(self):
        events = await self.run_turn(UNKNOWN_MODEL_TEXT, "tr")
        errors = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(errors), 1, events)
        self.assertEqual(errors[0]["code"], "agy_model_unavailable")
        message = errors[0]["message"]
        self.assertIn("**gemini-4-carbon** modeli Antigravity hesabında kullanılamıyor", message)
        self.assertIn("aboneliğin bu modeli desteklemiyor", message)
        self.assertIn("Hesabının kullanabildiği modeller: " + ", ".join(AVAILABLE) + ".", message)
        self.assertNotIn("invalid model selection", message)
        self.assertNotIn("Available models", message)

    async def test_english_message_names_the_model_and_the_accounts_models(self):
        events = await self.run_turn(UNKNOWN_MODEL_TEXT, "en")
        errors = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(errors), 1, events)
        self.assertEqual(errors[0]["code"], "agy_model_unavailable")
        message = errors[0]["message"]
        self.assertIn("**gemini-4-carbon** is not available on your Antigravity account", message)
        self.assertIn("Models your account can use: " + ", ".join(AVAILABLE) + ".", message)
        self.assertNotIn("invalid model selection", message)

    async def test_without_a_model_list_the_second_sentence_is_left_out(self):
        text = UNKNOWN_MODEL_TEXT.split("\nAvailable models:")[0]
        events = await self.run_turn(text, "en")
        error = [e for e in events if e["type"] == "error"][0]
        self.assertEqual(error["code"], "agy_model_unavailable")
        self.assertNotIn("Models your account can use", error["message"])

    async def test_other_failures_keep_the_plain_error(self):
        events = await self.run_turn("something broke", "en")
        errors = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(errors), 1, events)
        self.assertNotIn("code", errors[0])
        self.assertIn("something broke", errors[0]["message"])

    async def test_a_quota_failure_stays_a_quota_error(self):
        events = await self.run_turn(QUOTA_TEXT, "en", stderr=QUOTA_TEXT + "\n")
        errors = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(errors), 1, json.dumps(events))
        self.assertEqual(errors[0]["code"], "agy_quota")

    async def test_a_side_turn_gets_the_same_coded_error(self):
        self.stderr_by_spawn = [f"error: {UNKNOWN_MODEL_TEXT}\n".encode()]
        self.plans = [{"turns": [[failed_result(UNKNOWN_MODEL_TEXT, num_turns=0)]], "crash": True}]
        events = await asyncio.wait_for(self.collect(
            agy_session.get_session(-5), side=True, model="gemini-4-carbon", language="tr"), 5)
        errors = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(errors), 1, events)
        self.assertEqual(errors[0]["code"], "agy_model_unavailable")
        self.assertIn("**gemini-4-carbon** modeli Antigravity hesabında kullanılamıyor",
                      errors[0]["message"])
        self.assertIn("Hesabının kullanabildiği modeller: " + ", ".join(AVAILABLE) + ".",
                      errors[0]["message"])
        self.assertNotIn("invalid model selection", errors[0]["message"])

    async def test_a_side_turn_keeps_the_plain_error_for_other_failures(self):
        self.plans = [{"turns": [[failed_result("something broke", num_turns=0)]], "crash": True}]
        events = await asyncio.wait_for(self.collect(
            agy_session.get_session(-5), side=True, model="gemini-4-carbon", language="en"), 5)
        errors = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(errors), 1, events)
        self.assertNotIn("code", errors[0])

    async def test_a_side_turn_on_a_used_up_quota_gets_the_quota_message(self):
        self.stderr_by_spawn = [(QUOTA_TEXT + "\n").encode()]
        self.plans = [{"turns": [[failed_result(QUOTA_TEXT, num_turns=0)]], "crash": True}]
        events = await asyncio.wait_for(self.collect(
            agy_session.get_session(-5), side=True, model="gemini-4-carbon", language="en"), 5)
        errors = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(errors), 1, events)
        self.assertEqual(errors[0]["code"], "agy_quota")
        self.assertNotIn("RESOURCE_EXHAUSTED", errors[0]["message"])

    async def test_quota_text_wins_over_the_unknown_model_line(self):
        text = UNKNOWN_MODEL_TEXT + "\n" + QUOTA_TEXT
        events = await self.run_turn(text, "en", stderr=text + "\n")
        errors = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(errors), 1, events)
        self.assertEqual(errors[0]["code"], "agy_quota")

    async def test_the_diagnostic_matches_whatever_its_letter_case(self):
        events = await self.run_turn(UNKNOWN_MODEL_TEXT.upper(), "en")
        error = [e for e in events if e["type"] == "error"][0]
        self.assertEqual(error["code"], "agy_model_unavailable")

    async def test_the_two_phrases_on_different_lines_do_not_classify(self):
        text = ('invalid model selection (--model "gemini-4-carbon-high"): rejected\n'
                "later: gemini-4-carbon-high is not recognized as a known model\n"
                "Available models:\n  Gemini 4 Argon (High)")
        events = await self.run_turn(text, "en")
        errors = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(errors), 1, events)
        self.assertNotIn("code", errors[0])
        self.assertIn("invalid model selection", errors[0]["message"])

    async def test_duplicate_names_in_the_list_are_kept_once_in_order(self):
        text = UNKNOWN_MODEL_TEXT + "\n  Gemini 4 Argon (Medium)\n  GPT-OSS 120B (Medium)"
        self.assertEqual(agy_session.model_unavailable_models(text), AVAILABLE)
        events = await self.run_turn(text, "en")
        error = [e for e in events if e["type"] == "error"][0]
        self.assertEqual(error["code"], "agy_model_unavailable")
        self.assertIn("Models your account can use: " + ", ".join(AVAILABLE) + ".",
                      error["message"])


if __name__ == "__main__":
    unittest.main()
