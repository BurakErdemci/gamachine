"""Video failure notices follow the turn's language; the default stays Turkish."""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import providers.video_extract as ve

_403 = b"ERROR: unable to download video data: HTTP Error 403: Forbidden"


def _failure(source, language=None):
    kwargs = {} if language is None else {"language": language}
    with unittest.TestCase().assertRaises(ve.VideoPipelineError) as ctx:
        ve.extract(source, ".", "tag", **kwargs)
    return ctx.exception


class TestVideoMessagesLanguage(unittest.TestCase):
    def _patch(self, name, value):
        orig = getattr(ve, name)
        setattr(ve, name, value)
        self.addCleanup(lambda: setattr(ve, name, orig))

    def _download_fails(self):
        def fake_run(cmd, timeout, check=True):
            raise subprocess.CalledProcessError(1, cmd, stderr=_403)
        self._patch("_run", fake_run)
        self._patch("missing_binaries", lambda need_ytdlp: [])

    def test_download_failure_english_and_turkish(self):
        self._download_fails()
        src = {"kind": "url", "url": "https://www.youtube.com/shorts/abc"}
        en = _failure(src, "en")
        tr = _failure(src)
        self.assertEqual(en.code, "video_download_failed")
        self.assertEqual(tr.code, "video_download_failed")
        self.assertIn("refused the download", en.message)
        self.assertNotIn("sohbet metinle", en.message)
        self.assertIn("Video sitesi indirmeyi reddetti", tr.message)
        self.assertEqual(_failure(src, "tr").message, tr.message)

    def test_missing_binary_english_and_turkish(self):
        self._patch("missing_binaries", lambda need_ytdlp: ["ffmpeg"])
        src = {"kind": "path", "path": "x.mp4"}
        en = _failure(src, "en")
        tr = _failure(src)
        self.assertEqual(en.code, "video_binary_missing")
        self.assertEqual(tr.code, "video_binary_missing")
        self.assertIn("ffmpeg", en.message)
        self.assertIn("was not found on this computer", en.message)
        self.assertIn("bulunamadı", tr.message)
        self.assertEqual(en.detail, tr.detail)

    def test_download_failure_variants_have_english_twins(self):
        for stderr in ["", "Private video", "not available in your country", "HTTP Error 403"]:
            en = ve.download_failure_message(stderr, "en")
            self.assertIn("the chat continues with text", en)
            self.assertNotEqual(en, ve.download_failure_message(stderr))


if __name__ == "__main__":
    unittest.main()


def test_runner_passes_the_turn_language_to_extract_and_its_fallback():
    import asyncio
    from unittest import mock
    from agentic.agent_runner import AgentRunner
    from providers import video_extract as ve

    seen = []

    def fake_extract(src, ws, tag, cancel=None, language="tr"):
        seen.append(language)
        raise ValueError("boom")

    runner = AgentRunner(provider_type="google", api_key="k", model_name="gemini",
                         workspace_path=".", videos=[{"kind": "path", "path": "c.mp4"}],
                         language="en")
    with mock.patch.object(ve, "extract", fake_extract):
        _, warnings = asyncio.run(runner._prepare_videos("hi"))
    assert seen == ["en"]
    assert warnings[0]["code"] == "video_extract_failed"
    assert "chat continues with text" in warnings[0]["message"]
