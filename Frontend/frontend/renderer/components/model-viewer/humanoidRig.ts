import type * as THREE from 'three';

/**
 * Decides whether a clip's skeleton is a humanoid the bundled mannequin can
 * wear, and if so which source bone drives each mannequin bone. Pure: it reads
 * names and parent links only, so it runs on plain data in tests.
 *
 * Mannequin bone names are the Unreal-style ones in `public/models/mannequin.glb`.
 */

export interface RigBone {
  name: string;
  /** Parent bone's name, or null when the parent is not a bone. */
  parent: string | null;
}

/** `{ mannequinBone: sourceBone }`. */
export type HumanoidMap = Record<string, string>;

/**
 * Lower-case, separators removed, rig prefix stripped. FBXLoader sanitises
 * `mixamorig:Hips` to `mixamorigHips` (the colon is reserved in track names),
 * so the Mixamo prefix is matched with and without its colon and with the
 * numbered variants Mixamo emits for repeated downloads (`mixamorig5`).
 */
export const normalizeBoneName = (name: string): string =>
  name
    .replace(/^.*:/, '')
    .replace(/^mixamorig\d*/i, '')
    .toLowerCase()
    .replace(/[\s_.\-]/g, '');

const SIDES = [
  ['l', 'left'],
  ['r', 'right'],
] as const;

const FINGERS = ['thumb', 'index', 'middle', 'ring', 'pinky'] as const;

/** Mannequin bone -> accepted normalised source names, outside the spine. */
const SLOTS: Record<string, readonly string[]> = (() => {
  const slots: Record<string, string[]> = {
    pelvis: ['hips', 'pelvis', 'hip'],
    neck_01: ['neck', 'neck01', 'neck1'],
    Head: ['head'],
  };
  for (const [s, side] of SIDES) {
    slots[`clavicle_${s}`] = [`${side}shoulder`, `clavicle${s}`];
    slots[`upperarm_${s}`] = [`${side}arm`, `${side}upperarm`, `upperarm${s}`];
    slots[`lowerarm_${s}`] = [`${side}forearm`, `${side}lowerarm`, `lowerarm${s}`];
    slots[`hand_${s}`] = [`${side}hand`, `hand${s}`];
    slots[`thigh_${s}`] = [`${side}upleg`, `${side}upperleg`, `${side}thigh`, `thigh${s}`];
    slots[`calf_${s}`] = [`${side}leg`, `${side}lowerleg`, `${side}calf`, `calf${s}`];
    slots[`foot_${s}`] = [`${side}foot`, `foot${s}`];
    slots[`ball_${s}`] = [`${side}toebase`, `${side}toe`, `ball${s}`];
    for (const finger of FINGERS) {
      for (const n of [1, 2, 3]) {
        slots[`${finger}_0${n}_${s}`] = [`${side}hand${finger}${n}`, `${finger}0${n}${s}`];
      }
    }
  }
  return slots;
})();

const CORE = [
  'pelvis',
  'upperarm_l', 'upperarm_r', 'lowerarm_l', 'lowerarm_r',
  'thigh_l', 'thigh_r', 'calf_l', 'calf_r',
] as const;

const SPINE_PATTERN = /^(spine\d*|chest|upperchest)$/;

/** Mannequin spine slots, bottom to top. */
export const MANNEQUIN_SPINE = ['spine_01', 'spine_02', 'spine_03'] as const;

const depthOf = (name: string, parents: Map<string, string | null>): number => {
  let depth = 0;
  for (let at = parents.get(name); at != null && depth < 1000; at = parents.get(at)) depth += 1;
  return depth;
};

const isDescendant = (name: string, ancestor: string, parents: Map<string, string | null>): boolean => {
  let guard = 0;
  for (let at = parents.get(name); at != null && guard < 1000; at = parents.get(at), guard += 1) {
    if (at === ancestor) return true;
  }
  return false;
};

/**
 * The `{ mannequinBone: sourceBone }` map for a humanoid rig, or null when the
 * core set (hips, two spine bones, neck or head, both upper arms, forearms,
 * thighs and calves) is not all there. Fingers and toes are optional.
 *
 * The spine is ordered by HIERARCHY, not by name: Mixamo counts up the back
 * (Spine, Spine1, Spine2) but Meshy counts down it (Hips > Spine02 > Spine01 >
 * Spine), so a name sort would map one of them upside down.
 */
export const detectHumanoidRig = (bones: readonly RigBone[]): HumanoidMap | null => {
  const byKey = new Map<string, string>();
  for (const bone of bones) {
    const key = normalizeBoneName(bone.name);
    // First wins: a file holding two rigs keeps the first one consistent.
    if (!byKey.has(key)) byKey.set(key, bone.name);
  }
  const parents = new Map(bones.map(b => [b.name, b.parent] as const));

  const map: HumanoidMap = {};
  for (const [slot, keys] of Object.entries(SLOTS)) {
    const source = keys.map(k => byKey.get(k)).find((n): n is string => n !== undefined);
    if (source !== undefined) map[slot] = source;
  }
  if (CORE.some(slot => map[slot] === undefined)) return null;
  if (map.neck_01 === undefined && map.Head === undefined) return null;

  const hips = map.pelvis;
  const spine = bones
    .filter(b => SPINE_PATTERN.test(normalizeBoneName(b.name)) && isDescendant(b.name, hips, parents))
    .map(b => b.name)
    .sort((a, b) => depthOf(a, parents) - depthOf(b, parents));
  if (spine.length < 2) return null;
  // Bottom and top always map; a third source bone fills the middle, extra
  // ones beyond three are left to their parents.
  map.spine_01 = spine[0];
  map.spine_03 = spine[spine.length - 1];
  if (spine.length >= 3) map.spine_02 = spine[Math.floor(spine.length / 2)];
  return map;
};

/** `RigBone`s of every bone under `root`, in traversal order. */
export const rigBonesOf = (root: THREE.Object3D): RigBone[] => {
  const bones: RigBone[] = [];
  root.traverse(child => {
    if ((child as THREE.Bone).isBone !== true) return;
    const parent = child.parent as THREE.Bone | null;
    bones.push({ name: child.name, parent: parent?.isBone === true ? parent.name : null });
  });
  return bones;
};
