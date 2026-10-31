---
title: Which Unity tools find and change scenes, GameObjects, components, prefabs and the editor?
keywords: sahne, nesne, gameobject, bileşen, prefab, editör, kamera, arayüz, scene, component, hierarchy, camera, ui toolkit, play mode, undo
covers: manage_scene, find_gameobjects, manage_gameobject, manage_components, manage_prefabs, manage_editor, manage_camera, manage_ui
---
These Unity MCP tools work on what is open in the Editor. They exist only while Unity MCP is connected.

- find_gameobjects: find objects by criteria; use the returned instance IDs. match_mode exact, contains or prefix.
- manage_scene: load, save, hierarchy query, multi-scene editing, templates, validation. Save open scenes before code or menu items that rebuild scenes, or an unsaved-scene dialog blocks Unity.
- manage_gameobject, manage_components (action set_property), manage_prefabs: create, modify, parent, delete, instantiate, unpack.
- manage_editor: editor state, play mode, preferences, undo and redo; undo_action reverts one agent action by its action_id while it is still the latest undo step.
- manage_camera: cameras including Cinemachine; manage_ui: UI Toolkit (UXML, USS, UIDocument).
- manage_scene, find_gameobjects, manage_gameobject, manage_components and manage_prefabs first refresh a project changed outside the Editor, so in step mode every action of them opens a card, pure queries included; auto mode shows none (see approval-modes).
- manage_ui is in the opt-in ui group, so it is listed only when that group is enabled or on /mcp/full.
- Every mutating call is one named Unity undo step; file operations are not fully undoable.

Sources: unity-mcp/manifest.json, docs/unity-mcp.md, docs/security.md, Backend/app/agent_guide.py.
