using System.IO;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using MCPForUnity.Editor.Tools;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageTextureWriteGuardTests
    {
        private const string TempRoot = "Assets/Temp/ManageTextureWriteGuardTests";

        [SetUp]
        public void SetUp()
        {
            if (!AssetDatabase.IsValidFolder("Assets/Temp"))
                AssetDatabase.CreateFolder("Assets", "Temp");
            if (!AssetDatabase.IsValidFolder(TempRoot))
                AssetDatabase.CreateFolder("Assets/Temp", "ManageTextureWriteGuardTests");
        }

        [TearDown]
        public void TearDown()
        {
            if (AssetDatabase.IsValidFolder(TempRoot))
                AssetDatabase.DeleteAsset(TempRoot);
            // Refused writes leave unimported fixtures behind; remove whatever the database missed.
            string abs = Abs(TempRoot);
            if (Directory.Exists(abs))
                Directory.Delete(abs, true);
            if (File.Exists(abs + ".meta"))
                File.Delete(abs + ".meta");

            if (AssetDatabase.IsValidFolder("Assets/Temp")
                && Directory.GetFileSystemEntries(Abs("Assets/Temp")).Length == 0)
                AssetDatabase.DeleteAsset("Assets/Temp");
            AssetDatabase.Refresh();
        }

        private static string Abs(string assetPath)
        {
            return Path.Combine(Directory.GetParent(Application.dataPath).FullName, assetPath);
        }

        private static JObject Run(JObject @params)
        {
            object result = ManageTexture.HandleCommand(@params);
            return result as JObject ?? JObject.FromObject(result);
        }

        private static JObject WriteParams(string action, string path)
        {
            var p = new JObject { ["action"] = action, ["path"] = path, ["width"] = 4, ["height"] = 4 };
            if (action == "apply_pattern")
                p["pattern"] = "checkerboard";
            return p;
        }

        private static void AssertRefused(JObject result, string because)
        {
            Assert.IsFalse(result["success"]?.Value<bool>() ?? true, $"{because}: {result}");
        }

        private static string CreatePngFixture(string name)
        {
            string assetPath = $"{TempRoot}/{name}.png";
            var tex = new Texture2D(4, 4, TextureFormat.RGBA32, false);
            File.WriteAllBytes(Abs(assetPath), tex.EncodeToPNG());
            Object.DestroyImmediate(tex);
            AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceSynchronousImport);
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<Texture2D>(assetPath), "fixture did not import");
            return assetPath;
        }

        [Test]
        public void Create_WithPng_Writes()
        {
            string path = $"{TempRoot}/ok.png";
            var result = Run(WriteParams("create", path));

            Assert.IsTrue(result["success"]?.Value<bool>() ?? false, result.ToString());
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<Texture2D>(path));
        }

        [Test]
        public void Create_OverExistingTexture_StillOverwrites()
        {
            string path = CreatePngFixture("existing");
            var p = WriteParams("create", path);
            p["width"] = 8;
            p["height"] = 8;

            var result = Run(p);

            Assert.IsTrue(result["success"]?.Value<bool>() ?? false, result.ToString());
            Assert.AreEqual(8, AssetDatabase.LoadAssetAtPath<Texture2D>(path).width);
        }

        [Test]
        public void Modify_SetPixelsOnPng_Writes()
        {
            string path = CreatePngFixture("modifiable");
            var result = Run(new JObject
            {
                ["action"] = "modify",
                ["path"] = path,
                ["setPixels"] = new JObject { ["x"] = 0, ["y"] = 0, ["width"] = 1, ["height"] = 1, ["color"] = new JArray(255, 0, 0, 255) }
            });

            Assert.IsTrue(result["success"]?.Value<bool>() ?? false, result.ToString());
        }

        [Test]
        public void WriteActions_RefuseNonImageExtension(
            [Values("create", "create_sprite", "apply_pattern", "apply_gradient", "apply_noise")] string action,
            [Values(".prefab", ".meta", ".asset")] string extension)
        {
            string path = $"{TempRoot}/target{extension}";

            var result = Run(WriteParams(action, path));

            AssertRefused(result, $"{action} to {extension}");
            StringAssert.Contains(".png", result["error"]?.ToString());
            Assert.IsFalse(File.Exists(Abs(path)), "nothing may be written");
        }

        [Test]
        public void Modify_RefusesTextureStoredInNonImageFile()
        {
            // A Texture2D serialized as a YAML .asset loads as a texture, so modify reaches the raw write.
            string path = $"{TempRoot}/serialized.asset";
            AssetDatabase.CreateAsset(new Texture2D(4, 4, TextureFormat.RGBA32, false), path);
            byte[] before = File.ReadAllBytes(Abs(path));

            var result = Run(new JObject
            {
                ["action"] = "modify",
                ["path"] = path,
                ["setPixels"] = new JObject { ["x"] = 0, ["y"] = 0, ["width"] = 1, ["height"] = 1, ["color"] = new JArray(255, 0, 0, 255) }
            });

            AssertRefused(result, "modify into a .asset");
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Abs(path)));
        }

        [Test]
        public void Create_RefusesExistingFileThatIsNotATextureAsset()
        {
            // On disk but never imported: not a texture asset at this path.
            string path = $"{TempRoot}/stray.png";
            byte[] before = System.Text.Encoding.ASCII.GetBytes("not an image");
            File.WriteAllBytes(Abs(path), before);

            var result = Run(WriteParams("create", path));

            AssertRefused(result, "create over a non-texture file");
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Abs(path)));
        }

        [Test]
        public void Create_RefusesShortNameAliasOfPrefab()
        {
            string prefabPath = $"{TempRoot}/QzPrefabFixture.prefab";
            var go = new GameObject("QzPrefabFixture");
            PrefabUtility.SaveAsPrefabAsset(go, prefabPath);
            Object.DestroyImmediate(go);
            byte[] before = File.ReadAllBytes(Abs(prefabPath));

            string alias = $"{TempRoot}/QZPREF~1.PRE";
            RequireShortName(alias);

            var result = Run(WriteParams("create", alias));

            AssertRefused(result, "create through an 8.3 alias of a prefab");
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Abs(prefabPath)));
        }

        [Test]
        public void Create_RefusesShortNameAliasWithImageExtension()
        {
            // "QzAliasFixture.pngx" gets the short name "QZALIA~1.PNG": the requested extension
            // passes, only the name lookup on disk shows the target is not what was named.
            string realPath = $"{TempRoot}/QzAliasFixture.pngx";
            byte[] before = System.Text.Encoding.ASCII.GetBytes("keep me");
            File.WriteAllBytes(Abs(realPath), before);
            AssetDatabase.ImportAsset(realPath, ImportAssetOptions.ForceSynchronousImport);

            string alias = $"{TempRoot}/QZALIA~1.PNG";
            RequireShortName(alias);

            var result = Run(WriteParams("create", alias));

            AssertRefused(result, "create through an 8.3 alias ending in .PNG");
            StringAssert.Contains("8.3", result["error"]?.ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Abs(realPath)));
        }

        private static void RequireShortName(string aliasAssetPath)
        {
            if (!File.Exists(Abs(aliasAssetPath)))
                Assert.Ignore($"8.3 short names are not generated on this volume ('{aliasAssetPath}' does not resolve); alias test skipped.");
        }
    }
}
