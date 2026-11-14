"""`_prepare_videos`: frames in, reasons out, secrets not.

The `cancel=None` in every fake `extract` here is the stop switch
`_prepare_videos` now hands to the worker; see `test_video_cancellation.py` for
what it is for (cancelling a chat used to leave the download running).
"""
import os, sys, asyncio, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from agentic.agent_runner import AgentRunner
import agentic.agent_runner as ar
import providers.video_extract as ve


def _runner(videos):
    return AgentRunner(provider_type="google", api_key="k", model_name="gemini",
                       workspace_path=".", videos=videos,
                       images=["data:image/png;base64,AAAA"])


class TestPrepareVideos(unittest.TestCase):
    def test_appends_frames_and_block(self):
        def fake_extract(src, ws, tag, cancel=None, language="tr"):
            return ve.ExtractResult(
                ["data:image/jpeg;base64,BBBB", "data:image/jpeg;base64,CCCC"],
                "[00:00:01] selam",
                {"name": "c.mp4", "duration_s": 5, "frame_count": 2, "dropped": 0})
        with mock.patch.object(ve, "extract", fake_extract):
            r = _runner([{"kind": "path", "path": "c.mp4"}])
            msg, warnings = asyncio.run(r._prepare_videos("orijinal mesaj"))
        self.assertEqual(warnings, [])
        self.assertEqual(len(r.images), 3)                       # 1 mevcut + 2 kare
        self.assertEqual(r.images[-1], "data:image/jpeg;base64,CCCC")
        self.assertIn("[VİDEO]", msg)
        self.assertIn("orijinal mesaj", msg)
        self.assertIn("selam", msg)

    def test_noop_without_videos(self):
        r = _runner(None)
        msg, warnings = asyncio.run(r._prepare_videos("x"))
        self.assertEqual(warnings, [])
        self.assertEqual(msg, "x")
        self.assertEqual(len(r.images), 1)

    def test_extract_error_is_soft(self):
        def boom(src, ws, tag, cancel=None, language="tr"):
            raise RuntimeError("ffmpeg patladı")
        with mock.patch.object(ve, "extract", boom):
            r = _runner([{"kind": "url", "url": "http://x"}])
            msg, warnings = asyncio.run(r._prepare_videos("devam"))
        self.assertIn("devam", msg)
        self.assertEqual(len(r.images), 1)                       # çökme yok, kare eklenmez

    def test_pipeline_error_becomes_a_warning(self):
        def boom(src, ws, tag, cancel=None, language="tr"):
            raise ve.VideoPipelineError(
                "video_binary_missing", "yt-dlp bulunamadı.",
                stage="binary_resolve", detail="çözülemeyen binary: yt-dlp")
        with mock.patch.object(ve, "extract", boom):
            r = _runner([{"kind": "url", "url": "http://x"}])
            _msg, warnings = asyncio.run(r._prepare_videos("devam"))
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], "video_binary_missing")
        # Eksik programın adı KULLANICIYA GÖRÜNEN mesajda; `detail` yalnız aşama
        # ve hata cinsi taşır (aşağıdaki sızıntı testinin gerekçesi).
        self.assertIn("yt-dlp", warnings[0]["message"])
        self.assertIn("binary_resolve", warnings[0]["detail"])

    def test_warning_detail_carries_no_paths_or_urls(self):
        """Denetim bulgusu (30 Ağu 2026): alt süreç hatasının metni tüm argv'yi
        taşıyor — çözülmüş binary yolu, Windows ev dizini (yani hesap adı),
        çalışma alanı yolu ve sorgu dizesiyle birlikte hedef URL. Route katmanı
        ham istisna metnini istemciye BİLEREK vermiyor; bu kanal onu atlıyordu.
        """
        secret_url = "https://youtu.be/x?access_token=gizli"
        raw = ("Command '['C:\\\\Users\\\\burcu\\\\bin\\\\yt-dlp.exe', '--no-playlist', "
               f"'--', '{secret_url}']' returned non-zero exit status 1.")

        def boom(src, ws, tag, cancel=None, language="tr"):
            raise ve.VideoPipelineError(
                "video_download_failed", "Videonun linki indirilemedi.",
                stage="download", detail=f"CalledProcessError: {raw}")

        with mock.patch.object(ve, "extract", boom):
            r = _runner([{"kind": "url", "url": secret_url}])
            msg, warnings = asyncio.run(r._prepare_videos("devam"))

        blob = warnings[0]["detail"] + warnings[0]["message"] + msg
        for leaked in ("burcu", "yt-dlp.exe", "access_token", "C:\\\\Users", "--no-playlist"):
            self.assertNotIn(leaked, blob)
        self.assertIn("download", warnings[0]["detail"])

    def test_unclassified_error_also_becomes_a_warning(self):
        def boom(src, ws, tag, cancel=None, language="tr"):
            raise RuntimeError("ffmpeg patladı")
        with mock.patch.object(ve, "extract", boom):
            r = _runner([{"kind": "url", "url": "http://x"}])
            _msg, warnings = asyncio.run(r._prepare_videos("devam"))
        self.assertEqual([w["code"] for w in warnings], ["video_extract_failed"])


class TestWarningReachesTheStream(unittest.TestCase):
    """"The function was called" is not "the user saw it": this measures that
    the warning really enters the SSE stream, not what `_prepare_videos`
    returned."""

    def test_warning_event_is_serialized_into_the_sse_stream(self):
        async def _fake_simple(self, msg):
            yield ar.AgentEvent("response", {"content": "ok"})
            yield ar._done_event(1)

        def boom(src, ws, tag, cancel=None, language="tr"):
            raise ve.VideoPipelineError(
                "video_binary_missing", "ffmpeg bulunamadı.",
                stage="binary_resolve", detail="çözülemeyen binary: ffmpeg")

        async def _collect(runner):
            return [e async for e in runner.run("şu videoya bak")]

        with mock.patch.object(ve, "extract", boom), \
             mock.patch.object(ar.AgentRunner, "_run_simple", _fake_simple):
            r = ar.AgentRunner(provider_type="bilinmeyen", api_key="k",
                               model_name="m", workspace_path=".",
                               videos=[{"kind": "url", "url": "http://x"}])
            events = asyncio.run(_collect(r))

        sse = [e.to_sse() for e in events if e.type == "warning"]
        self.assertEqual(len(sse), 1)
        self.assertIn('"type": "warning"', sse[0])
        self.assertIn('"code": "video_binary_missing"', sse[0])
        self.assertIn("ffmpeg", sse[0])
        # The warning must precede the provider output, so the user sees the reason
        self.assertEqual(events[0].type, "warning")


if __name__ == "__main__":
    unittest.main()
