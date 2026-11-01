using System;
using System.IO;
using System.Net.Sockets;
using MCPForUnity.Editor.Constants;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Gamachine
{
    /// <summary>
    /// Applies the connection settings Gamachine writes to UserSettings/Gamachine/autoconnect.json.
    /// Without that file it does nothing, so projects that do not use Gamachine are unaffected.
    /// The code is static and every changing value lives in the file: Unity does not compile an
    /// edited script while its window is unfocused, but it keeps ticking EditorApplication.update
    /// and honours RequestScriptReload (measured 2 Nov 2026, Unity 6000.4).
    /// </summary>
    [InitializeOnLoad]
    internal static class GamachineAutoConnect
    {
        [Serializable]
        private class Settings
        {
            public bool autoStart;
            public int port;
            public string language;
            public string uvxPath;
            public string revision;
        }

        private const double PollSeconds = 2.0;
        private const string UiLanguageKey = "Gamachine.UiLanguage";
        private static readonly string SettingsPath;
        private static double _nextPoll;
        private static DateTime _lastWrite;
        private static long _lastLength = -1;

        static GamachineAutoConnect()
        {
            try
            {
                SettingsPath = Path.Combine(Path.GetDirectoryName(Application.dataPath), "UserSettings", "Gamachine", "autoconnect.json");
                if (!File.Exists(SettingsPath)) return;
                Snapshot(out _lastWrite, out _lastLength);
                TryApply();
                EditorApplication.update += Poll;
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[Gamachine] Could not apply {SettingsPath}: {e.Message}");
            }
        }

        private static bool Snapshot(out DateTime lastWrite, out long length)
        {
            var info = new FileInfo(SettingsPath);
            info.Refresh();
            if (!info.Exists)
            {
                lastWrite = default;
                length = -1;
                return false;
            }
            lastWrite = info.LastWriteTimeUtc;
            length = info.Length;
            return true;
        }

        private static void Poll()
        {
            try
            {
                double now = EditorApplication.timeSinceStartup;
                if (now < _nextPoll) return;
                _nextPoll = now + PollSeconds;
                if (!Snapshot(out DateTime lastWrite, out long length)) return;
                if (lastWrite == _lastWrite && length == _lastLength) return;
                _lastWrite = lastWrite;
                _lastLength = length;
                // Gamachine writes atomically, so a change is one new revision; reload so the
                // bridge reads the new preferences on the next domain load.
                if (TryApply()) EditorUtility.RequestScriptReload();
            }
            catch
            {
                // Never throw out of the update callback; the next change is tried again.
            }
        }

        /// <summary>False for a missing, half-written or invalid file: it is skipped until the next valid one.</summary>
        private static bool TryApply()
        {
            Settings settings;
            try
            {
                settings = JsonUtility.FromJson<Settings>(File.ReadAllText(SettingsPath));
            }
            catch
            {
                return false;
            }
            if (settings == null || settings.port <= 0 || settings.port > 65535 || string.IsNullOrEmpty(settings.revision))
                return false;

            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, $"http://127.0.0.1:{settings.port}");
            EditorPrefs.SetString(UiLanguageKey, settings.language == "tr" ? "tr" : "en");
            if (!string.IsNullOrEmpty(settings.uvxPath))
                EditorPrefs.SetString(EditorPrefKeys.UvxPathOverride, settings.uvxPath);

            // Connect on load only when our server already answers; otherwise the bridge logs
            // "Connection failed" on every reload.
            bool connect = settings.autoStart && ServerReachable(settings.port);
            EditorPrefs.SetBool(EditorPrefKeys.AutoStartOnLoad, connect);
            EditorPrefs.SetBool(EditorPrefKeys.ResumeHttpAfterReload, connect);
            return true;
        }

        private static bool ServerReachable(int port)
        {
            try
            {
                using (var client = new TcpClient())
                {
                    var connection = client.ConnectAsync("127.0.0.1", port);
                    return connection.Wait(150) && client.Connected;
                }
            }
            catch
            {
                return false;
            }
        }
    }
}
