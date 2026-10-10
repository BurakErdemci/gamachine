#if UNITY_EDITOR_WIN
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
#endif

namespace MCPForUnity.Editor.Helpers
{
    // Measured: native modal dialogs stop play-mode frames while editor update keeps ticking;
    // these dialogs are Win32 windows, not EditorWindows.
    internal static class UnityModalDialogProbe
    {
        public static string FindOpenDialogTitle()
        {
#if UNITY_EDITOR_WIN
            try
            {
                if (ProcessId == 0) return null;
                var state = s_State ?? (s_State = new ProbeState());
                state.Title = null;
                state.Failed = false;
                EnumWindows(state.Callback, IntPtr.Zero);
                return state.Failed ? null : state.Title;
            }
            catch
            {
                return null;
            }
#else
            return null;
#endif
        }

#if UNITY_EDITOR_WIN
        private static readonly int ProcessId = ReadProcessId();
        [ThreadStatic] private static ProbeState s_State;

        private static int ReadProcessId()
        {
            try
            {
                using var process = Process.GetCurrentProcess();
                return process.Id;
            }
            catch
            {
                return 0;
            }
        }

        // Reuse buffers and the callback per thread without locking the editor against a worker.
        private sealed class ProbeState
        {
            private readonly StringBuilder _className = new StringBuilder(256);
            private readonly StringBuilder _windowText = new StringBuilder(256);
            public readonly EnumWindowsProc Callback;
            public string Title;
            public bool Failed;

            public ProbeState() => Callback = VisitWindow;

            private bool VisitWindow(IntPtr window, IntPtr parameter)
            {
                try
                {
                    if (!IsWindowVisible(window)) return true;
                    GetWindowThreadProcessId(window, out uint ownerProcessId);
                    if (ownerProcessId != (uint)ProcessId) return true;
                    _className.Clear();
                    if (GetClassName(window, _className, _className.Capacity) == 0 || _className.ToString() != "#32770") return true;
                    _windowText.Clear();
                    _windowText.EnsureCapacity(GetWindowTextLength(window) + 1);
                    GetWindowText(window, _windowText, _windowText.Capacity);
                    Title = _windowText.ToString();
                    return false;
                }
                catch
                {
                    Failed = true;
                    return false;
                }
            }
        }

        [return: MarshalAs(UnmanagedType.Bool)]
        private delegate bool EnumWindowsProc(IntPtr window, IntPtr parameter);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool IsWindowVisible(IntPtr window);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetClassName(IntPtr window, StringBuilder className, int maxCount);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowText(IntPtr window, StringBuilder text, int maxCount);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowTextLength(IntPtr window);
#endif
    }
}
