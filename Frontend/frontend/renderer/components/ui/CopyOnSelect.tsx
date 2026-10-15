import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useAppearance } from '../../lib/appearance'
import { COPIED_EVENT, installCopyOnSelect } from '../../lib/copyOnSelect'
import { useLang } from '../../lib/i18n'

export function CopyOnSelect() {
  const { appearance } = useAppearance()
  const { t } = useLang()
  const enabled = useRef(appearance.copyOnSelect)
  const chip = useRef<HTMLDivElement>(null)
  const [copied, setCopied] = useState<{ x: number; y: number; id: number } | null>(null)
  enabled.current = appearance.copyOnSelect
  useEffect(() => installCopyOnSelect(() => enabled.current), [])
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    let id = 0
    const onCopied = (event: Event) => {
      const { x, y } = (event as CustomEvent<{ x: number; y: number }>).detail
      setCopied({ x, y, id: ++id })
      clearTimeout(timer)
      timer = setTimeout(() => setCopied(null), 1200)
    }
    window.addEventListener(COPIED_EVENT, onCopied)
    return () => {
      window.removeEventListener(COPIED_EVENT, onCopied)
      clearTimeout(timer)
    }
  }, [])
  useLayoutEffect(() => {
    if (!copied || !chip.current) return
    const width = chip.current.offsetWidth
    const height = chip.current.offsetHeight
    chip.current.style.left = `${Math.max(8, Math.min(copied.x + 12, window.innerWidth - width - 8))}px`
    chip.current.style.top = `${Math.max(8, Math.min(copied.y + 12, window.innerHeight - height - 8))}px`
  }, [copied, t])
  return copied && <div key={copied.id} ref={chip} className="gm-copied" role="status" aria-live="polite"
    style={{ position: 'fixed', left: copied.x + 12, top: copied.y + 12, pointerEvents: 'none' }}>
    {t('sel.copied')}
  </div>
}
