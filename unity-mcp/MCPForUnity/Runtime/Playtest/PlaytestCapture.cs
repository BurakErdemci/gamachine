using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using MCPForUnity.Runtime.Helpers;
using UnityEngine;
using UnityEngine.Rendering;

namespace MCPForUnity.Runtime.Playtest
{
    /// <summary>
    /// Renders a game camera into a RenderTexture while the game is paused, so the image is the frame just stepped
    /// (P2: 3.5-7 ms, frame-number strip decoded 12/12). Full-resolution PNG on disk, a downscaled JPEG/PNG inline.
    /// </summary>
    public static class PlaytestCapture
    {
        private static readonly Type s_GraphicType = Type.GetType("UnityEngine.UI.Graphic, UnityEngine.UI");

        public sealed class Result
        {
            public int Frame;
            public string Path;
            public int Width;
            public int Height;
            public int ImageWidth;
            public int ImageHeight;
            public string ImageBase64;
            public string Mime;
            public string Camera;
            public double RenderMs;
            public int HudCanvases;
            public string HudNote;
        }

        private sealed class CanvasState
        {
            public Canvas Canvas;
            public RenderMode RenderMode;
            public Camera WorldCamera;
            public float PlaneDistance;
            public int SortingLayerID;
            public int SortingOrder;
        }

        public static Camera FindCamera(string name, out string error)
        {
            error = null;
            if (!string.IsNullOrEmpty(name))
            {
                foreach (var cam in Camera.allCameras)
                    if (cam.name == name) return cam;
                error = $"no enabled camera named '{name}'";
                return null;
            }
            if (Camera.main != null) return Camera.main;
            if (Camera.allCamerasCount > 0) return Camera.allCameras[0];
            error = "no enabled camera in the scene";
            return null;
        }

        /// <summary>
        /// Captures <paramref name="cam"/>. <paramref name="inlineMaxSize"/> &lt;= 0 skips the inline image (path only).
        /// </summary>
        public static Result Capture(Camera cam, string directory, string label, int inlineMaxSize, bool jpeg)
        {
            if (cam == null) throw new ArgumentNullException(nameof(cam));
            int w = Mathf.Max(1, cam.pixelWidth > 0 ? cam.pixelWidth : Screen.width);
            int h = Mathf.Max(1, cam.pixelHeight > 0 ? cam.pixelHeight : Screen.height);
            var sw = Stopwatch.StartNew();
            var rt = RenderTexture.GetTemporary(w, h, 24, RenderTextureFormat.ARGB32);
            var prevTarget = cam.targetTexture;
            var prevActive = RenderTexture.active;
            int prevCullingMask = cam.cullingMask;
            var canvasStates = new List<CanvasState>();
            Texture2D tex = null, small = null;
            try
            {
                // Overlay UI is composited after cameras and never reaches their target textures, so retarget it while paused.
                var canvases = UnityFindObjectsCompat.FindAll<Canvas>();
                var sortingLayers = SortingLayer.layers;
                int hudSortingLayerID = sortingLayers[sortingLayers.Length - 1].id;
                foreach (var canvas in canvases)
                {
                    if (!canvas.isRootCanvas || !canvas.isActiveAndEnabled) continue;
                    if (canvas.renderMode != RenderMode.ScreenSpaceOverlay
                        && (canvas.renderMode != RenderMode.ScreenSpaceCamera || canvas.worldCamera == cam)) continue;
                    canvasStates.Add(new CanvasState
                    {
                        Canvas = canvas,
                        RenderMode = canvas.renderMode,
                        WorldCamera = canvas.worldCamera,
                        PlaneDistance = canvas.planeDistance,
                        SortingLayerID = canvas.sortingLayerID,
                        SortingOrder = canvas.sortingOrder,
                    });
                    canvas.renderMode = RenderMode.ScreenSpaceCamera;
                    canvas.worldCamera = cam;
                    canvas.planeDistance = cam.nearClipPlane + 0.05f;
                    canvas.sortingLayerID = hudSortingLayerID;
                    cam.cullingMask |= 1 << canvas.gameObject.layer;
                }
                var orderedStates = canvasStates.OrderBy(state => state.SortingOrder).ToList();
                for (int i = 0; i < orderedStates.Count; i++)
                    orderedStates[i].Canvas.sortingOrder = Math.Max(-32768, 32767 - (orderedStates.Count - 1 - i));
                foreach (var state in canvasStates) RebuildGraphics(state.Canvas);
                Canvas.ForceUpdateCanvases();

                string hudNote = null;
                var uiDocumentType = Type.GetType("UnityEngine.UIElements.UIDocument, UnityEngine.UIElementsModule");
                if (uiDocumentType != null)
                {
                    var documents = UnityFindObjectsCompat.FindAll(uiDocumentType);
                    foreach (var document in documents)
                    {
                        if (document is Behaviour behaviour && behaviour.enabled && behaviour.isActiveAndEnabled)
                        {
                            hudNote = "UI Toolkit panels are not included in captures.";
                            break;
                        }
                    }
                }
                Render(cam, rt);
                RenderTexture.active = rt;
                tex = new Texture2D(w, h, TextureFormat.RGBA32, false);
                tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
                tex.Apply();
                double renderMs = sw.Elapsed.TotalMilliseconds;

                int frame = Time.frameCount;
                Directory.CreateDirectory(directory);
                string safe = string.IsNullOrEmpty(label) ? "capture" : string.Join("_", label.Split(System.IO.Path.GetInvalidFileNameChars()));
                string path = System.IO.Path.GetFullPath(System.IO.Path.Combine(directory, $"{safe}_f{frame}.png"));
                File.WriteAllBytes(path, tex.EncodeToPNG());

                var result = new Result { Frame = frame, Path = path, Width = w, Height = h, Camera = cam.name, RenderMs = renderMs,
                    HudCanvases = canvasStates.Count, HudNote = hudNote };
                if (inlineMaxSize > 0)
                {
                    var source = tex;
                    if (w > inlineMaxSize || h > inlineMaxSize)
                    {
                        small = ScreenshotUtility.DownscaleTexture(tex, inlineMaxSize);
                        source = small;
                    }
                    result.ImageBase64 = Convert.ToBase64String(jpeg ? source.EncodeToJPG(85) : source.EncodeToPNG());
                    result.Mime = jpeg ? "image/jpeg" : "image/png";
                    result.ImageWidth = source.width;
                    result.ImageHeight = source.height;
                }
                return result;
            }
            finally
            {
                for (int i = canvasStates.Count - 1; i >= 0; i--)
                {
                    var state = canvasStates[i];
                    if (state.Canvas == null) continue;
                    try
                    {
                        state.Canvas.renderMode = state.RenderMode;
                        state.Canvas.worldCamera = state.WorldCamera;
                        state.Canvas.planeDistance = state.PlaneDistance;
                        state.Canvas.sortingLayerID = state.SortingLayerID;
                        state.Canvas.sortingOrder = state.SortingOrder;
                    }
                    catch (Exception ex)
                    {
                        UnityEngine.Debug.LogWarning($"[PlaytestCapture] Could not restore canvas '{state.Canvas.name}': {ex.Message}");
                    }
                    try { RebuildGraphics(state.Canvas); }
                    catch (Exception ex) { UnityEngine.Debug.LogWarning($"[PlaytestCapture] Could not rebuild restored canvas '{state.Canvas.name}': {ex.Message}"); }
                }
                // Each step runs even if an earlier one threw.
                try { cam.cullingMask = prevCullingMask; }
                catch (Exception ex) { UnityEngine.Debug.LogWarning($"[PlaytestCapture] Could not restore cullingMask: {ex.Message}"); }
                try { cam.targetTexture = prevTarget; }
                catch (Exception ex) { UnityEngine.Debug.LogWarning($"[PlaytestCapture] Could not restore targetTexture: {ex.Message}"); }
                try { RenderTexture.active = prevActive; }
                catch (Exception ex) { UnityEngine.Debug.LogWarning($"[PlaytestCapture] Could not restore RenderTexture.active: {ex.Message}"); }
                try { Canvas.ForceUpdateCanvases(); }
                catch (Exception ex) { UnityEngine.Debug.LogWarning($"[PlaytestCapture] ForceUpdateCanvases failed: {ex.Message}"); }
                try { RenderTexture.ReleaseTemporary(rt); }
                catch (Exception ex) { UnityEngine.Debug.LogWarning($"[PlaytestCapture] Could not release the temporary RenderTexture: {ex.Message}"); }
                try { if (tex != null) UnityEngine.Object.DestroyImmediate(tex); }
                catch (Exception ex) { UnityEngine.Debug.LogWarning($"[PlaytestCapture] Could not destroy the capture texture: {ex.Message}"); }
                try { if (small != null) UnityEngine.Object.DestroyImmediate(small); }
                catch (Exception ex) { UnityEngine.Debug.LogWarning($"[PlaytestCapture] Could not destroy the downscaled texture: {ex.Message}"); }
            }
        }

        /// <summary>Composes at most twelve captures in caller order, with four 320-pixel columns.</summary>
        public static Result ComposeContactSheet(IReadOnlyList<string> paths, string path)
        {
            var sources = new List<Texture2D>();
            Texture2D sheet = null;
            try
            {
                int cellHeight = 1;
                for (int i = 0; i < Math.Min(12, paths.Count); i++)
                {
                    var source = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                    sources.Add(source);
                    if (!source.LoadImage(File.ReadAllBytes(paths[i])))
                        throw new IOException($"Could not load capture '{paths[i]}'.");
                    cellHeight = Math.Max(cellHeight, Math.Max(1, Mathf.RoundToInt(320f * source.height / source.width)));
                }
                if (sources.Count == 0) return null;
                int rows = (sources.Count + 3) / 4;
                sheet = new Texture2D(1280, rows * cellHeight, TextureFormat.RGB24, false);
                var background = new Color32[sheet.width * sheet.height];
                for (int i = 0; i < background.Length; i++) background[i] = new Color32(24, 24, 28, 255);
                sheet.SetPixels32(background);
                for (int i = 0; i < sources.Count; i++)
                {
                    var source = sources[i];
                    int height = Math.Max(1, Mathf.RoundToInt(320f * source.height / source.width));
                    int x0 = (i % 4) * 320;
                    int y0 = (rows - 1 - i / 4) * cellHeight + (cellHeight - height) / 2;
                    for (int y = 0; y < height; y++)
                        for (int x = 0; x < 320; x++)
                            sheet.SetPixel(x0 + x, y0 + y, source.GetPixelBilinear((x + 0.5f) / 320, (y + 0.5f) / height));
                }
                sheet.Apply();
                Directory.CreateDirectory(System.IO.Path.GetDirectoryName(path));
                File.WriteAllBytes(path, sheet.EncodeToJPG(85));
                return InlineImage(sheet, path, 1280);
            }
            finally
            {
                if (sheet != null) UnityEngine.Object.DestroyImmediate(sheet);
                foreach (var source in sources) UnityEngine.Object.DestroyImmediate(source);
            }
        }

        public static Result LoadInlineImage(string path, int maxSize)
        {
            var texture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            try
            {
                if (!texture.LoadImage(File.ReadAllBytes(path))) throw new IOException($"Could not load capture '{path}'.");
                return InlineImage(texture, path, maxSize);
            }
            finally { UnityEngine.Object.DestroyImmediate(texture); }
        }

        private static Result InlineImage(Texture2D texture, string path, int maxSize)
        {
            Texture2D small = null;
            try
            {
                var source = texture;
                if (texture.width > maxSize || texture.height > maxSize)
                    source = small = ScreenshotUtility.DownscaleTexture(texture, maxSize);
                return new Result { Path = path, Width = texture.width, Height = texture.height,
                    ImageWidth = source.width, ImageHeight = source.height,
                    ImageBase64 = Convert.ToBase64String(source.EncodeToJPG(85)), Mime = "image/jpeg" };
            }
            finally { if (small != null) UnityEngine.Object.DestroyImmediate(small); }
        }

        private static void RebuildGraphics(Canvas canvas)
        {
            if (s_GraphicType == null) return;
            var setAllDirty = s_GraphicType.GetMethod("SetAllDirty",
                System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance,
                null, Type.EmptyTypes, null);
            if (setAllDirty == null) return;
            foreach (var graphic in canvas.GetComponentsInChildren(s_GraphicType, true))
            {
                try { setAllDirty.Invoke(graphic, null); }
                catch (Exception ex) { UnityEngine.Debug.LogWarning($"[PlaytestCapture] Could not rebuild graphic: {ex.Message}"); }
            }
        }

        private static void Render(Camera cam, RenderTexture rt)
        {
#if UNITY_2023_1_OR_NEWER
            // Scriptable pipelines (URP/HDRP) render off-screen through a render request; Camera.Render is the
            // built-in path and the fallback.
            var request = new RenderPipeline.StandardRequest { destination = rt };
            if (RenderPipeline.SupportsRenderRequest(cam, request))
            {
                RenderPipeline.SubmitRenderRequest(cam, request);
                return;
            }
#endif
            cam.targetTexture = rt;
            cam.Render();
        }
    }
}
