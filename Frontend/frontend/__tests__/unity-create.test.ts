import { EventEmitter } from 'events'
import fs from 'fs'
import os from 'os'
import path from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CreateDeps, CreateProgress, cancelCreate, createProject } from '../main/helpers/unity-create'

class FakeChild extends EventEmitter {
  killed = false
  kill() { this.killed = true; this.emit('exit', null, 'SIGTERM'); return true }
}

function setup(over: Partial<CreateDeps> = {}, platform: NodeJS.Platform = 'darwin') {
  const child = new FakeChild()
  const existing = new Set<string>(['/parent'])
  const deps: CreateDeps = {
    platform,
    exists: p => existing.has(p),
    realpath: p => {
      if (existing.has(p) || p.startsWith('/parent/')) return p
      throw new Error('ENOENT')
    },
    removeDir: vi.fn(),
    spawn: vi.fn(() => child),
    readLogTail: () => 'log line',
    isProject: () => true,
    registerTrusted: vi.fn(),
    logFile: () => '/tmp/create.log',
    now: () => Date.now(),
    intervalMs: 1000,
    ...over,
  }
  const events: CreateProgress[] = []
  const opts = { editorPath: '/Unity', parentDir: '/parent', name: 'My Game', onProgress: (p: CreateProgress) => events.push(p) }
  return { child, deps, events, opts, existing }
}

describe('createProject', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it.each([
    ['', 'empty name'],
    ['bad/name', 'slash'],
    ['..', 'dots'],
    ['a'.repeat(65), 'too long'],
    [' lead', 'leading space'],
  ])('rejects the invalid name %j (%s)', async name => {
    const { deps, opts } = setup()
    const result = await createProject({ ...opts, name }, deps)
    expect(result.ok).toBe(false)
    expect(deps.spawn).not.toHaveBeenCalled()
  })

  it('rejects a missing parent and an existing target', async () => {
    const a = setup()
    expect((await createProject({ ...a.opts, parentDir: '/nope' }, a.deps)).ok).toBe(false)
    const b = setup()
    b.existing.add('/parent/My Game')
    expect((await createProject(b.opts, b.deps)).ok).toBe(false)
    expect(a.deps.spawn).not.toHaveBeenCalled()
    expect(b.deps.spawn).not.toHaveBeenCalled()
  })

  it('refuses a Windows path longer than 150 characters', async () => {
    const parent = 'C:\\' + 'a'.repeat(140)
    const { deps, opts, existing } = setup({}, 'win32')
    existing.add(parent)
    const result = await createProject({ ...opts, parentDir: parent }, deps)
    expect(result).toMatchObject({ ok: false })
    expect((result as { error: string }).error).toContain('too long')
    expect(deps.spawn).not.toHaveBeenCalled()
  })

  it('spawns without a shell, with template args, streams progress and registers trust on success', async () => {
    const { child, deps, events, opts } = setup()
    const promise = createProject({ ...opts, templatePath: '/t/template.tgz' }, deps)
    expect(deps.spawn).toHaveBeenCalledExactlyOnceWith('/Unity', [
      '-batchmode', '-quit', '-createProject', '/parent/My Game',
      '-cloneFromTemplate', '/t/template.tgz', '-logFile', '/tmp/create.log',
    ])
    vi.advanceTimersByTime(2100)
    child.emit('exit', 0, null)
    await expect(promise).resolves.toEqual({ ok: true, path: '/parent/My Game' })
    expect(events.map(e => e.phase)).toEqual(['starting', 'running', 'running', 'done'])
    expect(events[1].logTail).toBe('log line')
    expect(deps.registerTrusted).toHaveBeenCalledExactlyOnceWith('/parent/My Game')
  })

  it('omits -cloneFromTemplate without a template', async () => {
    const { child, deps, opts } = setup()
    const promise = createProject(opts, deps)
    expect((deps.spawn as any).mock.calls[0][1]).not.toContain('-cloneFromTemplate')
    child.emit('exit', 0, null)
    await promise
  })

  it('fails without registering trust on a non-zero exit or a missing project', async () => {
    const a = setup()
    const pa = createProject(a.opts, a.deps)
    a.child.emit('exit', 1, null)
    expect(await pa).toMatchObject({ ok: false })
    expect(a.events.at(-1)?.phase).toBe('failed')
    const b = setup({ isProject: () => false })
    const pb = createProject(b.opts, b.deps)
    b.child.emit('exit', 0, null)
    expect(await pb).toMatchObject({ ok: false })
    expect(a.deps.registerTrusted).not.toHaveBeenCalled()
    expect(b.deps.registerTrusted).not.toHaveBeenCalled()
  })

  it('fails when the process cannot start', async () => {
    const { child, deps, opts } = setup()
    const promise = createProject(opts, deps)
    child.emit('error', new Error('ENOENT'))
    expect(await promise).toMatchObject({ ok: false })
    expect(deps.registerTrusted).not.toHaveBeenCalled()
  })

  it('allows one creation at a time and cancel kills only that child', async () => {
    const { child, deps, events, opts } = setup()
    const promise = createProject(opts, deps)
    const second = setup()
    expect(await createProject(second.opts, second.deps)).toMatchObject({ ok: false })
    expect(second.deps.spawn).not.toHaveBeenCalled()
    expect(cancelCreate()).toEqual({ cancelled: true })
    expect(child.killed).toBe(true)
    expect(await promise).toEqual({ ok: false, error: 'cancelled' })
    expect(events.at(-1)?.phase).toBe('failed')
    expect(deps.registerTrusted).not.toHaveBeenCalled()
    expect(cancelCreate()).toEqual({ cancelled: false })
    // The slot is free again.
    const again = setup()
    const p2 = createProject(again.opts, again.deps)
    again.child.emit('exit', 0, null)
    expect(await p2).toMatchObject({ ok: true })
  })

  it('keeps a finished project when a cancel races a successful exit', async () => {
    const { child, deps, opts } = setup()
    child.kill = () => true // The kill arrives too late to stop a process that already finished.
    const promise = createProject(opts, deps)
    expect(cancelCreate()).toEqual({ cancelled: true })
    child.emit('exit', 0, null)
    expect(await promise).toEqual({ ok: true, path: '/parent/My Game' })
    expect(deps.removeDir).not.toHaveBeenCalled()
    expect(deps.registerTrusted).toHaveBeenCalledExactlyOnceWith('/parent/My Game')
  })

  it('does not claim a cancel once the child has already exited', async () => {
    const { child, deps, opts } = setup()
    const promise = createProject(opts, deps)
    ;(child as any).exitCode = 0
    expect(cancelCreate()).toEqual({ cancelled: false })
    child.emit('exit', 0, null)
    expect(await promise).toMatchObject({ ok: true })
  })

  it('removes the half-made folder after a cancel or a non-zero exit', async () => {
    const a = setup()
    const pa = createProject(a.opts, a.deps)
    cancelCreate()
    expect(await pa).toEqual({ ok: false, error: 'cancelled' })
    expect(a.deps.removeDir).toHaveBeenCalledExactlyOnceWith('/parent/My Game')
    const b = setup()
    const pb = createProject(b.opts, b.deps)
    b.child.emit('exit', 1, null)
    await pb
    expect(b.deps.removeDir).toHaveBeenCalledExactlyOnceWith('/parent/My Game')
  })

  it('does not remove anything on success or when validation rejects an existing target', async () => {
    const a = setup()
    const pa = createProject(a.opts, a.deps)
    a.child.emit('exit', 0, null)
    expect(await pa).toMatchObject({ ok: true })
    expect(a.deps.removeDir).not.toHaveBeenCalled()
    const b = setup()
    b.existing.add('/parent/My Game')
    expect((await createProject(b.opts, b.deps)).ok).toBe(false)
    expect(b.deps.removeDir).not.toHaveBeenCalled()
  })

  it('keeps the original error and appends a note when the cleanup throws', async () => {
    const { child, deps, opts } = setup({ removeDir: () => { throw new Error('EBUSY') } })
    const promise = createProject(opts, deps)
    child.emit('exit', 3, null)
    expect(await promise).toEqual({
      ok: false,
      error: 'Unity exited with code 3. The partial folder could not be removed: /parent/My Game',
    })
  })

  it('leaves another folder intact when the parent symlink is re-pointed during the run', async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'gm-create-'))
    try {
      const realA = path.join(root, 'a')
      const realB = path.join(root, 'b')
      fs.mkdirSync(path.join(realA, 'Game'), { recursive: true })
      fs.mkdirSync(path.join(realB, 'Game'), { recursive: true })
      fs.writeFileSync(path.join(realB, 'Game', 'keep.txt'), 'x')
      const link = path.join(root, 'link')
      fs.symlinkSync(realA, link)
      fs.rmSync(path.join(realA, 'Game'), { recursive: true })
      const { child, deps, opts } = setup({
        exists: fs.existsSync,
        realpath: fs.realpathSync,
        removeDir: dir => fs.rmSync(dir, { recursive: true, force: true }),
      })
      const promise = createProject({ ...opts, parentDir: link, name: 'Game' }, deps)
      fs.mkdirSync(path.join(realA, 'Game'))
      fs.unlinkSync(link)
      fs.symlinkSync(realB, link)
      child.emit('exit', 1, null)
      expect(await promise).toEqual({ ok: false, error: 'Unity exited with code 1.' })
      expect(fs.existsSync(path.join(realB, 'Game', 'keep.txt'))).toBe(true)
    } finally {
      fs.rmSync(root, { recursive: true, force: true })
    }
  })

  it('builds the target from the resolved parent and removes it on a normal failure', async () => {
    const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'gm-create-')))
    try {
      const { child, deps, opts } = setup({
        exists: fs.existsSync,
        realpath: fs.realpathSync,
        removeDir: dir => fs.rmSync(dir, { recursive: true, force: true }),
      })
      const promise = createProject({ ...opts, parentDir: root, name: 'Game' }, deps)
      fs.mkdirSync(path.join(root, 'Game'))
      child.emit('exit', 1, null)
      expect(await promise).toEqual({ ok: false, error: 'Unity exited with code 1.' })
      expect(fs.existsSync(path.join(root, 'Game'))).toBe(false)
    } finally {
      fs.rmSync(root, { recursive: true, force: true })
    }
  })
})
