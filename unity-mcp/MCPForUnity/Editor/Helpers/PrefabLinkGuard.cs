using System;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>
    /// Detects edits that break or restructure a prefab link and reports them as response
    /// warnings through <see cref="McpActionJournal.Warn"/>. Warns only, never refuses.
    /// </summary>
    internal static class PrefabLinkGuard
    {
        internal static string WarnDelete(GameObject go) => Emit(DescribeDelete(go));
        internal static string WarnComponentRemove(Component component) => Emit(DescribeComponentRemove(component));
        internal static string WarnReparent(GameObject go) => Emit(DescribeReparent(go));
        internal static string WarnUnpack(GameObject go) => Emit(DescribeUnpack(go));
        internal static string WarnAssetDelete(string assetPath) => Emit(DescribeAssetDelete(assetPath));

        static string Emit(string warning)
        {
            if (warning != null) McpActionJournal.Warn(warning);
            return warning;
        }

        internal static string DescribeDelete(GameObject go)
        {
            var root = InstanceRootOfPrefabOwnedObject(go);
            if (root == null || root == go) return null;
            return $"prefab_link: deleting '{go.name}' removes an object that comes from prefab instance '{root.name}' ({AssetPathOf(root)}); the instance will carry a removed-GameObject override.";
        }

        internal static string DescribeComponentRemove(Component component)
        {
            if (component == null || !PrefabUtility.IsPartOfNonAssetPrefabInstance(component)
                || PrefabUtility.IsAddedComponentOverride(component))
            {
                return null;
            }
            var root = PrefabUtility.GetOutermostPrefabInstanceRoot(component.gameObject);
            if (root == null) return null;
            return $"prefab_link: removing {component.GetType().Name} from '{component.gameObject.name}' in prefab instance '{root.name}' ({AssetPathOf(root)}) records a removed-component override.";
        }

        internal static string DescribeReparent(GameObject go)
        {
            var root = InstanceRootOfPrefabOwnedObject(go);
            if (root == null || root == go) return null;
            return $"prefab_link: reparenting '{go.name}' restructures prefab instance '{root.name}' ({AssetPathOf(root)}); Unity may refuse it, and the instance will no longer match its prefab.";
        }

        internal static string DescribeUnpack(GameObject go)
        {
            if (go == null || !PrefabUtility.IsPartOfNonAssetPrefabInstance(go)) return null;
            var root = PrefabUtility.GetOutermostPrefabInstanceRoot(go);
            if (root == null) return null;
            return $"prefab_link: unpacking prefab instance '{root.name}' ({AssetPathOf(root)}) breaks its link; later changes to the prefab asset will not reach it.";
        }

        /// <summary>Cheap checks only: prefab asset type and the open scenes' dependency lists.</summary>
        internal static string DescribeAssetDelete(string assetPath)
        {
            if (string.IsNullOrEmpty(assetPath)) return null;
            if (assetPath.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase))
            {
                return $"prefab_link: '{assetPath}' is a prefab asset; deleting it turns every instance of it into a missing prefab.";
            }
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (!scene.isLoaded || string.IsNullOrEmpty(scene.path) || scene.path == assetPath) continue;
                // Saved scene content only: unsaved references are not in the dependency list.
                if (Array.IndexOf(AssetDatabase.GetDependencies(scene.path, true), assetPath) >= 0)
                {
                    return $"asset_reference: '{assetPath}' is referenced by open scene '{scene.path}'; deleting it leaves missing references there.";
                }
            }
            return null;
        }

        /// <summary>Outermost instance root when <paramref name="go"/> belongs to a prefab (not an added override).</summary>
        static GameObject InstanceRootOfPrefabOwnedObject(GameObject go)
        {
            if (go == null || !PrefabUtility.IsPartOfNonAssetPrefabInstance(go)
                || PrefabUtility.IsAddedGameObjectOverride(go))
            {
                return null;
            }
            return PrefabUtility.GetOutermostPrefabInstanceRoot(go);
        }

        static string AssetPathOf(GameObject instanceRoot)
        {
            var source = PrefabUtility.GetCorrespondingObjectFromSource(instanceRoot);
            string path = source != null ? AssetDatabase.GetAssetPath(source) : null;
            return string.IsNullOrEmpty(path) ? "unknown prefab asset" : path;
        }
    }
}
