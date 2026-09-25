using System;
using System.Collections.Generic;
using System.Globalization;
using Newtonsoft.Json.Linq;
using UnityEngine;
#if MCP_FOR_UNITY_INPUT_SYSTEM
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.Controls;
using UnityEngine.InputSystem.LowLevel;
#endif

namespace MCPForUnity.Runtime.Playtest
{
    /// <summary>
    /// Generic input path of a playtest session: session-owned virtual Keyboard/Mouse/Gamepad whose events for
    /// frame k are queued in InputSystem.onBeforeUpdate of frame k (Dynamic update), so polling
    /// (wasPressedThisFrame) and callbacks both see them in that frame. Measured in P2: this only reaches the game
    /// with backgroundBehavior = IgnoreFocus and editorInputBehaviorInPlayMode = AllDeviceInputAlwaysGoesToGameView,
    /// and never through the physical keyboard (the Input System disables it when the editor loses focus).
    /// </summary>
    public static class PlaytestInput
    {
        private sealed class Change
        {
            public string Kind;   // key | button | axis
            public string Name;
            public float Value;
        }

        private static readonly SortedDictionary<int, List<Change>> s_Queue = new SortedDictionary<int, List<Change>>();
        private static int s_LastPrologueFrame = -1;
        private static bool s_WarnedUnsupportedAutopilotInput;

        public const string UnsupportedMessage =
            "The Input System package (com.unity.inputsystem >= 1.1) is not installed, so key/button/axis input " +
            "is unsupported; drive input through the game's input.* hooks instead.";

#if MCP_FOR_UNITY_INPUT_SYSTEM
        public static bool Supported => true;

        private static bool s_SessionActive;
        private static bool s_Subscribed;
        private static InputSettings.BackgroundBehavior s_OrigBackground;
        private static InputSettings.EditorInputBehaviorInPlayMode s_OrigEditorBehavior;
        private static Keyboard s_Keyboard;
        private static Mouse s_Mouse;
        private static Gamepad s_Gamepad;

        /// <summary>True when frame prologues come from the Input System before-update hook (then the early Update skips).</summary>
        public static bool DrivesPrologue => s_Subscribed && InputSystem.settings.updateMode == InputSettings.UpdateMode.ProcessEventsInDynamicUpdate;

        public static UnityEngine.Object SettingsObject => InputSystem.settings;

        public static JObject BeginSession()
        {
            var settings = InputSystem.settings;
            if (!s_SessionActive && !LoadOriginals())
            {
                s_OrigBackground = settings.backgroundBehavior;
                s_OrigEditorBehavior = settings.editorInputBehaviorInPlayMode;
                SaveOriginals();
            }
            // Property setters apply the change in memory without dirtying the settings asset.
            settings.backgroundBehavior = InputSettings.BackgroundBehavior.IgnoreFocus;
            settings.editorInputBehaviorInPlayMode = InputSettings.EditorInputBehaviorInPlayMode.AllDeviceInputAlwaysGoesToGameView;
            s_SessionActive = true;
            EnsureKeyboard();
            if (!s_Subscribed)
            {
                InputSystem.onBeforeUpdate += OnBeforeUpdate;
                s_Subscribed = true;
            }
            return new JObject
            {
                ["background_behavior_before"] = s_OrigBackground.ToString(),
                ["editor_input_behavior_before"] = s_OrigEditorBehavior.ToString(),
                ["update_mode"] = settings.updateMode.ToString(),
            };
        }

        public static void EndSession()
        {
            if (s_Subscribed) InputSystem.onBeforeUpdate -= OnBeforeUpdate;
            s_Subscribed = false;
            s_Queue.Clear();
            RemoveDevice(ref s_Keyboard);
            RemoveDevice(ref s_Mouse);
            RemoveDevice(ref s_Gamepad);
            RemoveUnreferencedSessionDevices();
            if (s_SessionActive || LoadOriginals())
            {
                var settings = InputSystem.settings;
                settings.backgroundBehavior = s_OrigBackground;
                settings.editorInputBehaviorInPlayMode = s_OrigEditorBehavior;
            }
            ForgetOriginals();
            s_SessionActive = false;
            s_LastPrologueFrame = -1;
        }

        // The originals live in SessionState because a domain reload during a session clears these statics while the
        // changed settings stay in effect: they must be neither lost nor recaptured as new originals.
        private const string OriginalsKey = "MCPForUnity.Playtest.InputOriginals";

        private static bool LoadOriginals()
        {
#if UNITY_EDITOR
            var parts = UnityEditor.SessionState.GetString(OriginalsKey, "").Split('|');
            if (parts.Length == 2
                && Enum.TryParse<InputSettings.BackgroundBehavior>(parts[0], out var background)
                && Enum.TryParse<InputSettings.EditorInputBehaviorInPlayMode>(parts[1], out var editorBehavior))
            {
                s_OrigBackground = background;
                s_OrigEditorBehavior = editorBehavior;
                return true;
            }
#endif
            return false;
        }

        private static void SaveOriginals()
        {
#if UNITY_EDITOR
            UnityEditor.SessionState.SetString(OriginalsKey, $"{s_OrigBackground}|{s_OrigEditorBehavior}");
#endif
        }

        /// <summary>Drops the saved originals of a session that ended without <see cref="EndSession"/>.</summary>
        public static void ForgetOriginals()
        {
#if UNITY_EDITOR
            UnityEditor.SessionState.EraseString(OriginalsKey);
#endif
        }

        private static void RemoveDevice<T>(ref T device) where T : InputDevice
        {
            if (device != null && device.added) InputSystem.RemoveDevice(device);
            device = null;
        }

        /// <summary>Virtual devices added before a domain reload outlive it, but the references to them do not.</summary>
        private static void RemoveUnreferencedSessionDevices()
        {
            var leftovers = new List<InputDevice>();
            foreach (var device in InputSystem.devices)
                if (IsSessionDeviceName(device.name)) leftovers.Add(device);
            foreach (var device in leftovers) InputSystem.RemoveDevice(device);
        }

        private static bool IsSessionDeviceName(string name)
        {
            foreach (var prefix in new[] { "PlaytestKeyboard", "PlaytestMouse", "PlaytestGamepad" })
            {
                if (name == null || !name.StartsWith(prefix, StringComparison.Ordinal)) continue;
                for (int i = prefix.Length; i < name.Length; i++)
                    if (!char.IsDigit(name[i])) return false;
                return true;
            }
            return false;
        }

        private static Keyboard EnsureKeyboard()
        {
            if (s_Keyboard == null || !s_Keyboard.added)
            {
                s_Keyboard = InputSystem.AddDevice<Keyboard>("PlaytestKeyboard");
                s_Keyboard.MakeCurrent();
            }
            return s_Keyboard;
        }

        private static Mouse EnsureMouse()
        {
            if (s_Mouse == null || !s_Mouse.added)
            {
                s_Mouse = InputSystem.AddDevice<Mouse>("PlaytestMouse");
                s_Mouse.MakeCurrent();
            }
            return s_Mouse;
        }

        private static Gamepad EnsureGamepad()
        {
            if (s_Gamepad == null || !s_Gamepad.added)
            {
                s_Gamepad = InputSystem.AddDevice<Gamepad>("PlaytestGamepad");
                s_Gamepad.MakeCurrent();
            }
            return s_Gamepad;
        }

        private static InputControl<float> Resolve(string kind, string name, out string error)
        {
            error = null;
            InputControl control = null;
            switch (kind)
            {
                case "key":
                    if (!Enum.TryParse<Key>(name, true, out var key) || key == Key.None)
                    {
                        error = $"unknown key '{name}' (use Input System Key names, e.g. space, a, leftArrow)";
                        return null;
                    }
                    control = EnsureKeyboard()[key];
                    break;
                case "button":
                    switch (name.ToLowerInvariant())
                    {
                        case "mouse0": control = EnsureMouse().leftButton; break;
                        case "mouse1": control = EnsureMouse().rightButton; break;
                        case "mouse2": control = EnsureMouse().middleButton; break;
                        default: control = EnsureGamepad().TryGetChildControl(name); break;
                    }
                    break;
                case "axis":
                    control = name.StartsWith("mouse/", StringComparison.OrdinalIgnoreCase)
                        ? EnsureMouse().TryGetChildControl(name.Substring(6))
                        : EnsureGamepad().TryGetChildControl(name);
                    break;
            }
            if (control == null)
            {
                error = $"unknown {kind} '{name}'";
                return null;
            }
            if (control is InputControl<float> f) return f;
            error = $"{kind} '{name}' is a {control.valueType.Name} control; address a float component (e.g. leftStick/x)";
            return null;
        }

        private static void OnBeforeUpdate()
        {
            if (InputState.currentUpdateType != InputUpdateType.Dynamic) return;
            FramePrologue();
        }

        private static void Dispatch(List<Change> changes)
        {
            var byDevice = new Dictionary<InputDevice, List<(InputControl<float> control, float value)>>();
            foreach (var c in changes)
            {
                var control = Resolve(c.Kind, c.Name, out var error);
                if (control == null)
                {
                    Debug.LogError("[Playtest] input: " + error);
                    continue;
                }
                if (!byDevice.TryGetValue(control.device, out var list)) byDevice[control.device] = list = new List<(InputControl<float>, float)>();
                list.Add((control, c.Value));
            }
            // One state event per device, seeded from the device's current state so held controls stay held.
            foreach (var kv in byDevice)
            {
                using (StateEvent.From(kv.Key, out var eventPtr))
                {
                    foreach (var (control, value) in kv.Value) control.WriteValueIntoEvent(value, eventPtr);
                    InputSystem.QueueEvent(eventPtr);
                }
            }
        }
#else
        public static bool Supported => false;
        public static bool DrivesPrologue => false;
        public static UnityEngine.Object SettingsObject => null;
        public static JObject BeginSession() => new JObject { ["input_system"] = false };
        public static void ForgetOriginals() { }
        public static void EndSession()
        {
            s_Queue.Clear();
            s_LastPrologueFrame = -1;
        }
#endif

        public static int PendingCount
        {
            get
            {
                int n = 0;
                foreach (var kv in s_Queue) n += kv.Value.Count;
                return n;
            }
        }

        public static void ClearQueue() => s_Queue.Clear();

        /// <summary>
        /// Schedules one input entry ({key|button|axis, do: press|release|tap, value?}) for absolute frame
        /// <paramref name="frame"/>; tap = press at frame, release at frame + 1. Returns an error or null.
        /// </summary>
        public static string Schedule(int frame, JObject entry)
        {
            if (entry == null) return "input entry must be an object";
            string kind = entry["key"] != null ? "key" : entry["button"] != null ? "button" : entry["axis"] != null ? "axis" : null;
            if (kind == null) return "input entry needs one of key, button, axis";
            string name = entry[kind]?.ToString();
            if (string.IsNullOrEmpty(name)) return $"input entry has an empty {kind}";
            string op = entry["do"]?.ToString()?.ToLowerInvariant() ?? "tap";
            if (op != "press" && op != "release" && op != "tap") return $"input do must be press, release or tap (got '{op}')";
            float value = 1f;
            if (entry["value"] != null && entry["value"].Type != JTokenType.Null)
            {
                if (!float.TryParse(entry["value"].ToString(), NumberStyles.Float, CultureInfo.InvariantCulture, out value))
                    return $"input value '{entry["value"]}' is not a number";
            }
#if MCP_FOR_UNITY_INPUT_SYSTEM
            if (Resolve(kind, name, out var error) == null) return error;
#else
            return UnsupportedMessage;
#endif
#pragma warning disable CS0162 // unreachable without the Input System
            if (op == "press" || op == "tap") Enqueue(frame, new Change { Kind = kind, Name = name, Value = value });
            if (op == "release") Enqueue(frame, new Change { Kind = kind, Name = name, Value = 0f });
            if (op == "tap") Enqueue(frame + 1, new Change { Kind = kind, Name = name, Value = 0f });
            return null;
#pragma warning restore CS0162
        }

        private static void Enqueue(int frame, Change c)
        {
            if (!s_Queue.TryGetValue(frame, out var list)) s_Queue[frame] = list = new List<Change>();
            list.Add(c);
        }

        /// <summary>Per-frame hook: autopilot first, then this frame's queued input (stale frames are flushed too).</summary>
        internal static void FramePrologue()
        {
            int frame = Time.frameCount;
            if (frame == s_LastPrologueFrame) return;
            s_LastPrologueFrame = frame;

            var auto = PlaytestDriver.RunAutopilot();
            if (auto != null)
            {
                foreach (var item in auto)
                {
                    var err = Schedule(frame, item as JObject);
                    if (err != null && !(err == UnsupportedMessage && s_WarnedUnsupportedAutopilotInput))
                    {
                        if (err == UnsupportedMessage) s_WarnedUnsupportedAutopilotInput = true;
                        Debug.LogError("[Playtest] autopilot input: " + err);
                    }
                }
            }

            if (s_Queue.Count == 0) return;
            var due = new List<Change>();
            var done = new List<int>();
            foreach (var kv in s_Queue)
            {
                if (kv.Key > frame) break;
                due.AddRange(kv.Value);
                done.Add(kv.Key);
            }
            foreach (var f in done) s_Queue.Remove(f);
#if MCP_FOR_UNITY_INPUT_SYSTEM
            if (due.Count > 0) Dispatch(due);
#endif
        }
    }
}
