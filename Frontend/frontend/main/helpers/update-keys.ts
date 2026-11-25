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

// macOS manifests (latest-mac.yml) are signed with their own keys, never the two above: 4.0.0-4.0.3
// Macs verify latest-mac.yml with those, so a Mac manifest signed with another key makes them show
// the "download manually" dialog instead of crashing in Squirrel.Mac on the 1.37 GB zip. Generated
// like the others (scripts/update-keygen.js); while empty, macOS refuses every update.
export const MAC_PRIMARY_UPDATE_PUBLIC_KEY = `
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAPLOwSV7BWNt/Gu3lYxCGZEfMATfCyOX5NHj/OzuDjL0=
-----END PUBLIC KEY-----
`

// Kept offline; used only after rotating away from the Mac primary key.
export const MAC_BACKUP_UPDATE_PUBLIC_KEY = `
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAXMdntnWR+1xoKNETLWXYXO8qgIdZBFG+UMWYHeCaUkY=
-----END PUBLIC KEY-----
`

// An empty template (BEGIN/END with no base64 between) is not a key.
export const keysWithBody = (pems: string[]): string[] =>
  pems.map((k) => k.trim()).filter((k) => k.replace(/-----(BEGIN|END) PUBLIC KEY-----|\s/g, '') !== '')

export const UPDATE_PUBLIC_KEYS: string[] = keysWithBody([PRIMARY_UPDATE_PUBLIC_KEY, BACKUP_UPDATE_PUBLIC_KEY])

export const MAC_UPDATE_PUBLIC_KEYS: string[] = keysWithBody([MAC_PRIMARY_UPDATE_PUBLIC_KEY, MAC_BACKUP_UPDATE_PUBLIC_KEY])
