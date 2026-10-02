import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { displayName } from '../../lib/displayName'

// Display name, 2 Oct 2026: keep edits independent of the stable auth.user object.
export function useDisplayName(API: string, ready: boolean) {
  const [name, setName] = useState('')
  const saveSequence = useRef(0)

  useEffect(() => {
    if (!ready) return
    let active = true
    const sequence = saveSequence.current
    axios.get(`${API}/me`).then(res => {
      if (active && sequence === saveSequence.current) setName(displayName(res.data.name))
    }).catch(() => {})
    return () => { active = false }
  }, [API, ready])

  const saveName = useCallback(async (value: string): Promise<boolean> => {
    // Minor audit fixes, 2 Oct 2026: even a failed save invalidates earlier reads.
    saveSequence.current += 1
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
