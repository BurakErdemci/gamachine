// Public keys that may sign an update manifest (Ed25519, SPKI PEM), as printed by
// scripts/update-keygen.js (docs/releasing.md). With no key configured every update is refused.
export const PRIMARY_UPDATE_PUBLIC_KEY = `
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAkqHWZ7dZ6RTVrhrlkuc0fLyigNM6PP6Xc96cwlSOtm0=
-----END PUBLIC KEY-----
`

// Kept offline; used only after rotating away from the primary key.
export const BACKUP_UPDATE_PUBLIC_KEY = `
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAzEr6x50dQ2M9SKIfd/0ycq/u7UBlqjmxveXB8d3wFt0=
-----END PUBLIC KEY-----
`

export const UPDATE_PUBLIC_KEYS: string[] = [PRIMARY_UPDATE_PUBLIC_KEY, BACKUP_UPDATE_PUBLIC_KEY]
  .map((k) => k.trim())
  .filter(Boolean)
