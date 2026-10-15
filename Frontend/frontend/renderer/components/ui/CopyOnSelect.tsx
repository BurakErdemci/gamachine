import { useEffect, useRef } from 'react'
import { useAppearance } from '../../lib/appearance'
import { installCopyOnSelect } from '../../lib/copyOnSelect'

export function CopyOnSelect() {
  const { appearance } = useAppearance()
  const enabled = useRef(appearance.copyOnSelect)
  enabled.current = appearance.copyOnSelect
  useEffect(() => installCopyOnSelect(() => enabled.current), [])
  return null
}
