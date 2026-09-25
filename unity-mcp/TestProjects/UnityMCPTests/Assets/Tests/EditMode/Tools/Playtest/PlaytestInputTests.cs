using System;
using System.Collections;
using System.Reflection;
using MCPForUnity.Runtime.Playtest;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools.Playtest
{
    public class PlaytestInputTests
    {
        private const BindingFlags Private = BindingFlags.NonPublic | BindingFlags.Static;

        // This assembly does not reference the Input System, so its settings and devices are reached by reflection.
        private static int SessionDeviceCount()
        {
            var inputSystem = Type.GetType("UnityEngine.InputSystem.InputSystem, Unity.InputSystem");
            int n = 0;
            foreach (var device in (IEnumerable)inputSystem.GetProperty("devices").GetValue(null))
            {
                var name = (string)device.GetType().GetProperty("name").GetValue(device);
                if (name.StartsWith("Playtest", StringComparison.Ordinal)) n++;
            }
            return n;
        }

        /// <summary>A domain reload mid-session clears PlaytestInput's statics; model it by clearing them.</summary>
        private static void LoseStatics()
        {
            typeof(PlaytestInput).GetField("s_SessionActive", Private).SetValue(null, false);
            typeof(PlaytestInput).GetField("s_Keyboard", Private).SetValue(null, null);
        }

        [Test]
        public void OriginalSettings_SurviveADomainReloadMidSession()
        {
            if (!PlaytestInput.Supported) Assert.Ignore("needs the Input System package");
            var settings = PlaytestInput.SettingsObject;
            var background = settings.GetType().GetProperty("backgroundBehavior");
            var editorBehavior = settings.GetType().GetProperty("editorInputBehaviorInPlayMode");
            var background0 = background.GetValue(settings);
            var editorBehavior0 = editorBehavior.GetValue(settings);
            try
            {
                PlaytestInput.BeginSession();
                LoseStatics();
                var again = PlaytestInput.BeginSession();
                Assert.AreEqual(background0.ToString(), (string)again["background_behavior_before"]);
                Assert.AreEqual(editorBehavior0.ToString(), (string)again["editor_input_behavior_before"]);

                LoseStatics();
                PlaytestInput.EndSession();
                Assert.AreEqual(background0, background.GetValue(settings));
                Assert.AreEqual(editorBehavior0, editorBehavior.GetValue(settings));
                Assert.AreEqual(0, SessionDeviceCount());
            }
            finally
            {
                PlaytestInput.EndSession();
                background.SetValue(settings, background0);
                editorBehavior.SetValue(settings, editorBehavior0);
            }
        }
    }
}
