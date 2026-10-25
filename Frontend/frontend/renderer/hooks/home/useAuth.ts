import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { cevir } from '../../lib/i18n'

export interface User {
  id: number
  email: string
  username: string
  name: string
  sessionToken: string
}

const BASE_LOCAL: Omit<User, 'sessionToken'> = {
  id: 1, email: 'local@localhost', username: 'local', name: 'local',
}

export function useAuth(_API: string, backendReady: boolean) {
  const [sessionToken, setSessionToken] = useState<string>('local')
  const [ready, setReady] = useState(false)
  /**
   * Oturum token'ı ALINAMADI mı (IPC var ama başarısız/boş döndü).
   *
   * Ayrı bir alan çünkü `'local'` iki farklı durumu temsil ediyordu ve ikisi
   * karıştırılıyordu: (a) tarayıcıda/dev modda IPC hiç yok — `'local'` MEŞRU
   * ve backend `UNITYAI_ALLOW_NO_TOKEN=1` ile bunu kabul ediyor; (b) Electron
   * içindeyiz, IPC var ama çağrı düştü — o zaman `'local'` YANLIŞ bir token ve
   * her istek 401 alacak. Eski kod ikisini de sessizce yutup `ready`'yi true
   * yapıyordu; MCP yoklaması bu kapıya bağlı olduğu için onay kartı yolu
   * hiçbir iz bırakmadan ölüyordu (dış denetim: `invalid-auth-readiness`).
   */
  const [tokenError, setTokenError] = useState<string | null>(null)

  useEffect(() => {
    if (!backendReady) return
    const init = async () => {
      const ipc = (window as any).ipc
      let token = 'local'
      let hata: string | null = null
      if (!ipc?.invoke) {
        // Dev modu / tarayıcı: IPC yok. `'local'` burada gerçek değer.
        hata = null
      } else {
        try {
          const alinan = await ipc.invoke('app-token-get')
          // Boş string ya da undefined da BAŞARISIZLIK. Eskiden doğrudan
          // atanıyordu: `axios.defaults.headers.common['X-Session-Token']`
          // undefined olunca başlık hiç gönderilmiyor ve backend 503 dönüyordu.
          if (typeof alinan === 'string' && alinan.length > 0) token = alinan
          else hata = cevir('auth.tokenShape', { tur: typeof alinan })
        } catch (e) {
          hata = cevir('auth.tokenFailed', { hata: e instanceof Error ? e.message : String(e) })
        }
      }
      if (hata) console.error('[auth]', hata)
      setTokenError(hata)
      setSessionToken(token)
      axios.defaults.headers.common['X-Session-Token'] = token
      // `ready` hata durumunda da true: uygulamayı tamamen kilitlemek, token'a
      // ihtiyacı olmayan yolları da öldürürdü. Hatanın GÖRÜNÜR olmasını
      // sağlayan şey bu bayrak ve yoklama tarafındaki ardışık-hata uyarısı
      // (`useMCPApproval`), sessizlik değil.
      setReady(true)
    }
    init()
  }, [backendReady])

  // Memoize user object so it's only recreated when sessionToken changes
  const user = useMemo(() => ({ ...BASE_LOCAL, sessionToken }), [sessionToken])

  return {
    user,
    isLoading: !ready,
    /** IPC token alımı başarısızsa sebebi; başarılıysa (ya da dev modsa) null. */
    tokenError,
  }
}
