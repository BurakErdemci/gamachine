import fs from 'fs'
import os from 'os'
import path from 'path'
import { pathToFileURL } from 'url'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import {
  GAMACHINE_DEFAULT_PORTS, gamachinePorts, isAllowedNavigation, isAllowedRequest, isLoopbackAddress, isLoopbackHost, isPrivateAddress, pageOriginOf,
  resolvesToPrivate, tempRoot,
} from '../main/helpers/browser-url-policy'

describe('agent browser navigation allow-list', () => {
  it('allows public http and https pages', () => {
    for (const url of ['https://example.com/', 'http://opengameart.org/content/x', 'https://assetstore.unity.com/?q=walk', 'https://8.8.8.8/'])
      expect(isAllowedNavigation(url)).toBe(true)
  })

  it('refuses other schemes, private hosts and garbage, and every loopback address while Gamachine\'s ports are unknown', () => {
    for (const url of ['file:///etc/passwd', 'javascript:alert(1)', 'data:text/html,hi', 'chrome://settings',
      'devtools://devtools', 'ftp://example.com/', 'not a url', '', 'http://127.0.0.1:8000/', 'http://127.1/',
      'http://0x7f000001/', 'http://localhost:3000/', 'http://LOCALHOST./', 'http://foo.localhost/', 'http://[::1]/',
      'http://[::ffff:127.0.0.1]/', 'http://[fe80::1]/', 'http://10.0.0.5/', 'http://172.16.3.4/', 'http://192.168.1.1/',
      'http://169.254.169.254/', 'http://0.0.0.0/'])
      expect(isAllowedNavigation(url)).toBe(false)
  })
})

describe('isPrivateAddress', () => {
  it('flags loopback, private, link-local, unspecified and their IPv4-mapped forms', () => {
    for (const ip of ['127.0.0.1', '127.255.0.9', '10.1.2.3', '172.16.0.1', '172.31.0.1', '192.168.0.10', '169.254.1.1',
      '0.0.0.0', '::1', '::', 'fc00::1', 'fd12::3', 'fe80::1', 'febf::1', '::ffff:10.0.0.1', '::ffff:7f00:1'])
      expect(isPrivateAddress(ip), ip).toBe(true)
  })

  it('flags the reserved IPv4 ranges and the IPv6 forms that embed a private IPv4 address', () => {
    for (const ip of ['100.64.0.1', '100.127.255.254', '192.0.0.8', '192.0.2.5', '198.18.0.1', '198.19.255.255', '198.51.100.7',
      '203.0.113.9', '224.0.0.1', '239.1.1.1', '240.0.0.1', '255.255.255.255', 'ff02::1', 'ff00::', '::7f00:1', '::a00:1',
      '64:ff9b::7f00:1', '64:ff9b::10.0.0.1', '2002:7f00:1::1', '2002:c0a8:101::1'])
      expect(isPrivateAddress(ip), ip).toBe(true)
  })

  it('leaves the neighbours of those ranges alone', () => {
    for (const ip of ['100.63.255.255', '100.128.0.1', '198.20.0.1', '192.0.1.1', '223.255.255.255', '2002:808:808::1',
      '64:ff9b::808:808', '::808:808'])
      expect(isPrivateAddress(ip), ip).toBe(false)
  })

  it('leaves public addresses and non-addresses alone', () => {
    for (const ip of ['8.8.8.8', '172.32.0.1', '172.15.255.255', '1.1.1.1', '2606:4700::1111', 'fec0::1', '::ffff:8.8.8.8', 'example.com'])
      expect(isPrivateAddress(ip), ip).toBe(false)
  })
})

describe('resolvesToPrivate', () => {
  it('is true when any resolved address is private', async () => {
    const lookup = vi.fn(async () => [{ address: '93.184.216.34', family: 4 }, { address: '10.0.0.7', family: 4 }])
    expect(await resolvesToPrivate('rebind.example', lookup)).toBe(true)
    expect(lookup).toHaveBeenCalledWith('rebind.example', { all: true })
  })

  it('is false when every address is public', async () => {
    expect(await resolvesToPrivate('example.com', async () => [{ address: '93.184.216.34', family: 4 }])).toBe(false)
  })

  it('fails closed on a lookup error and answers literals without a lookup', async () => {
    const lookup = vi.fn(async () => { throw new Error('ENOTFOUND') })
    expect(await resolvesToPrivate('nowhere.example', lookup)).toBe(true)
    expect(await resolvesToPrivate('127.0.0.1', lookup)).toBe(true)
    expect(await resolvesToPrivate('[::1]', lookup)).toBe(true)
    expect(await resolvesToPrivate('8.8.8.8', lookup)).toBe(false)
    expect(await resolvesToPrivate('localhost', lookup)).toBe(true)
    expect(lookup).toHaveBeenCalledTimes(1)
  })
})

describe('loopback pages', () => {
  const ctx = { ports: gamachinePorts([54321, 41234]) }
  const FREE = 53917

  it('opens on a free port, whatever the spelling of the address', () => {
    for (const url of [`http://127.0.0.1:${FREE}/index.html`, `http://localhost:${FREE}/`, `https://LOCALHOST.:${FREE}/`, `http://app.localhost:${FREE}/`,
      `http://127.5.6.7:${FREE}/`, `http://[::1]:${FREE}/`, `http://[::ffff:127.0.0.1]:${FREE}/`, `http://127.1:${FREE}/`, `http://2130706433:${FREE}/`,
      `http://0x7f.0.0.1:${FREE}/`, 'http://localhost/', 'https://127.0.0.1/'])
      expect(isAllowedNavigation(url, ctx), url).toBe(true)
  })

  it('is refused on every Gamachine port, written any way', () => {
    for (const port of [...GAMACHINE_DEFAULT_PORTS, 54321, 41234])
      for (const host of ['127.0.0.1', 'localhost', 'x.localhost', '[::1]', '[::ffff:127.0.0.1]', '127.1', '2130706433', '0x7f.0.0.1', '0177.0.0.1'])
        expect(isAllowedNavigation(`http://${host}:${port}/`, ctx), `${host}:${port}`).toBe(false)
    expect(GAMACHINE_DEFAULT_PORTS).toEqual(expect.arrayContaining([8000, 8080]))
  })

  it('is refused whole while a port the host needs is unknown', () => {
    expect(isAllowedNavigation(`http://127.0.0.1:${FREE}/`)).toBe(false)
    expect(isAllowedNavigation(`http://127.0.0.1:${FREE}/`, { ports: null })).toBe(false)
    expect(isAllowedNavigation('https://example.com/', { ports: null })).toBe(true)
  })

  it('keeps private, link-local, metadata, CGNAT and reserved addresses refused', () => {
    for (const url of ['http://10.0.0.1/', 'http://172.16.0.1/', 'http://192.168.1.10/', 'http://169.254.169.254/latest/meta-data/', 'http://169.254.169.254:8080/',
      'http://100.64.0.1/', 'http://[fd00::1]/', 'http://[fe80::1]/', 'http://0.0.0.0/', 'http://224.0.0.1/', 'http://240.0.0.1/'])
      expect(isAllowedNavigation(url, ctx), url).toBe(false)
  })

  it('tells loopback hosts and addresses apart from the rest', () => {
    for (const host of ['localhost', 'a.localhost', 'LOCALHOST.', '127.0.0.1', '127.9.9.9', '[::1]', '::1', '::ffff:127.0.0.1']) expect(isLoopbackHost(host), host).toBe(true)
    for (const host of ['example.com', '10.0.0.1', '0.0.0.0', 'localhost.example.com', '8.8.8.8', '::ffff:10.0.0.1']) expect(isLoopbackHost(host), host).toBe(false)
    expect(isLoopbackAddress('127.0.0.1') && isLoopbackAddress('::1') && isLoopbackAddress('::ffff:7f00:1')).toBe(true)
    expect(isLoopbackAddress('10.0.0.1') || isLoopbackAddress('::') || isLoopbackAddress('example.com')).toBe(false)
  })

  it('names the kind of page a URL is', () => {
    expect(pageOriginOf('http://localhost:3000/')).toBe('loopback')
    expect(pageOriginOf('https://example.com/')).toBe('public')
    expect(pageOriginOf('file:///C:/x.html')).toBe('file')
    expect(pageOriginOf('not a url')).toBe('public')
  })
})

describe('request origin rule', () => {
  const ctx = { ports: gamachinePorts([54321]) }

  it('lets a public page reach nothing local or private', () => {
    for (const url of ['http://127.0.0.1:53917/x.js', 'http://localhost:53917/', 'ws://localhost:53917/', 'http://[::1]:53917/', 'http://10.0.0.1/', 'http://192.168.1.1/',
      'http://169.254.169.254/', 'file:///C:/Windows/win.ini'])
      expect(isAllowedRequest(url, 'public', ctx), url).toBe(false)
    expect(isAllowedRequest('https://cdn.example/a.png', 'public', ctx)).toBe(true)
  })

  it('lets a loopback page reach loopback and public hosts, not Gamachine ports, not the private network, not files', () => {
    expect(isAllowedRequest('http://127.0.0.1:53917/app.js', 'loopback', ctx)).toBe(true)
    expect(isAllowedRequest('ws://localhost:53917/socket', 'loopback', ctx)).toBe(true)
    expect(isAllowedRequest('https://cdn.example/a.png', 'loopback', ctx)).toBe(true)
    for (const url of ['http://127.0.0.1:8080/mcp', 'http://localhost:54321/', 'ws://localhost:8000/', 'http://192.168.1.1/', 'http://169.254.169.254/', 'file:///C:/x.html'])
      expect(isAllowedRequest(url, 'loopback', ctx), url).toBe(false)
  })

  it('keeps a file: page to files, with no http(s), ws(s) or loopback request', () => {
    const roots = [fs.realpathSync.native(os.tmpdir())]
    const here = pathToFileURL(path.join(roots[0], 'page.html')).toString()
    expect(isAllowedRequest(here, 'file', { ...ctx, fileRoots: roots })).toBe(true)
    for (const url of ['http://example.com/a.js', 'https://example.com/', 'ws://example.com/', 'http://127.0.0.1:53917/', 'http://localhost:53917/'])
      expect(isAllowedRequest(url, 'file', { ...ctx, fileRoots: roots }), url).toBe(false)
    expect(isAllowedRequest(here, 'public', { ...ctx, fileRoots: roots })).toBe(false)
    expect(isAllowedRequest(here, 'loopback', { ...ctx, fileRoots: roots })).toBe(false)
    expect(isAllowedRequest(here, 'file', ctx)).toBe(false)
  })
})

describe('file: pages', () => {
  const sep = String.fromCharCode(92)
  let base: string
  let workspace: string
  let outside: string
  beforeAll(() => {
    base = fs.mkdtempSync(path.join(os.tmpdir(), 'gm-file-policy-'))
    workspace = path.join(base, 'workspace')
    outside = path.join(base, 'other')
    fs.mkdirSync(path.join(workspace, 'sub dir'), { recursive: true })
    fs.mkdirSync(outside)
    fs.writeFileSync(path.join(workspace, 'index.html'), '<p>hi</p>')
    fs.writeFileSync(path.join(workspace, 'sub dir', 'a b.png'), 'x')
    fs.writeFileSync(path.join(outside, 'secret.html'), 'x')
    fs.mkdirSync(path.join(base, 'workspace-evil'))
  })
  afterAll(() => { fs.rmSync(base, { recursive: true, force: true }) })
  const ctxFor = (...roots: string[]) => ({ ports: gamachinePorts(), fileRoots: roots })
  const url = (p: string) => pathToFileURL(p).toString()

  it('opens inside the workspace and the OS temp dir', () => {
    const ctx = ctxFor(workspace, tempRoot())
    for (const target of [path.join(workspace, 'index.html'), path.join(workspace, 'sub dir', 'a b.png'), path.join(workspace, 'not-yet.html'), workspace,
      path.join(os.tmpdir(), 'gamachine-shot.png')])
      expect(isAllowedNavigation(url(target), ctx), target).toBe(true)
    expect(isAllowedNavigation(`${url(path.join(workspace, 'index.html'))}?x=1#top`, ctx)).toBe(true)
  })

  it('is refused with no roots and outside them', () => {
    expect(isAllowedNavigation(url(path.join(workspace, 'index.html')))).toBe(false)
    expect(isAllowedNavigation(url(path.join(workspace, 'index.html')), ctxFor())).toBe(false)
    expect(isAllowedNavigation(url(path.join(outside, 'secret.html')), ctxFor(workspace))).toBe(false)
    expect(isAllowedNavigation(url(path.join(base, 'workspace-evil', 'x.html')), ctxFor(workspace))).toBe(false)
    expect(isAllowedNavigation('file:///', ctxFor(workspace))).toBe(false)
    expect(isAllowedNavigation(url(process.execPath), ctxFor(workspace))).toBe(false)
  })

  it('is refused for climbing, hidden separators, streams, UNC, devices and trailing dots', () => {
    const root = url(workspace)
    const bad = [`${root}/../other/secret.html`, `${root}/%2e%2e/other/secret.html`, `${root}/..%2fother/secret.html`, `${root}/..%5cother`, `${root}/a%00.html`,
      `${root}/index.html:stream`, `${root}/index.html::$DATA`, `${root}/trailing./x`, `${root}/trailing%20/x`, `${root}/CON`, `${root}/nul.txt`,
      'file://server/share/x.html', 'file:////server/share/x.html', `file:///${sep}${sep}server${sep}share`, `file:///${sep}${sep}?${sep}C:${sep}x`]
    for (const target of bad) expect(isAllowedNavigation(target, ctxFor(workspace)), target).toBe(false)
  })

  it('is refused for a link that points out of the workspace', () => {
    const link = path.join(workspace, 'link')
    try { fs.symlinkSync(outside, link, 'junction') } catch { return }
    expect(isAllowedNavigation(url(path.join(link, 'secret.html')), ctxFor(workspace))).toBe(false)
    expect(isAllowedNavigation(url(path.join(workspace, 'index.html')), ctxFor(workspace))).toBe(true)
  })
})

describe('a public name that resolves to loopback', () => {
  it('is still private (rebinding)', async () => {
    expect(await resolvesToPrivate('rebind.example', async () => [{ address: '127.0.0.1', family: 4 }])).toBe(true)
    expect(await resolvesToPrivate('rebind.example', async () => [{ address: '::1', family: 6 }])).toBe(true)
    expect(await resolvesToPrivate('rebind.example', async () => [{ address: '93.184.216.34', family: 4 }, { address: '127.0.0.1', family: 4 }])).toBe(true)
  })
})
