using System;
using MCPForUnity.Editor;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Helpers
{
    public class McpPrefsTests
    {
        private string _key;

        [SetUp]
        public void SetUp()
        {
            _key = "MCPForUnityTests.McpPrefs." + Guid.NewGuid().ToString("N");
            McpPrefs.LaneOverrideForTests = null;
            McpPrefs.ResetLaneStoreForTests();
        }

        [TearDown]
        public void TearDown()
        {
            McpPrefs.LaneOverrideForTests = null;
            McpPrefs.ResetLaneStoreForTests();
            EditorPrefs.DeleteKey(_key);
        }

        [Test]
        public void NormalEditor_WritesGoToEditorPrefs()
        {
            McpPrefs.LaneOverrideForTests = false;

            McpPrefs.SetString(_key, "user");

            Assert.AreEqual("user", EditorPrefs.GetString(_key, ""));
            Assert.AreEqual(string.Empty, SessionState.GetString(McpPrefs.SessionKey, string.Empty));

            McpPrefs.DeleteKey(_key);
            Assert.IsFalse(EditorPrefs.HasKey(_key));
        }

        [Test]
        public void Lane_WritesNeverReachEditorPrefs()
        {
            McpPrefs.LaneOverrideForTests = true;

            McpPrefs.SetString(_key, "lane");
            McpPrefs.SetBool(_key + ".b", true);
            McpPrefs.SetInt(_key + ".i", 42);

            Assert.IsFalse(EditorPrefs.HasKey(_key));
            Assert.IsFalse(EditorPrefs.HasKey(_key + ".b"));
            Assert.IsFalse(EditorPrefs.HasKey(_key + ".i"));
            Assert.AreEqual("lane", McpPrefs.GetString(_key));
            Assert.IsTrue(McpPrefs.GetBool(_key + ".b"));
            Assert.AreEqual(42, McpPrefs.GetInt(_key + ".i"));
            Assert.IsTrue(McpPrefs.HasKey(_key));
        }

        [Test]
        public void Lane_ReadsFallBackToUserPrefs_AndOverrideWins()
        {
            EditorPrefs.SetString(_key, "user");
            McpPrefs.LaneOverrideForTests = true;

            Assert.AreEqual("user", McpPrefs.GetString(_key));

            McpPrefs.SetString(_key, "lane");

            Assert.AreEqual("lane", McpPrefs.GetString(_key));
            Assert.AreEqual("user", EditorPrefs.GetString(_key, ""));
        }

        [Test]
        public void Lane_DeleteHidesUserValue_WithoutDeletingIt()
        {
            EditorPrefs.SetBool(_key, true);
            McpPrefs.LaneOverrideForTests = true;

            McpPrefs.DeleteKey(_key);

            Assert.IsFalse(McpPrefs.HasKey(_key));
            Assert.IsFalse(McpPrefs.GetBool(_key, false));
            Assert.IsTrue(EditorPrefs.HasKey(_key));
            Assert.IsTrue(EditorPrefs.GetBool(_key, false));
        }

        [Test]
        public void Lane_StoreSurvivesDomainReload()
        {
            McpPrefs.LaneOverrideForTests = true;
            McpPrefs.SetBool(_key, true);
            McpPrefs.SetString(_key + ".s", "http://127.0.0.1:1234");
            McpPrefs.DeleteKey(_key + ".gone");

            McpPrefs.SimulateDomainReloadForTests();

            Assert.IsTrue(McpPrefs.GetBool(_key, false));
            Assert.AreEqual("http://127.0.0.1:1234", McpPrefs.GetString(_key + ".s"));
            Assert.IsFalse(McpPrefs.HasKey(_key + ".gone"));
            Assert.IsFalse(EditorPrefs.HasKey(_key));
        }

        [Test]
        public void Lane_TypeMismatchReturnsDefault()
        {
            McpPrefs.LaneOverrideForTests = true;
            McpPrefs.SetString(_key, "text");

            Assert.AreEqual(7, McpPrefs.GetInt(_key, 7));
            Assert.IsTrue(McpPrefs.GetBool(_key, true));
        }

        [Test]
        public void LeavingLaneMode_IgnoresLaneStore()
        {
            EditorPrefs.SetString(_key, "user");
            McpPrefs.LaneOverrideForTests = true;
            McpPrefs.SetString(_key, "lane");

            McpPrefs.LaneOverrideForTests = false;

            Assert.AreEqual("user", McpPrefs.GetString(_key));
        }
    }
}
