using System;
using System.Diagnostics;
using System.IO;
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
            Texture2D tex = null, small = null;
            try
            {
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

                var result = new Result { Frame = frame, Path = path, Width = w, Height = h, Camera = cam.name, RenderMs = renderMs };
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
                cam.targetTexture = prevTarget;
                RenderTexture.active = prevActive;
                RenderTexture.ReleaseTemporary(rt);
                if (tex != null) UnityEngine.Object.DestroyImmediate(tex);
                if (small != null) UnityEngine.Object.DestroyImmediate(small);
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
