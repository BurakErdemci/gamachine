import * as THREE from 'three';
// The `.js` suffix is required by three's exports map under this tsconfig.
import { GLTFLoader, type GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js';
import * as SkeletonUtils from 'three/examples/jsm/utils/SkeletonUtils.js';

import { MANNEQUIN_MARKER, type MannequinHandle } from './mannequin';
import { type HumanoidMap } from './humanoidRig';

/**
 * The bundled CC0 mannequin (Quaternius, Universal Animation Library) wearing a
 * mesh-less humanoid clip. Root-relative like the fonts and the Monaco files:
 * `/models/...` resolves against the Next dev server and against `app://` in
 * the packaged build (electron-serve registers it with fetch support, and CSP
 * `connect-src 'self'` covers it).
 */
export const MANNEQUIN_URL = '/models/mannequin.glb';

/** Material name in the glb that takes the secondary (joint) colour. */
export const MANNEQUIN_JOINT_MATERIAL = 'M_Joints';

export const parseMannequinTemplate = async (data: ArrayBuffer): Promise<THREE.Object3D> => {
  const gltf = await new Promise<GLTF>((resolve, reject) => {
    new GLTFLoader().parse(data, '', resolve, reject);
  });
  return gltf.scene;
};

let template: Promise<THREE.Object3D | null> | null = null;

/**
 * The glb, fetched and parsed once per session. Resolves null on any failure
 * so the caller falls back to the procedural figure; a failure is not cached,
 * so a later file gets another try.
 */
export const loadMannequinTemplate = (): Promise<THREE.Object3D | null> => {
  template ??= (async () => {
    const response = await fetch(MANNEQUIN_URL);
    if (!response.ok) throw new Error(`mannequin: HTTP ${response.status}`);
    return parseMannequinTemplate(await response.arrayBuffer());
  })().catch(() => {
    template = null;
    return null;
  });
  return template;
};

/**
 * For aligning a bone to the source's rest pose: the mannequin bones whose
 * position shows where this bone points, nearest first. A bone with none
 * mapped keeps its rest rotation relative to its parent.
 */
const AIM: Record<string, readonly string[]> = (() => {
  const aim: Record<string, string[]> = {
    pelvis: ['spine_01', 'spine_02', 'spine_03', 'neck_01'],
    spine_01: ['spine_02', 'spine_03', 'neck_01', 'Head'],
    spine_02: ['spine_03', 'neck_01', 'Head'],
    spine_03: ['neck_01', 'Head'],
    neck_01: ['Head'],
  };
  for (const s of ['l', 'r']) {
    // No clavicle entry: the mannequin's clavicle runs from the front of the
    // sternum back to the shoulder (13.6 cm deep), a source's (Mixamo, Meshy)
    // runs sideways, and swinging one onto the other threw the shoulder joint
    // ~10 cm forward of the chest. It keeps its own seat on the chest instead.
    aim[`upperarm_${s}`] = [`lowerarm_${s}`];
    aim[`lowerarm_${s}`] = [`hand_${s}`];
    aim[`hand_${s}`] = [`middle_01_${s}`, `index_01_${s}`, `ring_01_${s}`];
    aim[`thigh_${s}`] = [`calf_${s}`];
    aim[`calf_${s}`] = [`foot_${s}`];
    aim[`foot_${s}`] = [`ball_${s}`];
    for (const f of ['thumb', 'index', 'middle', 'ring', 'pinky']) {
      aim[`${f}_01_${s}`] = [`${f}_02_${s}`];
      aim[`${f}_02_${s}`] = [`${f}_03_${s}`];
    }
  }
  return aim;
})();

/** Torso bones also align a left-to-right axis, so the figure's facing follows the source. */
const LATERAL: Record<string, readonly [string, string]> = {
  pelvis: ['thigh_l', 'thigh_r'],
  spine_01: ['thigh_l', 'thigh_r'],
  spine_02: ['upperarm_l', 'upperarm_r'],
  spine_03: ['upperarm_l', 'upperarm_r'],
  neck_01: ['upperarm_l', 'upperarm_r'],
};

const worldPos = (o: THREE.Object3D) => new THREE.Vector3().setFromMatrixPosition(o.matrixWorld);
const worldQuat = (o: THREE.Object3D) => o.getWorldQuaternion(new THREE.Quaternion());

/** Rotation taking the (primary, lateral) frame `from` onto the frame `to`. */
const frameRotation = (
  fromPrimary: THREE.Vector3, fromLateral: THREE.Vector3 | null,
  toPrimary: THREE.Vector3, toLateral: THREE.Vector3 | null,
): THREE.Quaternion => {
  const a = fromPrimary.clone().normalize();
  const b = toPrimary.clone().normalize();
  if (!fromLateral || !toLateral) return new THREE.Quaternion().setFromUnitVectors(a, b);
  const basis = (p: THREE.Vector3, l: THREE.Vector3) => {
    const x = l.clone().sub(p.clone().multiplyScalar(l.dot(p))).normalize();
    const z = new THREE.Vector3().crossVectors(x, p);
    return new THREE.Matrix4().makeBasis(x, p, z);
  };
  const m = basis(b, toLateral).multiply(basis(a, fromLateral).transpose());
  return new THREE.Quaternion().setFromRotationMatrix(m);
};

export interface RiggedMannequin {
  /** Hang this on the stage; it carries the scale into the source's units. */
  object: THREE.Object3D;
  /** The source clip rebaked onto the mannequin, or null when none was given. */
  clip: THREE.AnimationClip | null;
  /** The mannequin's hip bone, for anything that follows the figure. */
  hips: THREE.Object3D;
  handle: MannequinHandle;
}

/** World Y of the lowest bone in `bone`'s subtree. */
const lowestUnder = (bone: THREE.Object3D): number => {
  let lowest = Infinity;
  bone.traverse(o => { if ((o as THREE.Bone).isBone) lowest = Math.min(lowest, worldPos(o).y); });
  return lowest;
};

/** Bones under `root`, keyed by name. */
const bonesByName = (root: THREE.Object3D): Map<string, THREE.Bone> => {
  const map = new Map<string, THREE.Bone>();
  root.traverse(o => { if ((o as THREE.Bone).isBone) map.set(o.name, o as THREE.Bone); });
  return map;
};

/**
 * Put a clone of `templateScene` on `source`'s humanoid rig and rebake `clip`
 * onto it with SkeletonUtils.retargetClip.
 *
 * Rest poses differ (the mannequin is a T-pose with Unreal bone axes, a source
 * may be an A-pose with Mixamo or Blender axes), so the mannequin is first
 * swung bone by bone onto the source's rest directions, and each bone's
 * `localOffset` is the rotation from the source's rest to that aligned pose.
 * The clip then moves each mannequin bone by the source bone's change from
 * rest, in world space, which keeps limbs untwisted.
 *
 * `source` must not be on a scaled or moved parent yet: its root space is taken
 * as the stage's space.
 */
export const buildRiggedMannequin = (
  templateScene: THREE.Object3D,
  source: THREE.Object3D,
  map: HumanoidMap,
  clip: THREE.AnimationClip | null,
): RiggedMannequin => {
  const instance = SkeletonUtils.clone(templateScene);
  instance.updateMatrixWorld(true);
  const meshes: THREE.SkinnedMesh[] = [];
  instance.traverse(o => { if ((o as THREE.SkinnedMesh).isSkinnedMesh) meshes.push(o as THREE.SkinnedMesh); });
  if (meshes.length === 0) throw new Error('mannequin: no skinned mesh');
  const skeleton = meshes[0].skeleton;
  const skeletons = new Set(meshes.map(mesh => mesh.skeleton));
  // Per-instance materials: the theme tints them, the cached template's stay put.
  const materials = new Map<THREE.Material, THREE.Material>();
  for (const mesh of meshes) {
    const original = mesh.material as THREE.Material;
    if (!materials.has(original)) materials.set(original, original.clone());
    mesh.material = materials.get(original)!;
    // Bones carry the figure far from its bind-pose bounds.
    mesh.frustumCulled = false;
    mesh.userData[MANNEQUIN_MARKER] = true;
  }

  const target = bonesByName(instance);
  const src = bonesByName(source);
  const pairs = Object.entries(map).filter(([t, s]) => target.has(t) && src.has(s));
  const sourceOf = (t: string) => src.get(map[t]);

  // Retargeting drives the source through its clip; it is put back afterwards.
  const sourceLoaded = snapshot(source);
  try {
    source.updateMatrixWorld(true);
    const sourceRest = new Map(pairs.map(([t]) => [t, worldQuat(sourceOf(t)!)] as const));

    // Swing the mannequin onto the source's rest directions, parents first.
    for (const bone of skeleton.bones) {
      const aimAt = (AIM[bone.name] ?? []).find(n => target.has(n) && sourceOf(n) && sourceOf(bone.name));
      if (!aimAt) continue;
      const s = sourceOf(bone.name)!;
      const lateral = LATERAL[bone.name];
      const lateralOk = lateral && lateral.every(n => target.has(n) && sourceOf(n));
      const tPrimary = worldPos(target.get(aimAt)!).sub(worldPos(bone));
      const sPrimary = worldPos(sourceOf(aimAt)!).sub(worldPos(s));
      if (tPrimary.lengthSq() < 1e-12 || sPrimary.lengthSq() < 1e-12) continue;
      const tLateral = lateralOk ? worldPos(target.get(lateral[0])!).sub(worldPos(target.get(lateral[1])!)) : null;
      const sLateral = lateralOk ? worldPos(sourceOf(lateral[0])!).sub(worldPos(sourceOf(lateral[1])!)) : null;
      const swing = frameRotation(tPrimary, tLateral, sPrimary, sLateral);
      const aligned = swing.multiply(worldQuat(bone));
      const parentWorld = bone.parent ? worldQuat(bone.parent) : new THREE.Quaternion();
      bone.quaternion.copy(parentWorld.invert().multiply(aligned));
      bone.updateMatrixWorld(true);
    }

    const localOffsets: Record<string, THREE.Matrix4> = {};
    for (const [t] of pairs) {
      const offset = sourceRest.get(t)!.clone().invert().multiply(worldQuat(target.get(t)!));
      localOffsets[t] = new THREE.Matrix4().makeRotationFromQuaternion(offset);
    }

    // Mannequin units per source unit (Mixamo FBX arrives in centimetres, the
    // mannequin is in metres). Both floors are the lowest bone under the hips
    // (toe tips on Mixamo and on the mannequin), so the ratios compare like
    // with like; the mannequin's `root` bone sits at the origin.
    const pelvis = target.get('pelvis')!;
    const hips = sourceOf('pelvis')!;
    const sourceFloor = lowestUnder(hips);
    const targetFloor = lowestUnder(pelvis);
    const heightRatio = (t: THREE.Object3D, s: THREE.Object3D) => {
      const sh = worldPos(s).y - sourceFloor;
      const th = worldPos(t).y - targetFloor;
      return sh > 1e-6 && th > 1e-6 ? th / sh : null;
    };
    const sourceHipHeight = worldPos(hips).y - sourceFloor;
    // The hips' travel scales with leg length...
    const toTarget = heightRatio(pelvis, hips) ?? 1;
    // ...but the figure's size with standing height: Mixamo hips sit at 68% of
    // head height, the mannequin's at 58%, and scaling by hips alone drew
    // Mixamo figures 15% too large.
    const top = ['Head', 'neck_01'].find(n => target.has(n) && sourceOf(n));
    const bodyScale = (top ? heightRatio(target.get(top)!, sourceOf(top)!) : null) ?? toTarget;

    let retargeted: THREE.AnimationClip | null = null;
    if (clip) {
      // A bare holder rather than a SkeletonHelper: retargetClip only needs
      // `.skeleton`, and a helper would allocate line geometry nobody frees.
      const holder = new THREE.Object3D() as THREE.Object3D & { skeleton: THREE.Skeleton };
      holder.skeleton = new THREE.Skeleton([...src.values()]);
      const names: Record<string, string> = {};
      for (const [t, s] of pairs) names[t] = s;
      const fps = Math.min(60, Math.max(...clip.tracks.map(tr => tr.times.length)) / clip.duration);
      // `localOffsets` is read by three's retarget() but missing from its typings.
      const options: SkeletonUtils.RetargetClipOptions & { localOffsets: Record<string, THREE.Matrix4> } = {
        names,
        hip: hips.name,
        scale: toTarget,
        localOffsets,
        fps: Number.isFinite(fps) && fps > 0 ? fps : 30,
      };
      const baked = SkeletonUtils.retargetClip(meshes[0], holder, clip, options);
      // Rebind from `.bones[x]` (which needs a SkinnedMesh root) to plain node
      // names, so the mixer can run on the whole instance. Only the hips keep
      // their position track: retargetClip writes no other.
      const tracks = baked.tracks.map(track => {
        const match = /^\.bones\[(.+)\]\.(\w+)$/.exec(track.name);
        if (match) track.name = `${match[1]}.${match[2]}`;
        return track;
      });
      retargeted = new THREE.AnimationClip(clip.name, clip.duration, tracks);
      restore(sourceLoaded);
    }

    const holderObject = new THREE.Group();
    holderObject.name = 'mannequin:rigged';
    holderObject.add(instance);
    holderObject.scale.setScalar(1 / bodyScale);

    // Ground the figure at frame 0. A source touching its floor (within a tenth
    // of hip height) gets the mannequin's lowest vertex put on the floor, so
    // soles and a lying body rest on the grid whatever the toe-joint heights;
    // a source starting in the air keeps its lowest joint's height instead.
    const mixer = retargeted ? new THREE.AnimationMixer(instance) : null;
    const sampler = retargeted ? new THREE.AnimationMixer(source) : null;
    if (mixer && sampler) {
      mixer.clipAction(retargeted!).play();
      mixer.update(0);
      sampler.clipAction(clip!).play();
      sampler.update(0);
    }
    holderObject.updateMatrixWorld(true);
    source.updateMatrixWorld(true);
    let lowest: [string, number] | null = null;
    for (const [t] of pairs) {
      const y = worldPos(sourceOf(t)!).y;
      if (!lowest || y < lowest[1]) lowest = [t, y];
    }
    if (lowest && lowest[1] - sourceFloor > 0.1 * sourceHipHeight) {
      holderObject.position.y = lowest[1] - worldPos(target.get(lowest[0])!).y;
    } else {
      holderObject.position.y = sourceFloor - new THREE.Box3().setFromObject(holderObject, true).min.y;
    }
    holderObject.updateMatrixWorld(true);
    // The mannequin's sampler is NOT stopped: stopping restores the bones' state
    // from before it bound, and the caller frames and first paints frame 0.
    if (sampler) {
      sampler.stopAllAction();
      sampler.uncacheRoot(source);
    }
    let disposed = false;
    const handle: MannequinHandle = {
      meshes,
      dispose: () => {
        if (disposed) return;
        disposed = true;
        holderObject.removeFromParent();
        // Geometry is shared with the cached template and is not freed here.
        for (const material of materials.values()) material.dispose();
        for (const skeleton of skeletons) skeleton.dispose();
      },
    };
    return { object: holderObject, clip: retargeted, hips: pelvis, handle };
  } catch (error) {
    for (const material of materials.values()) material.dispose();
    for (const skeleton of skeletons) skeleton.dispose();
    throw error;
  } finally {
    restore(sourceLoaded);
    source.updateMatrixWorld(true);
  }
};

type Transform = [THREE.Vector3, THREE.Quaternion, THREE.Vector3];

/** Local transforms of every node under `root`, to put back after sampling. */
const snapshot = (root: THREE.Object3D): Map<THREE.Object3D, Transform> => {
  const saved = new Map<THREE.Object3D, Transform>();
  root.traverse(o => saved.set(o, [o.position.clone(), o.quaternion.clone(), o.scale.clone()]));
  return saved;
};

const restore = (saved: Map<THREE.Object3D, Transform>): void => {
  for (const [o, [p, q, s]] of saved) { o.position.copy(p); o.quaternion.copy(q); o.scale.copy(s); }
};
