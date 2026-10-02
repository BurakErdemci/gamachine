import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import React from 'react'
import { cleanup, render, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'

const mocks = vi.hoisted(() => ({ parse: vi.fn(), scene: null as unknown }))
vi.mock('../renderer/components/model-viewer/loaders', async importOriginal => {
  const actual = await importOriginal<typeof import('../renderer/components/model-viewer/loaders')>()
  return { ...actual, parseModel: mocks.parse }
})
vi.mock('three', async importOriginal => {
  const actual = await importOriginal<typeof import('three')>()
  class FakeWebGLRenderer {
    domElement = document.createElement('canvas')
    setPixelRatio() {}
    setSize() {}
    render(scene: unknown) { mocks.scene = scene }
    forceContextLoss() {}
    dispose() {}
  }
  return { ...actual, WebGLRenderer: FakeWebGLRenderer }
})

const glb = () => {
  const bytes = readFileSync(resolve(__dirname, '../renderer/public/models/mannequin.glb'))
  const data = new ArrayBuffer(bytes.byteLength)
  new Uint8Array(data).set(bytes)
  return data
}

beforeEach(() => { vi.resetModules(); mocks.parse.mockReset(); mocks.scene = null })
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('loadMannequinTemplate cache', () => {
  it('shares one in-flight fetch and caches the successfully parsed object', async () => {
    let resolveFetch!: (response: Response) => void
    const fetch = vi.fn(() => new Promise<Response>(resolve => { resolveFetch = resolve }))
    vi.stubGlobal('fetch', fetch)
    const { loadMannequinTemplate, MANNEQUIN_URL } = await import('../renderer/components/model-viewer/riggedMannequin')
    const first = loadMannequinTemplate()
    const second = loadMannequinTemplate()
    expect(fetch).toHaveBeenCalledExactlyOnceWith(MANNEQUIN_URL)
    resolveFetch({ ok: true, arrayBuffer: async () => glb() } as Response)
    const [a, b] = await Promise.all([first, second])
    expect(a).not.toBeNull()
    expect(a).toBe(b)
    expect(await loadMannequinTemplate()).toBe(a)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it.each(['http', 'network', 'parse'])('does not cache a %s failure and fetches again', async failure => {
    const fetch = vi.fn()
    if (failure === 'network') fetch.mockRejectedValueOnce(new Error('offline'))
    else fetch.mockResolvedValueOnce({ ok: failure === 'parse', status: 500, arrayBuffer: async () => new ArrayBuffer(0) })
    fetch.mockResolvedValue({ ok: true, arrayBuffer: async () => glb() })
    vi.stubGlobal('fetch', fetch)
    const { loadMannequinTemplate } = await import('../renderer/components/model-viewer/riggedMannequin')
    expect(await loadMannequinTemplate()).toBeNull()
    expect(await loadMannequinTemplate()).not.toBeNull()
    expect(fetch).toHaveBeenCalledTimes(2)
  })
})

describe('panel mannequin fetch gate', () => {
  it.each(['non-humanoid', 'humanoid'])('fetches the template only for a humanoid rig: %s', async kind => {
    const { ModelPreviewPanel, humanoidMapFor } = await import('../renderer/components/model-viewer/ModelPreviewPanel')
    const object = new THREE.Group()
    const addBone = (name: string, parent: THREE.Object3D) => {
      const bone = new THREE.Bone()
      bone.name = name
      bone.position.y = 1
      parent.add(bone)
      return bone
    }
    if (kind === 'humanoid') {
      const hips = addBone('Hips', object)
      const spine = addBone('Spine', hips)
      const chest = addBone('Spine1', spine)
      addBone('Head', chest)
      for (const side of ['Left', 'Right']) {
        addBone(`${side}ForeArm`, addBone(`${side}Arm`, chest))
        addBone(`${side}Leg`, addBone(`${side}UpLeg`, hips))
      }
    } else addBone('tip', addBone('branch', object))
    object.updateMatrixWorld(true)
    const parsed = { object, clips: [] }
    expect(humanoidMapFor(parsed) !== null).toBe(kind === 'humanoid')
    mocks.parse.mockResolvedValue(parsed)
    const invoke = vi.fn().mockResolvedValue({ data: new ArrayBuffer(0) })
    vi.stubGlobal('ipc', { invoke })
    const fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 })
    vi.stubGlobal('fetch', fetch)
    render(React.createElement(ModelPreviewPanel, {
      file: { path: 'rig.fbx', name: 'rig.fbx' }, workspacePath: 'project',
    }))
    await waitFor(() => expect(object.parent).not.toBeNull())
    expect((mocks.scene as THREE.Scene).getObjectById(object.id)).toBe(object)
    expect(fetch).toHaveBeenCalledTimes(kind === 'humanoid' ? 1 : 0)
    expect(mocks.parse).toHaveBeenCalledTimes(1)
  })
})
