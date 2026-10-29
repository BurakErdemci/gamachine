import { RemoteError, type RemoteTransport } from './transport'

/** Base URL the renderer gets from 'get-backend-base-url' in remote mode; the http shims own it. */
export const REMOTE_BACKEND_BASE = 'remote://backend'

// Native dialogs, authorising a new root, pairing and profile reset have no remote form
// (docs/remote-control-web.md, "Work parity"); refusing them here saves a round trip.
const DESKTOP_ONLY = new Set([
  'open-file-dialog',
  'open-video-dialog',
  'open-folder-dialog',
  'authorize-workspace',
  'remote-control',
  'profile-reset',
])

export interface RemoteIpc {
  send(channel: string, value: unknown): void
  on(channel: string, callback: (...args: unknown[]) => void): () => void
  invoke(channel: string, ...args: unknown[]): Promise<any>
  registerDroppedFolder(file: File): Promise<{ path: string } | { error: string }>
}

/** The preload `window.ipc` shape (main/preload.ts) over the remote transport. */
export function createIpcShim(transport: RemoteTransport, onUnavailable: (channel: string) => void): RemoteIpc {
  const unavailable = (channel: string) => {
    onUnavailable(channel)
    return new RemoteError('remote_unavailable')
  }
  return {
    send() {},
    on(channel, callback) {
      return transport.subscribe(channel, callback)
    },
    async invoke(channel, ...args) {
      if (channel === 'get-backend-base-url') return REMOTE_BACKEND_BASE
      // The desktop applies the device identity; no session token exists in the browser.
      if (channel === 'app-token-get') return null
      if (DESKTOP_ONLY.has(channel)) throw unavailable(channel)
      try {
        // All arguments go: multi-argument channels (write-file path, content, workspace) stay whole.
        return await transport.ipc(channel, args)
      } catch (err) {
        if (err instanceof RemoteError && err.code === 'remote_unavailable') throw unavailable(channel)
        if (err instanceof RemoteError && err.code === 'too_large') {
          // An answer (or argument) that no frame can carry: retrying would not help.
          const tooLarge = new RemoteError('too_large', err.reply)
          tooLarge.message = `${channel}: too large to send over the remote connection`
          throw tooLarge
        }
        throw err
      }
    },
    async registerDroppedFolder() {
      // A dropped file has no path on the desktop.
      unavailable('register-dropped-folder')
      return { error: 'remote_unavailable' }
    },
  }
}
