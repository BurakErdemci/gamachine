// Public keys that may sign an update manifest (Ed25519, SPKI PEM).
// Both are EMPTY until the owner pastes the output of scripts/update-keygen.js
// (docs/releasing.md). With no key configured every update is refused.
export const PRIMARY_UPDATE_PUBLIC_KEY = ``

// Kept offline; used only after rotating away from the primary key.
export const BACKUP_UPDATE_PUBLIC_KEY = ``

export const UPDATE_PUBLIC_KEYS: string[] = [PRIMARY_UPDATE_PUBLIC_KEY, BACKUP_UPDATE_PUBLIC_KEY]
  .map((k) => k.trim())
  .filter(Boolean)
