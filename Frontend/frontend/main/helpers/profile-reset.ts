/**
 * Maker profile reset (Settings > Account > Reset statistics) from the main process.
 *
 * `POST /profile/reset` refuses a call without the UI secret, and the renderer never holds that
 * secret (as with 'approval-mode-set'). The path and the body are fixed here; nothing the
 * renderer sends reaches the request.
 */
export interface ProfileResetDeps {
  baseUrl: () => string
  appToken: string
  uiSecret: string
  http: { post: (url: string, body: unknown, config: { timeout: number; headers: Record<string, string> }) => Promise<{ data?: any }> }
  log?: (...args: unknown[]) => void
}

export function createProfileReset(deps: ProfileResetDeps) {
  return async (): Promise<{ cleared: number }> => {
    try {
      const response = await deps.http.post(`${deps.baseUrl()}/profile/reset`, {}, {
        timeout: 10000,
        headers: { 'X-Session-Token': deps.appToken, 'X-Gamachine-UI-Secret': deps.uiSecret },
      })
      return { cleared: Number(response.data?.cleared) || 0 }
    } catch (error) {
      const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      const text = typeof detail === 'string' ? detail : undefined
      deps.log?.('[profile-reset] failed:', text || (error as Error)?.message)
      throw new Error(text || 'Profile reset failed.')
    }
  }
}
