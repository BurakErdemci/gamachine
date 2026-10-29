import { describe, expect, it, vi } from 'vitest'
import { isAllowedNavigation, isPrivateAddress, resolvesToPrivate } from '../main/helpers/browser-url-policy'

describe('agent browser navigation allow-list', () => {
  it('allows public http and https pages', () => {
    for (const url of ['https://example.com/', 'http://opengameart.org/content/x', 'https://assetstore.unity.com/?q=walk', 'https://8.8.8.8/'])
      expect(isAllowedNavigation(url)).toBe(true)
  })

  it('refuses other schemes, loopback, private and localhost hosts and garbage', () => {
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
