using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// Per-action Undo groups, the action log and undo_action, exercised through
    /// CommandRegistry.ExecuteCommand: the same entry point the transport dispatcher uses.
    /// </summary>
    public class McpActionJournalTests
    {
        readonly List<string> _names = new List<string>();

        [SetUp]
        public void SetUp()
        {
            CommandRegistry.Initialize();
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var name in _names)
            {
                GameObject go;
                while ((go = GameObject.Find(name)) != null) UnityEngine.Object.DestroyImmediate(go);
            }
            _names.Clear();
        }

        string Unique(string prefix)
        {
            string name = $"{prefix}_{Guid.NewGuid():N}".Substring(0, prefix.Length + 9);
            _names.Add(name);
            return name;
        }

        static JObject RunSync(string tool, JObject p)
        {
            var result = CommandRegistry.ExecuteCommand(tool, p, new TaskCompletionSource<string>());
            Assert.IsNotNull(result, $"{tool} was expected to complete synchronously");
            return result as JObject ?? JObject.FromObject(result);
        }

        static IEnumerator RunAsync(string tool, JObject p, Action<JObject> done)
        {
            var tcs = new TaskCompletionSource<string>();
            var sync = CommandRegistry.ExecuteCommand(tool, p, tcs);
            if (sync != null)
            {
                done(sync as JObject ?? JObject.FromObject(sync));
                yield break;
            }
            double deadline = EditorApplication.timeSinceStartup + 60;
            while (!tcs.Task.IsCompleted && EditorApplication.timeSinceStartup < deadline) yield return null;
            Assert.IsTrue(tcs.Task.IsCompleted, $"{tool} did not complete");
            done(JObject.Parse(tcs.Task.Result)["result"] as JObject);
        }

        static JObject CreateParams(string name) => new JObject { ["action"] = "create", ["name"] = name };

        static string[] LogLines() =>
            File.Exists(McpActionJournal.LogPath) ? File.ReadAllLines(McpActionJournal.LogPath) : new string[0];

        const string TwoObjectsTool = "mcp_journal_test_two_objects";

        static Dictionary<string, HandlerInfo> Handlers() =>
            (Dictionary<string, HandlerInfo>)typeof(CommandRegistry)
                .GetField("_handlers", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);

        [Test]
        public void OneCommand_WithSeveralInternalGroups_IsUndoneByOneUndo()
        {
            string a = Unique("JournalA"), b = Unique("JournalB");
            // A handler that splits its work over two Undo groups, as ProBuilder does.
            Handlers()[TwoObjectsTool] = new HandlerInfo(TwoObjectsTool, _ =>
            {
                Undo.RegisterCreatedObjectUndo(new GameObject(a), "a");
                Undo.IncrementCurrentGroup();
                Undo.RegisterCreatedObjectUndo(new GameObject(b), "b");
                return new SuccessResponse("two objects");
            }, null);
            try
            {
                var result = RunSync(TwoObjectsTool, new JObject());

                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                Assert.IsNotNull(result["undo"], "tracked command must report its undo group");
                Assert.AreEqual(result["undo"].Value<string>("name"), Undo.GetCurrentGroupName());
                Assert.IsNotNull(GameObject.Find(a));
                Assert.IsNotNull(GameObject.Find(b));

                Undo.PerformUndo();

                Assert.IsNull(GameObject.Find(a), "one undo must revert the whole agent action");
                Assert.IsNull(GameObject.Find(b), "one undo must revert the whole agent action");
            }
            finally
            {
                Handlers().Remove(TwoObjectsTool);
            }
        }

        [UnityTest]
        public IEnumerator BatchExecute_IsOneUndoGroup()
        {
            string a = Unique("BatchA"), b = Unique("BatchB");
            var p = new JObject
            {
                ["commands"] = new JArray
                {
                    new JObject { ["tool"] = "manage_gameobject", ["params"] = CreateParams(a) },
                    new JObject { ["tool"] = "manage_gameobject", ["params"] = CreateParams(b) },
                }
            };
            JObject result = null;
            yield return RunAsync("batch_execute", p, r => result = r);

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var undo = result["undo"];
            Assert.IsNotNull(undo);
            StringAssert.StartsWith("MCP: batch_execute #", undo.Value<string>("name"));
            Assert.AreEqual(true, undo.Value<bool>("undoable"));
            // Sub-results carry no undo of their own: nested calls join the batch group.
            foreach (var entry in result["data"]["results"]) Assert.IsNull(entry["result"]?["undo"], entry.ToString());

            Undo.PerformUndo();

            Assert.IsNull(GameObject.Find(a));
            Assert.IsNull(GameObject.Find(b));
        }

        [Test]
        public void UndoAction_RevertsTheLatestAction_AndRefusesASecondTime()
        {
            string name = Unique("UndoActionTarget");
            var created = RunSync("manage_gameobject", CreateParams(name));
            string id = created["undo"].Value<string>("action_id");
            Assert.IsNotNull(GameObject.Find(name));

            var first = RunSync("manage_editor", new JObject { ["action"] = "undo_action", ["action_id"] = id });
            Assert.IsTrue(first.Value<bool>("success"), first.ToString());
            Assert.IsNull(GameObject.Find(name));
            Assert.IsNull(first["undo"], "undo_action must not open an undo group of its own");

            var second = RunSync("manage_editor", new JObject { ["action"] = "undo_action", ["action_id"] = id });
            Assert.IsFalse(second.Value<bool>("success"), second.ToString());
        }

        [Test]
        public void UndoAction_RefusesWhenALaterEditExists()
        {
            string name = Unique("UndoActionOlder");
            string userName = Unique("UserEdit");
            var created = RunSync("manage_gameobject", CreateParams(name));
            string id = created["undo"].Value<string>("action_id");

            Undo.IncrementCurrentGroup();
            var user = new GameObject(userName);
            Undo.RegisterCreatedObjectUndo(user, "User edit");
            Undo.IncrementCurrentGroup();

            var result = RunSync("manage_editor", new JObject { ["action"] = "undo_action", ["action_id"] = id });

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("not the most recent undo step", result.Value<string>("error"));
            Assert.IsNotNull(GameObject.Find(name), "nothing may be undone on refusal");
            Assert.IsNotNull(GameObject.Find(userName), "the user's later edit must survive");
        }

        [Test]
        public void UndoAction_RefusesAnActionThatUndoCannotRevert()
        {
            string name = Unique("DeletedPlain");
            new GameObject(name);
            var deleted = RunSync("manage_gameobject", new JObject { ["action"] = "delete", ["target"] = name, ["searchMethod"] = "by_name" });
            Assert.AreEqual(false, deleted["undo"].Value<bool>("undoable"), deleted.ToString());

            var result = RunSync("manage_editor", new JObject { ["action"] = "undo_action", ["action_id"] = deleted["undo"].Value<string>("action_id") });

            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("cannot be undone", result.Value<string>("error"));
        }

        [Test]
        public void ActionLog_IsWrittenUnderLibrary_NotAssets()
        {
            string name = Unique("Logged");
            var created = RunSync("manage_gameobject", CreateParams(name));
            string id = created["undo"].Value<string>("action_id");

            string projectRoot = Directory.GetParent(Application.dataPath).FullName;
            string logPath = Path.GetFullPath(McpActionJournal.LogPath);
            StringAssert.StartsWith(Path.Combine(projectRoot, "Library"), logPath);
            Assert.IsFalse(logPath.StartsWith(Path.GetFullPath(Application.dataPath), StringComparison.OrdinalIgnoreCase));
            Assert.IsFalse(Directory.Exists(Path.Combine(Application.dataPath, "GamachineActions")));

            var line = LogLines().Select(JObject.Parse).LastOrDefault(l => l.Value<string>("action_id") == id);
            Assert.IsNotNull(line, "the action must be logged");
            Assert.AreEqual("manage_gameobject", line.Value<string>("tool"));
            Assert.AreEqual("create", line.Value<string>("action"));
            Assert.AreEqual(created["undo"].Value<int>("group"), line.Value<int>("undo_group"));
            Assert.IsTrue(line.Value<bool>("ok"));
        }

        [Test]
        public void ReadCommands_AreNotTrackedOrLogged()
        {
            int before = LogLines().Length;
            var result = RunSync("find_gameobjects", new JObject { ["searchTerm"] = "NoSuchObject_McpJournal" });
            Assert.IsNull(result["undo"], result.ToString());
            Assert.AreEqual(before, LogLines().Length);
        }
    }
}
