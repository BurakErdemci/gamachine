import { isIP } from 'net'
import { promises as dns } from 'dns'
import { fileURLToPath } from 'url'
import fs from 'fs'
import os from 'os'
import path from 'path'

export type LookupAll = (hostname: string, options: { all: true }) => Promise<{ address: string; family: number }[]>

function ipv4Octets(ip: string): number[] | null {
  if (isIP(ip) !== 4) return null
  return ip.split('.').map(Number)
}

function isPrivateIpv4(o: number[]): boolean {
  const [a, b, c] = o
  return a === 0 || a === 10 || a === 127 || (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) ||
    (a === 100 && b >= 64 && b <= 127) || // carrier-grade NAT
    (a === 192 && b === 0 && (c === 0 || c === 2)) || // IETF protocol assignments, TEST-NET-1
    (a === 198 && (b === 18 || b === 19)) || // benchmarking
    (a === 198 && b === 51 && c === 100) || (a === 203 && b === 0 && c === 113) || // TEST-NET-2/3
    a >= 224 // multicast, reserved and broadcast
}

/** Expands an IPv6 literal to its eight 16-bit groups; an embedded dotted quad becomes the last two. */
function ipv6Groups(ip: string): number[] | null {
  if (isIP(ip) !== 6) return null
  let text = ip.split('%')[0]
  const dotted = text.match(/(\d+\.\d+\.\d+\.\d+)$/)
  if (dotted) {
    const o = dotted[1].split('.').map(Number)
    text = text.slice(0, -dotted[1].length) + `${((o[0] << 8) | o[1]).toString(16)}:${((o[2] << 8) | o[3]).toString(16)}`
  }
  const [head, tail] = text.split('::')
  const part = (s: string | undefined) => (s ? s.split(':').map((g) => parseInt(g, 16)) : [])
  const left = part(head)
  const right = part(tail)
  const fill = tail === undefined ? [] : new Array(8 - left.length - right.length).fill(0)
  const groups = [...left, ...fill, ...right]
  return groups.length === 8 && groups.every((g) => Number.isInteger(g) && g >= 0 && g <= 0xffff) ? groups : null
}

/** True for loopback, private, link-local, reserved, multicast and unspecified addresses (IPv4, IPv6 and the IPv6 forms that embed an IPv4 address). */
export function isPrivateAddress(ip: string): boolean {
  const v4 = ipv4Octets(ip)
  if (v4) return isPrivateIpv4(v4)
  const g = ipv6Groups(ip)
  if (!g) return false
  const embedded = (hi: number, lo: number) => isPrivateIpv4([hi >> 8, hi & 0xff, lo >> 8, lo & 0xff])
  if (g.slice(0, 7).every((x) => x === 0) && (g[7] === 0 || g[7] === 1)) return true
  if ((g[0] & 0xfe00) === 0xfc00 || (g[0] & 0xffc0) === 0xfe80 || (g[0] & 0xff00) === 0xff00) return true
  if (g.slice(0, 5).every((x) => x === 0) && g[5] === 0xffff) return embedded(g[6], g[7])
  if (g.slice(0, 6).every((x) => x === 0)) return embedded(g[6], g[7]) // IPv4-compatible ::/96
  if (g[0] === 0x64 && g[1] === 0xff9b && g.slice(2, 6).every((x) => x === 0)) return embedded(g[6], g[7]) // NAT64
  if (g[0] === 0x2002) return embedded(g[1], g[2]) // 6to4
  // Only global unicast (2000::/3) is public, minus the protocol-assignment, documentation and benchmarking blocks;
  // everything else is reserved or site-local, as in the backend's copy of the rule.
  if ((g[0] & 0xe000) !== 0x2000) return true
  return (g[0] === 0x2001 && (g[1] < 0x0200 || g[1] === 0x0db8)) || (g[0] === 0x3fff && g[1] < 0x1000)
}

/**
 * IPv6 forms that carry an IPv4 address (mapped, compatible, NAT64 64:ff9b::/96, 6to4 2002::/16, Teredo 2001::/32):
 * refused as a URL host whatever they carry, as in the backend's copy; loopback is judged before this.
 */
function embedsIpv4(ip: string): boolean {
  const g = ipv6Groups(ip)
  if (!g) return false
  const zero = (from: number, to: number) => g.slice(from, to).every((x) => x === 0)
  return (zero(0, 5) && (g[5] === 0xffff || g[5] === 0)) || (g[0] === 0x64 && g[1] === 0xff9b && zero(2, 6)) ||
    g[0] === 0x2002 || (g[0] === 0x2001 && g[1] === 0)
}

function hostOf(url: URL): string {
  return url.hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase()
}

/**
 * Ports of Gamachine's own local listeners: a page in the agent browser must never reach them (the backend and
 * the browser host carry tokens, the Unity MCP server drives the editor). Backend/app/browser_use/policy.py keeps
 * the same list; change both together.
 */
export const GAMACHINE_DEFAULT_PORTS: readonly number[] = [
  8000, // backend HTTP: Backend main.py PORT default, docker-compose.yml, DOCKER_BACKEND_PORT default in background.ts
  8080, // Unity MCP HTTP server: unity_mcp_manager.mcp_port, mcp_port_guard.DEFAULT_MCP_PORT
  ...Array.from({ length: 100 }, (_, i) => 6400 + i), // Unity editor bridge: port_discovery.py DEFAULT_PORT, PortManager.cs falls back DefaultPort+1..+99
  9876, // Blender connector bridge: Backend connectors/views.py BRIDGE_PORTS
  8799, // relay dev server: relay/package.json "wrangler dev --port 8799"
  8888, // nextron renderer dev server (background.ts loads it in dev, port in process.argv[2])
  11434, // Ollama, the local model provider Gamachine calls (Backend agentic/chat_model.py); its API can delete models
]

/**
 * The defaults plus the ports this process knows at run time (`known`: the backend's port, the browser
 * command server's port, the dev renderer's port; a missing or invalid value adds nothing).
 * Callers pass null as the context's ports when a runtime value they need is unavailable: loopback is then refused outright.
 */
export function gamachinePorts(known: Iterable<number | null | undefined> = []): Set<number> {
  const ports = new Set<number>(GAMACHINE_DEFAULT_PORTS)
  for (const port of known) if (typeof port === 'number' && Number.isInteger(port) && port > 0 && port < 65536) ports.add(port)
  return ports
}

export type PageOrigin = 'public' | 'loopback' | 'file'

export interface NavigationContext {
  /** Gamachine's own ports; null when they are not known, which refuses every loopback address. */
  ports: ReadonlySet<number> | null
  /** The directories a file: page may be read from; none (or empty) refuses every file: address. */
  fileRoots?: readonly string[] | null
}

const FAIL_CLOSED: NavigationContext = { ports: null }

/** Loopback addresses only: 127.0.0.0/8, ::1 and their IPv4-mapped forms. */
export function isLoopbackAddress(ip: string): boolean {
  const v4 = ipv4Octets(ip)
  if (v4) return v4[0] === 127
  const g = ipv6Groups(ip)
  if (!g) return false
  if (g.slice(0, 7).every((x) => x === 0) && g[7] === 1) return true
  return g.slice(0, 5).every((x) => x === 0) && g[5] === 0xffff && (g[6] >> 8) === 127
}

/** localhost, *.localhost and loopback IP literals (URL parsing has already normalised legacy spellings such as 2130706433). */
export function isLoopbackHost(hostname: string): boolean {
  const host = hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase()
  if (host === 'localhost' || host.endsWith('.localhost')) return true
  return isIP(host) !== 0 && isLoopbackAddress(host)
}

function portOf(url: URL): number {
  if (url.port) return Number(url.port)
  return url.protocol === 'https:' || url.protocol === 'wss:' ? 443 : 80
}

/** The kind of page a URL is: file:, a loopback address, or anything else. */
export function pageOriginOf(url: string): PageOrigin {
  try {
    const parsed = new URL(url)
    if (parsed.protocol === 'file:') return 'file'
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:') && isLoopbackHost(parsed.hostname) ? 'loopback' : 'public'
  } catch {
    return 'public'
  }
}

/** The path with its links resolved; a path that does not exist yet is resolved through its nearest existing parent. */
function realPathLoose(target: string): string {
  const rest: string[] = []
  let current = path.resolve(target)
  for (;;) {
    try {
      return path.join(fs.realpathSync.native(current), ...rest.reverse())
    } catch {
      const parent = path.dirname(current)
      if (parent === current) return path.resolve(target)
      rest.push(path.basename(current))
      current = parent
    }
  }
}

function insideDir(target: string, root: string): boolean {
  const relative = path.relative(root, target)
  return relative === '' || (relative.split(path.sep)[0] !== '..' && !path.isAbsolute(relative))
}

/**
 * Spellings a browser reads differently from the backend's parser, refused in both copies: control characters or
 * surrounding whitespace, no slashes after the scheme, backslashes, a percent-encoded or non-ASCII host, port 0,
 * and a file: address that is not file:///path, file://localhost/path or file:/path.
 */
function plainAddress(url: string, parsed: URL): boolean {
  if (/[\x00-\x1f\x7f]/.test(url) || url !== url.trim() || url.includes('\\')) return false
  if (parsed.protocol === 'file:') return /^file:(\/\/(localhost)?\/(?!\/)|\/(?!\/))/i.test(url)
  const authority = /^[a-z][a-z0-9+.-]*:\/\/([^/?#]*)/i.exec(url)
  return authority !== null && authority[1] !== '' && !authority[1].includes('%') && !/[^\x00-\x7f]/.test(authority[1]) && parsed.port !== '0'
}

const RESERVED_FILE_NAMES = /^(con|prn|aux|nul|conin\$|conout\$|com[1-9]|lpt[1-9])$/i

/** True when a file: URL names a plain local path inside one of `roots` (links, UNC, device and stream names refused). */
export function isFileInRoots(url: string, roots: readonly string[] | null | undefined): boolean {
  if (!roots || roots.length === 0) return false
  let target: string
  try {
    const parsed = new URL(url)
    if (parsed.protocol !== 'file:' || parsed.hostname !== '' || !plainAddress(url, parsed)) return false
    target = fileURLToPath(parsed)
  } catch {
    return false
  }
  const win = process.platform === 'win32'
  // WHATWG reads "C|" as a drive letter; the backend does not.
  if (win && /^file:(\/\/(localhost)?)?\/[A-Za-z]\|/i.test(url)) return false
  if (/[\x00-\x1f\x7f]/.test(target) || target.startsWith('\\\\') || target.startsWith('//')) return false
  if (win && !/^[A-Za-z]:[\\/]/.test(target)) return false
  const rest = win ? target.slice(2) : target
  if (rest.includes(':')) return false
  const names = rest.split(/[\\/]/).filter((name) => name !== '' && name !== '.' && name !== '..')
  if (names.some((name) => /[. ]$/.test(name) || RESERVED_FILE_NAMES.test(name.split('.')[0]))) return false
  const real = realPathLoose(target)
  const inside = (root: string) => typeof root === 'string' && root !== '' && insideDir(real, realPathLoose(root))
  if (!roots.some(inside)) return false
  // Inside the temp folder alone, Gamachine's own files (gamachine.log, its temp folders) stay closed.
  const temp = tempRoot()
  if (roots.some((root) => inside(root) && realPathLoose(root) !== temp)) return true
  return !path.relative(temp, real).split(path.sep).some((name) => name.toLowerCase().startsWith('gamachine'))
}

/** The OS temp folder, one of the roots a file: page may always read from. */
export function tempRoot(): string {
  return realPathLoose(os.tmpdir())
}

/**
 * Static check on one request or navigation made while the page shown is of kind `origin` (the host's own
 * navigations count as 'loopback': the most permissive origin that may still reach public pages):
 * - file: only from a file: page, and only inside the roots;
 * - a file: page loads nothing over http(s), ws(s) or from loopback;
 * - loopback only from a loopback page, and never a Gamachine port (or any port while those are unknown);
 * - private, link-local, CGNAT, unspecified, multicast and reserved IP literals never;
 * - everything else (public names and addresses) is judged by the caller's DNS check as well.
 */
export function isAllowedRequest(url: string, origin: PageOrigin, ctx: NavigationContext = FAIL_CLOSED): boolean {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  if (parsed.protocol === 'file:') return origin === 'file' && isFileInRoots(url, ctx.fileRoots)
  if (!plainAddress(url, parsed)) return false
  if (!['http:', 'https:', 'ws:', 'wss:'].includes(parsed.protocol) || origin === 'file') return false
  const host = hostOf(parsed)
  if (!host) return false
  if (isLoopbackHost(host)) return origin === 'loopback' && ctx.ports !== null && !ctx.ports.has(portOf(parsed))
  return !(isIP(host) && (isPrivateAddress(host) || embedsIpv4(host)))
}

/** Static check on a URL the host itself opens: http(s) anywhere public, loopback except Gamachine's ports, file: inside the roots. */
export function isAllowedNavigation(url: string, ctx: NavigationContext = FAIL_CLOSED): boolean {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  if (parsed.protocol === 'file:') return isAllowedRequest(url, 'file', ctx)
  return (parsed.protocol === 'http:' || parsed.protocol === 'https:') && isAllowedRequest(url, 'loopback', ctx)
}

/**
 * True when any address the name resolves to is private. A lookup failure counts as
 * private: the request is refused rather than sent to an address nobody checked.
 */
export async function resolvesToPrivate(hostname: string, lookup: LookupAll = dns.lookup as LookupAll): Promise<boolean> {
  const host = hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase()
  if (!host || host === 'localhost' || host.endsWith('.localhost')) return true
  if (isIP(host)) return isPrivateAddress(host)
  try {
    const addresses = await lookup(host, { all: true })
    return addresses.length === 0 || addresses.some((a) => isPrivateAddress(a.address))
  } catch {
    return true
  }
}
