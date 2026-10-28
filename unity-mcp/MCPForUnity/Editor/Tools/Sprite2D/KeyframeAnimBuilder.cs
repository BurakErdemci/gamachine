using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using MCPForUnity.Editor.Helpers;

namespace MCPForUnity.Editor.Tools.Sprite2D
{
    internal static class KeyframeAnimBuilder
    {
        private static readonly string[] ValidProperties = { "position", "rotation", "scale", "alpha", "color" };
        private static readonly string[] ValidEasings = { "linear", "ease_in", "ease_out", "ease_in_out" };

        /// <summary>One validated keyframe. Scalar properties read x and ignore y/z.</summary>
        private struct ParsedKeyframe
        {
            public float time;
            public float x, y, z;
            public float inTangent, outTangent;
        }

        /// <summary>
        /// params:
        ///   target          - GameObject adı veya "path/in/hierarchy"
        ///   clip_name       - AnimationClip adı (dosya adı olarak da kullanılır)
        ///   output_dir      - .anim ve .controller kayıt dizini
        ///   property        - "position", "rotation", "scale", "alpha", "color"
        ///   keyframes       - [{time: float, value: [x,y,z] veya float, easing: "linear"|"ease_in"|"ease_out"|"ease_in_out"}]
        ///   loop            - bool (default false)
        ///   overwrite       - bool (default false); an existing clip is kept unless this is true
        ///   duration        - toplam süre (son keyframe'den alınır eğer belirtilmezse)
        /// </summary>
        public static object AddKeyframeAnim(JObject @params, SpriteDiagnosticBuilder diagnostics)
        {
            string targetName = @params["target"]?.ToString();
            if (string.IsNullOrEmpty(targetName))
                return diagnostics.Fail("BAD_PARAM", "'target' is required (GameObject name or hierarchy path).");

            // Read through the shared reader rather than sanitizing with a fallback to the raw
            // value: the fallback handed a traversal path straight to CreateFolders.
            string requestedDir = @params["output_dir"]?.ToString();
            if (string.IsNullOrWhiteSpace(requestedDir)) requestedDir = "Assets/Animations";
            if (!SpriteParams.TryReadAssetPath(new JObject { ["output_dir"] = requestedDir },
                                               "output_dir", out string outputDir, out string dirError))
                return diagnostics.Fail("BAD_PARAM", dirError);

            string clipName = @params["clip_name"]?.ToString();
            if (string.IsNullOrWhiteSpace(clipName)) clipName = "NewAnimation";
            if (!SpriteParams.TryReadClipName(clipName, outputDir, ".anim",
                                              out string clipPath, out string nameReason, out string[] nameFixes))
                return diagnostics.Fail("CLIP_BAD_NAME", $"Clip '{clipName}': {nameReason}.", nameFixes);

            string property = (@params["property"]?.ToString() ?? "position").ToLowerInvariant();
            if (Array.IndexOf(ValidProperties, property) < 0)
                return diagnostics.Fail("PROPERTY_NOT_FOUND",
                    $"Unknown property '{property}'. Valid: {string.Join(", ", ValidProperties)}.",
                    "Use one of: " + string.Join(", ", ValidProperties));

            if (!SpriteParams.TryReadBool(@params, "loop", false, out bool loop, out string loopError))
                return diagnostics.Fail("BAD_PARAM", loopError);
            // Same policy as the sibling actions: replacing an asset the caller did not ask to
            // replace is destruction, and destruction needs authorisation.
            if (!SpriteParams.TryReadBool(@params, "overwrite", false, out bool overwrite, out string overwriteError))
                return diagnostics.Fail("BAD_PARAM", overwriteError);

            if (!(@params["keyframes"] is JArray keyframesToken) || keyframesToken.Count == 0)
                return diagnostics.Fail("BAD_PARAM", "'keyframes' array is required.");

            // A scalar property has no room for a vector, and a vector one broadcasts a scalar.
            bool vectorValued = property != "alpha";
            if (!TryReadKeyframes(keyframesToken, vectorValued, out var keys, out string keyError))
                return diagnostics.Fail("BAD_PARAM", keyError,
                    "Each keyframe is an object: {time: number >= 0, value: number or [x,y,z], easing: " +
                    string.Join("|", ValidEasings) + "}.");

            var existingClip = AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath);
            if (existingClip != null && !overwrite)
                return diagnostics.Fail("CLIP_EXISTS",
                    $"Clip '{clipName}': an animation clip already exists at '{clipPath}'.",
                    "Set overwrite=true to replace it.", "Choose a different clip name or output_dir.");

            var go = ResolveTarget(targetName, diagnostics);
            if (go == null) return diagnostics.Fail();

            // The scene object names the controller, so its name is untrusted in the same way.
            if (!SpriteParams.TryReadClipName(go.name, outputDir, "_Controller.controller",
                                              out string controllerPath, out string ctrlReason, out string[] ctrlFixes))
                return diagnostics.Fail("BAD_PARAM",
                    $"The controller is named after '{go.name}', and {ctrlReason}.", ctrlFixes);

            // Every refusal above happens before this line: the folder is the first thing that
            // outlives a failed call, so nothing may be created until the request is fully read.
            if (!AssetDatabase.IsValidFolder(outputDir))
                SpriteClipBuilder.CreateFolders(outputDir);

            var clip = new AnimationClip { name = clipName };
            bool needsCanvasGroup = BuildCurves(clip, property, keys, go);

            var settings = AnimationUtility.GetAnimationClipSettings(clip);
            settings.loopTime = loop;
            AnimationUtility.SetAnimationClipSettings(clip, settings);

            // CreateAsset replaces an existing asset itself; deleting first left nothing at
            // the path when the replacement failed to be written. Reference equality, not a
            // null check: a failed replacement leaves the old asset loadable at the path.
            AssetDatabase.CreateAsset(clip, clipPath);
            if (AssetDatabase.LoadAssetAtPath<AnimationClip>(clipPath) != clip)
            {
                UnityEngine.Object.DestroyImmediate(clip);
                return diagnostics.Fail("CLIP_WRITE_FAILED", $"Unity did not write '{clipPath}'.",
                    "Check the Unity console for the AssetDatabase error.");
            }

            // The controller is reused rather than replaced when it already exists: each call
            // adds one more state to the target's controller, so overwriting it would throw
            // away the states earlier calls put there. `overwrite` deliberately covers only
            // the clip.
            var controller = AssetDatabase.LoadAssetAtPath<AnimatorController>(controllerPath);
            if (controller == null)
                controller = AnimatorController.CreateAnimatorControllerAtPath(controllerPath);
            if (controller == null || AssetDatabase.LoadAssetAtPath<AnimatorController>(controllerPath) != controller)
                return diagnostics.Fail("CONTROLLER_WRITE_FAILED", $"Unity did not write '{controllerPath}'.",
                    "Check the Unity console for the AssetDatabase error.");

            var sm = controller.layers[0].stateMachine;
            var state = sm.AddState(clipName);
            state.motion = clip;
            if (sm.defaultState == null) sm.defaultState = state;

            // Components come last, after every asset write, for the reason full_setup
            // adds its SpriteRenderer last: a component is the one side effect a refused
            // write cannot take back.
            if (needsCanvasGroup && go.GetComponent<CanvasGroup>() == null)
            {
                Undo.RecordObject(go, "Add CanvasGroup Component");
                Undo.AddComponent<CanvasGroup>(go);
            }

            // `??` compares references and never sees Unity's overloaded ==, so the explicit
            // null check is what keeps AddComponent from being skipped.
            var animator = go.GetComponent<Animator>();
            if (animator == null)
            {
                Undo.RecordObject(go, "Add Animator Component");
                animator = Undo.AddComponent<Animator>(go);
            }
            Undo.RecordObject(animator, "Assign AnimatorController");
            animator.runtimeAnimatorController = controller;
            EditorUtility.SetDirty(go);

            EditorUtility.SetDirty(controller);
            AssetDatabase.SaveAssets();

            return new
            {
                success         = true,
                target          = targetName,
                clip_path       = clipPath,
                controller_path = controllerPath,
                property,
                keyframe_count  = keys.Count,
                loop,
                diagnostics     = diagnostics.Build(),
            };
        }

        // ── Validation ───────────────────────────────────────────────────────

        /// <summary>
        /// Reads every keyframe before anything is written. A malformed entry used to reach
        /// ToObject&lt;float&gt; and leave the dispatcher to report a generic INTERNAL, after
        /// the output folder and a CanvasGroup had already been created.
        /// </summary>
        private static bool TryReadKeyframes(JArray keyframesToken, bool vectorValued,
                                             out List<ParsedKeyframe> parsed, out string error)
        {
            parsed = new List<ParsedKeyframe>(keyframesToken.Count);
            error = null;

            for (int i = 0; i < keyframesToken.Count; i++)
            {
                if (!(keyframesToken[i] is JObject kf))
                {
                    error = $"'keyframes[{i}]' must be an object with a 'time' and a 'value'.";
                    return false;
                }

                // Absent is not zero: an entry without a time used to land at t=0 in
                // silence, so a list of them collapsed into one frame and said nothing.
                JToken timeToken = kf["time"];
                if (timeToken == null || timeToken.Type == JTokenType.Null)
                {
                    error = $"'keyframes[{i}].time' is required.";
                    return false;
                }
                if (!SpriteParams.TryReadFiniteNumber(timeToken, $"'keyframes[{i}].time'", out float time, out error))
                    return false;
                if (time < 0f)
                {
                    error = $"'keyframes[{i}].time' must be 0 or more; got {time}.";
                    return false;
                }

                JToken valueToken = kf["value"];
                if (valueToken == null || valueToken.Type == JTokenType.Null)
                {
                    error = $"'keyframes[{i}].value' is required.";
                    return false;
                }

                float x, y, z;
                if (valueToken is JArray components)
                {
                    if (!vectorValued)
                    {
                        error = $"'keyframes[{i}].value' must be a single number for this property.";
                        return false;
                    }
                    if (components.Count != 3)
                    {
                        error = $"'keyframes[{i}].value' must have exactly 3 numbers; got {components.Count}.";
                        return false;
                    }
                    if (!SpriteParams.TryReadFiniteNumber(components[0], $"'keyframes[{i}].value[0]'", out x, out error) ||
                        !SpriteParams.TryReadFiniteNumber(components[1], $"'keyframes[{i}].value[1]'", out y, out error) ||
                        !SpriteParams.TryReadFiniteNumber(components[2], $"'keyframes[{i}].value[2]'", out z, out error))
                        return false;
                }
                else
                {
                    if (!SpriteParams.TryReadFiniteNumber(valueToken, $"'keyframes[{i}].value'", out x, out error))
                        return false;
                    y = z = x;
                }

                // An unrecognised easing used to fall through to linear without a word, so the
                // caller was told the clip eased when it did not.
                float inTangent = 0f, outTangent = 0f;
                JToken easingToken = kf["easing"];
                if (easingToken != null && easingToken.Type != JTokenType.Null)
                {
                    string easing = easingToken.Type == JTokenType.String ? easingToken.ToString() : null;
                    if (easing == null || Array.IndexOf(ValidEasings, easing) < 0)
                    {
                        error = $"'keyframes[{i}].easing' must be one of: {string.Join(", ", ValidEasings)}.";
                        return false;
                    }
                    if (easing == "ease_in") { inTangent = 0f; outTangent = float.PositiveInfinity; }
                    else if (easing == "ease_out") { inTangent = float.PositiveInfinity; outTangent = 0f; }
                    else if (easing == "ease_in_out") { inTangent = float.PositiveInfinity; outTangent = float.PositiveInfinity; }
                }

                parsed.Add(new ParsedKeyframe
                {
                    time = time, x = x, y = y, z = z,
                    inTangent = inTangent, outTangent = outTangent,
                });
            }

            return true;
        }

        /// <summary>
        /// Resolves the target through the shared lookup. The old walk over every GameObject
        /// took the first name match, so with two objects of one name it animated whichever
        /// the scene happened to yield first.
        /// </summary>
        private static GameObject ResolveTarget(string targetName, SpriteDiagnosticBuilder diagnostics)
        {
            // A name carrying a separator is a hierarchy path, which by_path resolves; a bare
            // name goes to by_name, whose duplicates are refused rather than guessed.
            string method = targetName.Contains("/") ? "by_path" : "by_name";
            var matches = GameObjectLookup.SearchGameObjects(method, targetName, includeInactive: true);

            if (matches.Count > 1)
            {
                diagnostics.AddError("SCENE_TARGET_AMBIGUOUS",
                    $"{matches.Count} GameObjects are named '{targetName}'; nothing was animated.",
                    "Rename the target or pick a unique name.");
                return null;
            }

            var go = matches.Count == 1 ? GameObjectLookup.FindById(matches[0]) : null;
            if (go == null)
            {
                diagnostics.AddError("SCENE_TARGET_NOT_FOUND", $"GameObject '{targetName}' not found in scene.",
                    "Check GameObject name or open the correct scene first.");
                return null;
            }
            return go;
        }

        // ── Curves ───────────────────────────────────────────────────────────

        /// <returns>true when the curves drive a CanvasGroup the target does not have yet.</returns>
        private static bool BuildCurves(AnimationClip clip, string property, List<ParsedKeyframe> keys, GameObject go)
        {
            switch (property)
            {
                case "position":
                    SetVector3Curves(clip, typeof(Transform),
                        "localPosition.x", "localPosition.y", "localPosition.z", keys);
                    break;

                case "rotation":
                    SetVector3Curves(clip, typeof(Transform),
                        "localEulerAnglesRaw.x", "localEulerAnglesRaw.y", "localEulerAnglesRaw.z", keys);
                    break;

                case "scale":
                    SetVector3Curves(clip, typeof(Transform),
                        "localScale.x", "localScale.y", "localScale.z", keys);
                    break;

                case "alpha":
                    // CanvasGroup varsa kullan; yoksa ekle (UI Image varsa Image.color.a tercih edilir)
                    var imageType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image");
                    if (imageType != null && go.GetComponent(imageType) != null && go.GetComponent<CanvasGroup>() == null)
                    {
                        SetFloatCurve(clip, imageType, "m_Color.a", keys);
                        break;
                    }
                    // The binding needs only the type; the component itself is added by the
                    // caller after the asset writes, so a refused write leaves no component.
                    SetFloatCurve(clip, typeof(CanvasGroup), "m_Alpha", keys);
                    return true;

                case "color":
                    SetVector3Curves(clip, DetectColorComponent(go),
                        "m_Color.r", "m_Color.g", "m_Color.b", keys);
                    break;
            }
            return false;
        }

        private static void SetVector3Curves(AnimationClip clip, Type componentType,
                                             string propX, string propY, string propZ,
                                             List<ParsedKeyframe> keys)
        {
            var kx = new Keyframe[keys.Count];
            var ky = new Keyframe[keys.Count];
            var kz = new Keyframe[keys.Count];
            for (int i = 0; i < keys.Count; i++)
            {
                kx[i] = new Keyframe(keys[i].time, keys[i].x, keys[i].inTangent, keys[i].outTangent);
                ky[i] = new Keyframe(keys[i].time, keys[i].y, keys[i].inTangent, keys[i].outTangent);
                kz[i] = new Keyframe(keys[i].time, keys[i].z, keys[i].inTangent, keys[i].outTangent);
            }

            clip.SetCurve("", componentType, propX, new AnimationCurve(kx));
            clip.SetCurve("", componentType, propY, new AnimationCurve(ky));
            clip.SetCurve("", componentType, propZ, new AnimationCurve(kz));
        }

        private static void SetFloatCurve(AnimationClip clip, Type componentType, string prop,
                                          List<ParsedKeyframe> keys)
        {
            var k = new Keyframe[keys.Count];
            for (int i = 0; i < keys.Count; i++)
                k[i] = new Keyframe(keys[i].time, keys[i].x, keys[i].inTangent, keys[i].outTangent);

            clip.SetCurve("", componentType, prop, new AnimationCurve(k));
        }

        private static Type DetectColorComponent(GameObject go)
        {
            if (go.GetComponent<SpriteRenderer>() != null) return typeof(SpriteRenderer);
            var imageType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image");
            if (imageType != null && go.GetComponent(imageType) != null) return imageType;
            return typeof(SpriteRenderer); // fallback
        }
    }
}
