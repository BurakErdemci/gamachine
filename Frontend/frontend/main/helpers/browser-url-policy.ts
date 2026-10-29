import { isIP } from 'net'
import { promises as dns } from 'dns'

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
  return false
}

function hostOf(url: URL): string {
  return url.hostname.replace(/^\[|\]$/g, '').toLowerCase()
}

/** Static check on a URL: http(s) only, and no loopback, private or localhost host written literally. */
export function isAllowedNavigation(url: string): boolean {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false
  const host = hostOf(parsed).replace(/\.$/, '')
  if (!host || host === 'localhost' || host.endsWith('.localhost')) return false
  return !(isIP(host) && isPrivateAddress(host))
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
