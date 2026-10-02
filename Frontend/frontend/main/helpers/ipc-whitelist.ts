export const ALLOWED_INVOKE_CHANNELS = new Set([
  'get-backend-base-url',
  'open-file-dialog',
  'open-video-dialog',
  'open-folder-dialog',
  'read-directory',
  'read-file',
  'read-model-file',
  'read-image-file',
  'git-status',
  'write-file',
  'file-exists',
  'write-multiple-files',
  'create-file',
  'create-folder',
  'rename-entry',
  'delete-entry',
  'move-entry',
  'app-token-get',
  'approval-mode-set',
  'app-zoom-set',
  'backend-workspace-path',
  'host-workspace-path',
  'path-exists',
  'save-file-dialog',
  'export-text-file',
  'import-text-file',
  'delete-file',
  'terminal-spawn',
  'terminal-write',
  'terminal-resize',
  'notify',
  'remote-control',
  'profile-reset',
  'workspace-info',
  'open-unity-hub',
])

export const PRELOAD_ONLY_CHANNELS = new Set(['register-dropped-folder'])

export function assertRegistrableChannel(channel: string): void {
  if (!PRELOAD_ONLY_CHANNELS.has(channel)) assertAllowedInvokeChannel(channel)
}

export function assertAllowedInvokeChannel(channel: string): void {
  if (!ALLOWED_INVOKE_CHANNELS.has(channel)) {
    throw new Error(`IPC channel izinsiz: ${channel}`)
  }
}
