import { describe, it, expect } from 'vitest'

import { detectHumanoidRig, normalizeBoneName, type RigBone } from '../renderer/components/model-viewer/humanoidRig'

/** A rig from `[name, parent]` pairs. */
const rig = (pairs: [string, string | null][]): RigBone[] => pairs.map(([name, parent]) => ({ name, parent }))

/** The Mixamo humanoid (no fingers), with `p` in front of every name. */
const mixamo = (p: string): RigBone[] => {
  const pairs: [string, string | null][] = [
    [`${p}Hips`, null], [`${p}Spine`, `${p}Hips`], [`${p}Spine1`, `${p}Spine`], [`${p}Spine2`, `${p}Spine1`],
    [`${p}Neck`, `${p}Spine2`], [`${p}Head`, `${p}Neck`], [`${p}HeadTop_End`, `${p}Head`],
  ]
  for (const side of ['Left', 'Right']) {
    pairs.push(
      [`${p}${side}Shoulder`, `${p}Spine2`], [`${p}${side}Arm`, `${p}${side}Shoulder`],
      [`${p}${side}ForeArm`, `${p}${side}Arm`], [`${p}${side}Hand`, `${p}${side}ForeArm`],
      [`${p}${side}HandIndex1`, `${p}${side}Hand`],
      [`${p}${side}UpLeg`, `${p}Hips`], [`${p}${side}Leg`, `${p}${side}UpLeg`],
      [`${p}${side}Foot`, `${p}${side}Leg`], [`${p}${side}ToeBase`, `${p}${side}Foot`],
    )
  }
  return rig(pairs)
}

/** Meshy's auto-rig (RedPlayer): no prefix, lower-case `neck`, spine numbered DOWN the back. */
const meshy = (): RigBone[] => {
  const pairs: [string, string | null][] = [
    ['Hips', null], ['Spine02', 'Hips'], ['Spine01', 'Spine02'], ['Spine', 'Spine01'],
    ['neck', 'Spine'], ['Head', 'neck'], ['head_end', 'Head'], ['headfront', 'Head'],
  ]
  for (const side of ['Left', 'Right']) {
    pairs.push(
      [`${side}Shoulder`, 'Spine'], [`${side}Arm`, `${side}Shoulder`], [`${side}ForeArm`, `${side}Arm`],
      [`${side}Hand`, `${side}ForeArm`], [`${side}UpLeg`, 'Hips'], [`${side}Leg`, `${side}UpLeg`],
      [`${side}Foot`, `${side}Leg`], [`${side}ToeBase`, `${side}Foot`],
    )
  }
  return rig(pairs)
}

/** The mannequin's own Unreal-style names. */
const unreal = (): RigBone[] => {
  const pairs: [string, string | null][] = [
    ['root', null], ['pelvis', 'root'], ['spine_01', 'pelvis'], ['spine_02', 'spine_01'], ['spine_03', 'spine_02'],
    ['neck_01', 'spine_03'], ['Head', 'neck_01'],
  ]
  for (const s of ['l', 'r']) {
    pairs.push(
      [`clavicle_${s}`, 'spine_03'], [`upperarm_${s}`, `clavicle_${s}`], [`lowerarm_${s}`, `upperarm_${s}`],
      [`hand_${s}`, `lowerarm_${s}`], [`thumb_01_${s}`, `hand_${s}`], [`thumb_02_${s}`, `thumb_01_${s}`],
      [`thigh_${s}`, 'pelvis'], [`calf_${s}`, `thigh_${s}`], [`foot_${s}`, `calf_${s}`], [`ball_${s}`, `foot_${s}`],
    )
  }
  return rig(pairs)
}

describe('normalizeBoneName', () => {
  it('strips Mixamo prefixes with or without the colon FBXLoader removes', () => {
    for (const name of ['mixamorig:LeftForeArm', 'mixamorig1:LeftForeArm', 'mixamorigLeftForeArm', 'mixamorig5LeftForeArm', 'LeftForeArm']) {
      expect(normalizeBoneName(name)).toBe('leftforearm')
    }
  })
})

describe('detectHumanoidRig', () => {
  it.each(['mixamorig:', 'mixamorig1:', 'mixamorig5', ''])('maps a Mixamo rig with prefix %j', prefix => {
    const map = detectHumanoidRig(mixamo(prefix))!
    expect(map).not.toBeNull()
    expect(map.pelvis).toBe(`${prefix}Hips`)
    expect([map.spine_01, map.spine_02, map.spine_03]).toEqual([`${prefix}Spine`, `${prefix}Spine1`, `${prefix}Spine2`])
    expect(map.upperarm_l).toBe(`${prefix}LeftArm`)
    expect(map.lowerarm_r).toBe(`${prefix}RightForeArm`)
    expect(map.thigh_l).toBe(`${prefix}LeftUpLeg`)
    expect(map.calf_r).toBe(`${prefix}RightLeg`)
    expect(map.ball_l).toBe(`${prefix}LeftToeBase`)
    expect(map.index_01_l).toBe(`${prefix}LeftHandIndex1`)
    // HeadTop_End is a tip, not a bone the mannequin has.
    expect(Object.values(map)).not.toContain(`${prefix}HeadTop_End`)
  })

  it('maps the mannequin onto itself', () => {
    const map = detectHumanoidRig(unreal())!
    expect(map).not.toBeNull()
    for (const [target, source] of Object.entries(map)) expect(source).toBe(target)
    expect(map.thumb_02_r).toBe('thumb_02_r')
  })

  it('orders the Meshy spine by hierarchy, not by its downward numbering', () => {
    const map = detectHumanoidRig(meshy())!
    expect(map).not.toBeNull()
    expect([map.spine_01, map.spine_02, map.spine_03]).toEqual(['Spine02', 'Spine01', 'Spine'])
    expect(map.neck_01).toBe('neck')
    expect(map.Head).toBe('Head')
    expect(map.clavicle_r).toBe('RightShoulder')
  })

  it('returns null for a non-humanoid rig', () => {
    expect(detectHumanoidRig(rig([['Hips', null], ['Spine', 'Hips']]))).toBeNull()
    expect(detectHumanoidRig(rig([['Root', null], ['Tail1', 'Root'], ['Tail2', 'Tail1'], ['Jaw', 'Root']]))).toBeNull()
  })

  it.each(['mixamorigLeftForeArm', 'mixamorigRightUpLeg', 'mixamorigHips'])('returns null when the core bone %s is missing', missing => {
    const bones = mixamo('mixamorig').filter(b => b.name !== missing)
    expect(detectHumanoidRig(bones)).toBeNull()
  })

  it('returns null with fewer than two spine bones', () => {
    const bones = mixamo('').filter(b => b.name !== 'Spine1' && b.name !== 'Spine2')
    expect(detectHumanoidRig(bones)).toBeNull()
  })

  it('returns null with neither a neck nor a head', () => {
    const bones = mixamo('').filter(b => b.name !== 'Neck' && b.name !== 'Head')
    expect(detectHumanoidRig(bones)).toBeNull()
  })
})
