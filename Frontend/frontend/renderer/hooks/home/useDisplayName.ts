import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import { displayName } from '../../lib/displayName'

// Display name, 2 Oct 2026: keep edits independent of the stable auth.user object.
export function useDisplayName(API: string, ready: boolean) {
  const [name, setName] = useState('')

  useEffect(() => {
    if (!ready) return
    let active = true
    axios.get(`${API}/me`).then(res => {
      if (active) setName(displayName(res.data.name))
    }).catch(() => {})
    return () => { active = false }
  }, [API, ready])

  const saveName = useCallback(async (value: string): Promise<boolean> => {
    try {
      const res = await axios.put(`${API}/me/name`, { name: value.trim() })
      setName(displayName(res.data.name))
      return true
    } catch {
      return false
    }
  }, [API])

  return { name, saveName }
}
