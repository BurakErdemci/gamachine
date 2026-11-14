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


class TestEveryVideoNoticeHasBothLanguages(unittest.TestCase):
    """Exact text of every user-facing failure, in both languages, with the same code."""

    def _patch(self, name, value):
        orig = getattr(ve, name)
        setattr(ve, name, value)
        self.addCleanup(lambda: setattr(ve, name, orig))

    def _check(self, source, code, tr, en):
        for language, expected in (("en", en), (None, tr), ("tr", tr)):
            err = _failure(source, language)
            self.assertEqual(err.code, code)
            self.assertEqual(err.message, expected)

    def test_file_not_found(self):
        self._patch("missing_binaries", lambda need_ytdlp: [])
        self._check({"kind": "path", "path": "no/such/clip.mp4"}, "video_download_failed",
                    "Eklenen video dosyası bulunamadı.", "The attached video file was not found.")

    def test_unrecognised_source(self):
        self._patch("missing_binaries", lambda need_ytdlp: [])
        self._check({"kind": "zzz"}, "video_extract_failed",
                    "Bu video kaynağı tanınmadı.", "This video source was not recognized.")

    def test_frame_extraction_failure_and_zero_frames(self):
        self._patch("missing_binaries", lambda need_ytdlp: [])
        self._patch("_probe_duration", lambda path: 10.0)
        src = {"kind": "path", "path": __file__}

        def boom(*a, **k):
            raise RuntimeError("ffmpeg died")
        self._patch("_extract_frames", boom)
        self._check(src, "video_extract_failed",
                    "Videodan görüntü çıkarılamadı; video atlandı, sohbet metinle sürüyor.",
                    "Frames could not be extracted from the video; the video was skipped and "
                    "the chat continues with text.")
        self._patch("_extract_frames", lambda *a, **k: [])
        self._check(src, "video_extract_failed",
                    "Videodan hiç görüntü çıkmadı (dosya bozuk ya da desteklenmeyen bir "
                    "biçimde olabilir); video atlandı, sohbet metinle sürüyor.",
                    "No frames came out of the video (the file may be corrupt or in an "
                    "unsupported format); the video was skipped and the chat continues with text.")

    def test_missing_binary_text(self):
        self._patch("missing_binaries", lambda need_ytdlp: ["ffmpeg", "ffprobe"])
        self._check({"kind": "path", "path": "x.mp4"}, "video_binary_missing",
                    "Videoyu işlemek için gereken ffmpeg ve ffprobe programı bu bilgisayarda "
                    "bulunamadı; video atlandı, sohbet metinle sürüyor.",
                    "The program needed to process the video (ffmpeg and ffprobe) was not found "
                    "on this computer; the video was skipped and the chat continues with text.")

    def test_each_download_failure_variant(self):
        cases = {
            "HTTP Error 403: Forbidden": (
                "Video sitesi indirmeyi reddetti (403) — genelde geçici bir kısıtlama, "
                "biraz sonra yeniden denemek işe yarayabilir; video atlandı, sohbet metinle sürüyor.",
                "The video site refused the download (403) - usually a temporary restriction, "
                "trying again a little later may work; the video was skipped and the chat "
                "continues with text."),
            "not available in your country": (
                "Video bu ülkeden izlenemiyor (yayıncı bölgesel kısıtlama koymuş); "
                "video atlandı, sohbet metinle sürüyor.",
                "The video cannot be watched from this country (the publisher set a regional "
                "restriction); the video was skipped and the chat continues with text."),
            "Private video": (
                "Video artık açık değil (özel, üyelere özel ya da kaldırılmış); "
                "video atlandı, sohbet metinle sürüyor.",
                "The video is no longer available (private, members-only or removed); "
                "the video was skipped and the chat continues with text."),
            "something else entirely": (
                "Videonun linki indirilemedi (link kapalı, bölgesel kısıtlı ya da "
                "ağ engelli olabilir); video atlandı, sohbet metinle sürüyor.",
                "The video link could not be downloaded (it may be private, region-locked or "
                "blocked); the video was skipped and the chat continues with text."),
        }
        self._patch("missing_binaries", lambda need_ytdlp: [])
        for stderr, (tr, en) in cases.items():
            def fail(url, tmp_root, _s=stderr):
                raise subprocess.CalledProcessError(1, "yt-dlp", stderr=_s.encode())
            self._patch("_download_url", fail)
            self._check({"kind": "url", "url": "https://example.com/v"}, "video_download_failed", tr, en)


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
