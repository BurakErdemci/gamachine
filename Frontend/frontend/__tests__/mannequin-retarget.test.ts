/**
 * The bundled mannequin wearing a Mixamo clip. The source is synthetic (no
 * third-party clip is redistributable here): a Mixamo-named skeleton in
 * centimetres, built on the mannequin's own joint positions so a correct
 * retarget lands the mannequin's hands and feet where the source's are.
 */
import { describe, it, expect, beforeAll, afterEach, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import * as SkeletonUtils from 'three/examples/jsm/utils/SkeletonUtils.js'

vi.mock('three/examples/jsm/utils/SkeletonUtils.js', async importOriginal => {
  const actual = await importOriginal<typeof SkeletonUtils>()
  return { ...actual, clone: vi.fn(actual.clone), retargetClip: vi.fn(actual.retargetClip) }
})

afterEach(() => { vi.restoreAllMocks() })

import { detectHumanoidRig, rigBonesOf } from '../renderer/components/model-viewer/humanoidRig'
import { buildRiggedMannequin, MANNEQUIN_JOINT_MATERIAL, parseMannequinTemplate } from '../renderer/components/model-viewer/riggedMannequin'
import { clearContent, mountParsedModel, type ClearTarget } from '../renderer/components/model-viewer/ModelPreviewPanel'

const glb = (): ArrayBuffer => {
  // Copied into an ArrayBuffer of jsdom's realm: GLTFLoader tests `instanceof ArrayBuffer`.
  const buf = readFileSync(resolve(__dirname, '../renderer/public/models/mannequin.glb'))
  const out = new ArrayBuffer(buf.byteLength)
  new Uint8Array(out).set(buf)
  return out
}

/** Mixamo name -> the mannequin bone whose rest position it takes. */
const MIXAMO: [string, string][] = [
  ['Hips', 'pelvis'], ['Spine', 'spine_01'], ['Spine1', 'spine_02'], ['Spine2', 'spine_03'],
  ['Neck', 'neck_01'], ['Head', 'Head'],
  ...(['Left', 'Right'] as const).flatMap(side => {
    const s = side === 'Left' ? 'l' : 'r'
    return [
      [`${side}Shoulder`, `clavicle_${s}`], [`${side}Arm`, `upperarm_${s}`], [`${side}ForeArm`, `lowerarm_${s}`],
      [`${side}Hand`, `hand_${s}`], [`${side}UpLeg`, `thigh_${s}`], [`${side}Leg`, `calf_${s}`],
      [`${side}Foot`, `foot_${s}`], [`${side}ToeBase`, `ball_${s}`],
      // Unmapped tip, as in a real Mixamo export; it is the rig's floor.
      [`${side}Toe_End`, `ball_leaf_${s}`],
    ] as [string, string][]
  }),
]
const PREFIX = 'mixamorig'

/**
 * Mixamo-named T-pose in centimetres, identity rotations, on the mannequin's
 * joint layout; `place` moves a joint (given the mannequin's position for it).
 */
const syntheticMixamo = (
  template: THREE.Object3D,
  place: (mixamo: string, at: THREE.Vector3) => THREE.Vector3 = (_, at) => at,
): THREE.Group => {
  template.updateMatrixWorld(true)
  const at = (name: string) => {
    const p = new THREE.Vector3().setFromMatrixPosition(template.getObjectByName(name)!.matrixWorld).multiplyScalar(100)
    return place(MIXAMO.find(([, m]) => m === name)![0], p)
  }
  const parentOf: Record<string, string | null> = {}
  for (const [mixamo, mannequin] of MIXAMO) {
    const parentBone = template.getObjectByName(mannequin)!.parent!
    parentOf[mixamo] = MIXAMO.find(([, m]) => m === parentBone.name)?.[0] ?? null
  }
  const root = new THREE.Group()
  const bones = new Map<string, THREE.Bone>()
  for (const [mixamo, mannequin] of MIXAMO) {
    const bone = new THREE.Bone()
    bone.name = PREFIX + mixamo
    const parent = parentOf[mixamo]
    bone.position.copy(at(mannequin).sub(parent ? at(MIXAMO.find(([m]) => m === parent)![1]) : new THREE.Vector3()))
    ;(parent ? bones.get(parent)! : root).add(bone)
    bones.set(mixamo, bone)
  }
  root.updateMatrixWorld(true)
  return root
}

const q = (axis: THREE.Vector3, degrees: number) =>
  new THREE.Quaternion().setFromAxisAngle(axis, THREE.MathUtils.degToRad(degrees)).toArray()

/** Arms down and bent, a leg forward, the hips travelling: what a real clip does. */
const clip = () => new THREE.AnimationClip('synthetic', 1, [
  new THREE.VectorKeyframeTrack(`${PREFIX}Hips.position`, [0, 1], [0, 91.7, 0, 12, 84, 6]),
  new THREE.QuaternionKeyframeTrack(`${PREFIX}Hips.quaternion`, [0, 1], [...q(new THREE.Vector3(0, 1, 0), 0), ...q(new THREE.Vector3(0, 1, 0), 30)]),
  new THREE.QuaternionKeyframeTrack(`${PREFIX}LeftArm.quaternion`, [0, 1], [...q(new THREE.Vector3(0, 0, 1), 0), ...q(new THREE.Vector3(0, 0, 1), -70)]),
  new THREE.QuaternionKeyframeTrack(`${PREFIX}LeftForeArm.quaternion`, [0, 1], [...q(new THREE.Vector3(0, 1, 0), 0), ...q(new THREE.Vector3(0, 1, 0), 60)]),
  new THREE.QuaternionKeyframeTrack(`${PREFIX}RightUpLeg.quaternion`, [0, 1], [...q(new THREE.Vector3(1, 0, 0), 0), ...q(new THREE.Vector3(1, 0, 0), -45)]),
  new THREE.QuaternionKeyframeTrack(`${PREFIX}RightLeg.quaternion`, [0, 1], [...q(new THREE.Vector3(1, 0, 0), 0), ...q(new THREE.Vector3(1, 0, 0), 50)]),
  // Mixamo keys every bone's position too; only the hips may survive.
  new THREE.VectorKeyframeTrack(`${PREFIX}LeftForeArm.position`, [0, 1], [27, 0, 0, 27, 0, 0]),
])

const worldOf = (o: THREE.Object3D) => new THREE.Vector3().setFromMatrixPosition(o.matrixWorld)

describe('buildRiggedMannequin', () => {
  let template: THREE.Object3D
  beforeAll(async () => { template = await parseMannequinTemplate(glb()) })

  it.each(['retarget', 'sampling'])('restores the source and frees cloned resources when %s throws', step => {
    const source = syntheticMixamo(template)
    const before = new Map<THREE.Object3D, number[]>()
    source.traverse(o => before.set(o, [...o.position.toArray(), ...o.quaternion.toArray(), ...o.scale.toArray(), ...o.matrixWorld.elements]))
    const failure = new Error(`${step} failed`)
    const materials: THREE.Material[] = []
    const skeletons: THREE.Skeleton[] = []
    const geometryDisposals: ReturnType<typeof vi.spyOn>[] = []
    const clone = SkeletonUtils.clone
    const realClone = vi.mocked(clone).getMockImplementation()!
    vi.mocked(clone).mockImplementationOnce(object => {
      const result = realClone(object)
      result.traverse(o => {
        if ((o as THREE.SkinnedMesh).isSkinnedMesh) {
          const mesh = o as THREE.SkinnedMesh
          skeletons.push(mesh.skeleton)
          geometryDisposals.push(vi.spyOn(mesh.geometry, 'dispose'))
        }
      })
      return result
    })
    const materialDispose = vi.spyOn(THREE.Material.prototype, 'dispose').mockImplementation(function (this: THREE.Material) { materials.push(this) })
    const skeletonDispose = vi.spyOn(THREE.Skeleton.prototype, 'dispose')
    if (step === 'retarget') {
      vi.mocked(SkeletonUtils.retargetClip).mockImplementationOnce(() => {
        source.getObjectByName(`${PREFIX}Hips`)!.position.y += 100
        source.getObjectByName(`${PREFIX}LeftArm`)!.quaternion.set(0, 1, 0, 0)
        source.getObjectByName(`${PREFIX}RightLeg`)!.scale.setScalar(2)
        source.updateMatrixWorld(true)
        throw failure
      })
    } else {
      const update = THREE.AnimationMixer.prototype.update
      vi.spyOn(THREE.AnimationMixer.prototype, 'update').mockImplementation(function (this: THREE.AnimationMixer, delta) {
        const result = update.call(this, delta)
        if (this.getRoot() === source && delta === 0) {
          source.getObjectByName(`${PREFIX}Hips`)!.position.y += 100
          source.updateMatrixWorld(true)
          throw failure
        }
        return result
      })
    }
    let thrown: unknown
    try { buildRiggedMannequin(template, source, detectHumanoidRig(rigBonesOf(source))!, clip()) }
    catch (error) { thrown = error }
    expect(thrown).toBe(failure)
    source.traverse(o => expect([...o.position.toArray(), ...o.quaternion.toArray(), ...o.scale.toArray(), ...o.matrixWorld.elements]).toEqual(before.get(o)))
    expect(materialDispose).toHaveBeenCalledTimes(2)
    const sharedMaterials: THREE.Material[] = []
    template.traverse(o => { if ((o as THREE.Mesh).isMesh) sharedMaterials.push((o as THREE.Mesh).material as THREE.Material) })
    expect(materials.every(m => !sharedMaterials.includes(m))).toBe(true)
    expect(new Set(skeletons).size).toBe(2)
    for (const skeleton of new Set(skeletons)) expect(skeletonDispose.mock.instances.filter(s => s === skeleton)).toHaveLength(1)
    for (const disposal of geometryDisposals) expect(disposal).not.toHaveBeenCalled()
    template.traverse(o => {
      if ((o as THREE.SkinnedMesh).isSkinnedMesh) expect(skeletonDispose.mock.instances).not.toContain((o as THREE.SkinnedMesh).skeleton)
    })
  })

  it('disposes both distinct cloned skeletons and their bone textures exactly once', () => {
    const source = syntheticMixamo(template)
    const rigged = buildRiggedMannequin(template, source, detectHumanoidRig(rigBonesOf(source))!, clip())
    const skeletons = [...new Set(rigged.handle.meshes.map(m => (m as THREE.SkinnedMesh).skeleton))]
    expect(skeletons).toHaveLength(2)
    const disposals = skeletons.map(s => { s.computeBoneTexture(); return vi.spyOn(s.boneTexture!, 'dispose') })
    const geometryDisposals = rigged.handle.meshes.map(m => vi.spyOn(m.geometry, 'dispose'))
    rigged.handle.dispose()
    rigged.handle.dispose()
    for (const disposal of disposals) expect(disposal).toHaveBeenCalledTimes(1)
    for (const disposal of geometryDisposals) expect(disposal).not.toHaveBeenCalled()
  })

  it('deduplicates a skeleton shared by the cloned meshes', () => {
    const realClone = vi.mocked(SkeletonUtils.clone).getMockImplementation()!
    vi.mocked(SkeletonUtils.clone).mockImplementationOnce(object => {
      const result = realClone(object)
      const meshes: THREE.SkinnedMesh[] = []
      result.traverse(o => { if ((o as THREE.SkinnedMesh).isSkinnedMesh) meshes.push(o as THREE.SkinnedMesh) })
      expect(meshes).toHaveLength(2)
      meshes[1].skeleton = meshes[0].skeleton
      return result
    })
    const source = syntheticMixamo(template)
    const rigged = buildRiggedMannequin(template, source, detectHumanoidRig(rigBonesOf(source))!, null)
    const dispose = vi.spyOn((rigged.handle.meshes[0] as THREE.SkinnedMesh).skeleton, 'dispose')
    rigged.handle.dispose()
    expect(dispose).toHaveBeenCalledTimes(1)
  })

  it('rebakes the clip onto mannequin bones only, with a position track on the hips alone', () => {
    const source = syntheticMixamo(template)
    const map = detectHumanoidRig(rigBonesOf(source))!
    expect(map).not.toBeNull()
    const rigged = buildRiggedMannequin(template, source, map, clip())
    const mannequinBones = new Set<string>()
    rigged.object.traverse(o => { if ((o as THREE.Bone).isBone) mannequinBones.add(o.name) })

    const tracks = rigged.clip!.tracks
    expect(tracks.length).toBeGreaterThan(0)
    for (const track of tracks) expect(mannequinBones.has(track.name.split('.')[0])).toBe(true)
    expect(tracks.filter(t => t.name.endsWith('.position')).map(t => t.name)).toEqual(['pelvis.position'])
  })

  it.each([0, 0.4, 0.75, 1])('puts hands and feet where the source has them at t=%s', time => {
    const source = syntheticMixamo(template)
    const map = detectHumanoidRig(rigBonesOf(source))!
    const sourceClip = clip()
    const rigged = buildRiggedMannequin(template, source, map, sourceClip)
    // The mannequin was given metres and lands in the source's centimetres.
    expect(rigged.object.scale.x).toBeCloseTo(100, 0)

    const sampleTarget = new THREE.AnimationMixer(rigged.object)
    sampleTarget.clipAction(rigged.clip!).play()
    sampleTarget.setTime(time)
    const sampleSource = new THREE.AnimationMixer(source)
    sampleSource.clipAction(sourceClip).play()
    sampleSource.setTime(time)
    rigged.object.updateMatrixWorld(true)
    source.updateMatrixWorld(true)

    for (const joint of ['hand_l', 'hand_r', 'foot_l', 'foot_r', 'Head', 'pelvis']) {
      const got = worldOf(rigged.object.getObjectByName(joint)!)
      const want = worldOf(source.getObjectByName(map[joint])!)
      // Centimetres. The floors differ by the toe-joint height the grounding
      // snaps away, so a couple of centimetres is the honest bound.
      expect(got.distanceTo(want), joint).toBeLessThan(3)
    }
  })

  it('does not free the shared template geometry when disposed', () => {
    const source = syntheticMixamo(template)
    const rigged = buildRiggedMannequin(template, source, detectHumanoidRig(rigBonesOf(source))!, clip())
    const geometrySpies = rigged.handle.meshes.map(m => vi.spyOn(m.geometry, 'dispose'))
    const materials = rigged.handle.meshes.map(m => m.material as THREE.Material)
    const parent = new THREE.Group().add(rigged.object)
    rigged.handle.dispose()
    expect(parent.children).toHaveLength(0)
    for (const spy of geometrySpies) expect(spy).not.toHaveBeenCalled()
    // Its own (cloned) materials are freed; the template's are not the same objects.
    let templateMaterials = 0
    template.traverse(o => { if (materials.includes((o as THREE.Mesh).material as THREE.Material)) templateMaterials += 1 })
    expect(templateMaterials).toBe(0)
    expect(materials.map(m => m.name).sort()).toEqual(['M_Main', MANNEQUIN_JOINT_MATERIAL].sort())
  })
})

describe('buildRiggedMannequin on Mixamo proportions', () => {
  let template: THREE.Object3D
  beforeAll(async () => { template = await parseMannequinTemplate(glb()) })

  // Real Mixamo rest (measured, goalkeeper catch.fbx): about as tall as the
  // mannequin at the head (156 cm) but with the hips at 105.5 cm, not 91.7,
  // and a clavicle running straight out sideways where the mannequin's starts
  // at the front of the sternum.
  const HIP = 91.7, HEAD = 156.9, MIXAMO_HIP = 105.5
  const lift = (y: number) => y < HIP
    ? y * MIXAMO_HIP / HIP
    : MIXAMO_HIP + (y - HIP) * (HEAD - MIXAMO_HIP) / (HEAD - HIP)
  const mixamoLike = () => syntheticMixamo(template, (name, p) => {
    if (!name.endsWith('Shoulder')) return new THREE.Vector3(p.x, lift(p.y), p.z)
    const arm = template.getObjectByName(name.startsWith('Left') ? 'upperarm_l' : 'upperarm_r')!
    const shoulder = new THREE.Vector3().setFromMatrixPosition(arm.matrixWorld).multiplyScalar(100)
    return new THREE.Vector3(shoulder.x - Math.sign(shoulder.x) * 12.5, lift(shoulder.y) + 3.1, shoulder.z)
  })

  it('stands as tall as the source, not as high at the hips', () => {
    const source = mixamoLike()
    const rigged = buildRiggedMannequin(template, source, detectHumanoidRig(rigBonesOf(source))!, null)
    rigged.object.updateMatrixWorld(true)
    let floor = Infinity
    source.traverse(o => { floor = Math.min(floor, worldOf(o).y) })
    const want = worldOf(source.getObjectByName(`${PREFIX}Head`)!).y - floor
    const got = worldOf(rigged.object.getObjectByName('Head')!).y - floor
    expect(Math.abs(got - want) / want).toBeLessThan(0.05)
  })
})

describe('mountParsedModel with the bundled mannequin', () => {
  let template: THREE.Object3D
  beforeAll(async () => { template = await parseMannequinTemplate(glb()) })

  const fakeStage = (): ClearTarget => ({
    camera: new THREE.PerspectiveCamera(50, 1.5, 0.1, 1000),
    controls: { enableDamping: true, target: new THREE.Vector3(), minDistance: 0, maxDistance: Infinity, update: vi.fn(() => false) },
    content: new THREE.Group(),
    grid: new THREE.GridHelper(10, 20),
    render: vi.fn(),
    playback: null,
    playing: false,
    wake: vi.fn(),
    mannequin: null,
    frame: null,
  })

  const parsedSource = () => {
    const object = syntheticMixamo(template)
    const bounds = { box: new THREE.Box3().setFromPoints([...rigBonesOf(object)].map(b => worldOf(object.getObjectByName(b.name)!))), skeleton: true }
    return { parsed: { object, clips: [clip()] }, bounds }
  }

  it('wears the bundled mannequin for a humanoid rig and plays the rebaked clip', () => {
    const stage = fakeStage()
    const { parsed, bounds } = parsedSource()
    const map = detectHumanoidRig(rigBonesOf(parsed.object))!
    const duration = mountParsedModel(stage, parsed, bounds, { template, map })
    expect(duration).toBeCloseTo(1, 5)
    expect(stage.mannequin!.meshes.every(m => (m as THREE.SkinnedMesh).isSkinnedMesh)).toBe(true)
    expect(stage.content.getObjectByName('mannequin:contactShadow')).toBeDefined()
    clearContent(stage)
    expect(stage.content.children).toHaveLength(0)
    expect(stage.mannequin).toBeNull()
  })

  it('falls back to the procedural figure when the bundled one cannot be built', () => {
    const stage = fakeStage()
    const { parsed, bounds } = parsedSource()
    const map = detectHumanoidRig(rigBonesOf(parsed.object))!
    // A template with no skinned mesh: the build throws.
    mountParsedModel(stage, parsed, bounds, { template: new THREE.Group(), map })
    expect(stage.mannequin!.meshes.length).toBeGreaterThan(0)
    expect(stage.mannequin!.meshes.some(m => (m as THREE.SkinnedMesh).isSkinnedMesh)).toBe(false)
    expect(stage.playback!.duration).toBeCloseTo(1, 5)
  })

  it('removes and disposes a mounted rigged figure when the bounds step throws', () => {
    const stage = fakeStage()
    const { parsed, bounds } = parsedSource()
    const map = detectHumanoidRig(rigBonesOf(parsed.object))!
    let dispose: ReturnType<typeof vi.spyOn> | undefined
    const actions = vi.spyOn(THREE.AnimationMixer.prototype, 'clipAction')
    vi.spyOn(bounds.box, 'clone').mockImplementationOnce(() => {
      expect(stage.content.getObjectByName('mannequin:rigged')).toBeDefined()
      dispose = vi.spyOn(stage.mannequin!, 'dispose')
      throw new Error('bounds failed')
    })
    mountParsedModel(stage, parsed, bounds, { template, map })
    expect(stage.content.getObjectByName('mannequin:rigged')).toBeUndefined()
    expect(dispose).toHaveBeenCalledTimes(1)
    expect(stage.mannequin!.meshes.length).toBeGreaterThan(0)
    expect(stage.mannequin!.meshes.every(m => !(m as THREE.SkinnedMesh).isSkinnedMesh)).toBe(true)
    expect(stage.playback!.duration).toBeCloseTo(1, 5)
    expect((actions.mock.instances.at(-1) as THREE.AnimationMixer).getRoot()).toBe(parsed.object)
    expect(actions.mock.calls.at(-1)![0]).toBe(parsed.clips[0])
    clearContent(stage)
  })
})

describe('buildRiggedMannequin frame-0 pose', () => {
  it('leaves the mannequin posed at frame 0 for framing and the first paint', async () => {
    const template = await parseMannequinTemplate(glb())
    const source = syntheticMixamo(template)
    const map = detectHumanoidRig(rigBonesOf(source))!
    const rigged = buildRiggedMannequin(template, source, map, clip())
    rigged.object.updateMatrixWorld(true)
    const before = worldOf(rigged.object.getObjectByName('pelvis')!)
    const mixer = new THREE.AnimationMixer(rigged.object)
    mixer.clipAction(rigged.clip!).play()
    mixer.setTime(0)
    rigged.object.updateMatrixWorld(true)
    expect(before.distanceTo(worldOf(rigged.object.getObjectByName('pelvis')!))).toBeLessThan(0.01)
  })
})
