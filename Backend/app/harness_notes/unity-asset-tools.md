---
title: Which Unity tools create or edit assets, materials, shaders, textures, animation, physics and effects?
keywords: varlık, materyal, shader, doku, animasyon, fizik, parçacık, asset, material, texture, sprite, fbx, vfx, probuilder, graphics, scriptable object
covers: manage_asset, manage_scriptable_object, manage_fbx, manage_material, manage_shader, manage_texture, manage_graphics, manage_sprite, manage_vfx, manage_probuilder, manage_animation, manage_physics
---
These Unity MCP tools create and edit project content through Unity, so the .meta files and GUIDs stay intact.

- manage_asset: create, modify, search, move, rename, delete assets. Use it instead of raw file moves.
- manage_scriptable_object, manage_fbx (rigs, avatars, clips), manage_material, manage_shader, manage_texture (patterns, gradients, noise), manage_sprite (slice sheets, 2D sprite animation), manage_probuilder (meshes).
- manage_graphics: volumes, post-processing, light baking, rendering stats, pipeline settings (URP).
- manage_vfx: particle systems, trails and visual effects.
- manage_animation: Animator control, AnimatorController and AnimationClip operations.
- manage_physics: 3D and 2D physics settings, collision matrix, materials, joints, queries.
- manage_fbx, manage_animation and manage_sprite are in the opt-in animation group; manage_shader, manage_texture and manage_vfx in vfx; manage_probuilder in probuilder; manage_scriptable_object in scripting_ext.
- Not every group is always listed: the server filters tools by group and by connection URL, so a tool you expect may be missing (see unity-connection-states).
- Raw-text writes to Unity YAML assets are refused; these tools are the route (see unity-file-rule).

Sources: unity-mcp/manifest.json, docs/unity-mcp.md, docs/security.md.
