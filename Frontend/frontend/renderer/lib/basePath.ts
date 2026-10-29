/** Prefix for public-folder URLs: '/app' in the relay-served remote export, nothing on the desktop. */
export function withBase(path: string): string {
  return (process.env.NEXT_PUBLIC_BASE_PATH ?? '') + path
}
