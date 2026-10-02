import React, { useEffect, useRef, useState } from 'react'
import { loadAppearance, useAppearance } from '../../lib/appearance'
import { useLang } from '../../lib/i18n'
import { renderIntroMarkup } from './introMarkup'

const OFF: Record<number, number> = { 1: 0, 2: 600, 3: 0, 4: 0, 5: 0 }

export function pickIntroScene(): number {
  let last = 0
  try { last = Number(localStorage.getItem('app-intro-last')) } catch { /* Storage may be denied. */ }
  const validLast = Number.isInteger(last) && last >= 1 && last <= 5
  const pool = [1, 2, 3, 4, 5].filter(candidate => candidate !== last)
  const scene = validLast ? pool[Math.floor(Math.random() * pool.length)] : 1
  try { localStorage.setItem('app-intro-last', String(scene)) } catch { /* Play even when saving is denied. */ }
  return scene
}

export function IntroOverlay() {
  const { appearance } = useAppearance()
  const { t } = useLang()
  const [scene, setScene] = useState<number | null>(null)
  const [visible, setVisible] = useState(false)
  const chosenScene = useRef<number | null>(null)
  const wrapperRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // The provider initially exposes defaults; consult storage before showing even one frame.
    if (!appearance.intro || !loadAppearance().intro || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    // StrictMode replays mount effects. Keep the same choice for this window's launch.
    chosenScene.current ??= pickIntroScene()
    setScene(chosenScene.current)
    setVisible(true)
  }, [])

  useEffect(() => {
    const wrapper = wrapperRef.current
    if (scene === null || !wrapper) return
    const root = document.documentElement
    const intro = wrapper.querySelector('#ov')
    const run = scene === 1 ? 'run' : `run${scene}`
    let ended = false
    let offsetTimer: ReturnType<typeof setTimeout> | undefined
    let cleanupTimer: ReturnType<typeof setTimeout> | undefined

    const siblings = Array.from(document.querySelectorAll<HTMLElement>('#__next > *'))
      .filter(element => element !== wrapper && !element.contains(wrapper))
    const appRoots = siblings.length ? siblings : Array.from(document.querySelectorAll<HTMLElement>('.app'))
    const inertBefore = appRoots.map(element => element.getAttribute('inert'))
    appRoots.forEach(element => element.setAttribute('inert', ''))

    function reclaimFocus() {
      const active = document.activeElement
      if (active instanceof HTMLElement && appRoots.some(element => element.contains(active))) active.blur()
    }
    function releaseFocus() {
      appRoots.forEach((element, index) => {
        const previous = inertBefore[index]
        if (previous === null) element.removeAttribute('inert')
        else element.setAttribute('inert', previous)
      })
    }
    function cleanRoot() {
      root.classList.remove(run, 'intro-run')
      root.removeAttribute('data-intro')
      root.removeAttribute('data-scene')
    }
    function detachListeners() {
      window.removeEventListener('keydown', onKeyDown, true)
      document.removeEventListener('focusin', reclaimFocus, true)
      wrapper!.removeEventListener('click', onClick)
      wrapper!.removeEventListener('animationend', onAnimationEnd)
    }
    function end(skip: boolean) {
      if (ended) return
      ended = true
      clearTimeout(offsetTimer)
      if (skip) {
        root.classList.add('intro-run')
        // Flush the entrance styles so scene 2 can also finish its still-delayed app entrance.
        void root.offsetWidth
        for (const animation of document.getAnimations?.() ?? []) {
          const target = (animation.effect as KeyframeEffect | null)?.target
          const name = 'animationName' in animation ? animation.animationName : ''
          const isEntrance = typeof name === 'string' && name.startsWith('intro-')
          if (!(target instanceof Element)) continue
          if (!wrapper!.contains(target) && !(isEntrance && appRoots.some(element => element.contains(target)))) continue
          try { animation.finish() } catch { /* A cancelled animation must not prevent dismissal. */ }
        }
      }
      detachListeners()
      releaseFocus()
      setVisible(false)
      // The overlay ends just before the last app entrance does. Keep its fill styles until
      // every panel has arrived; one second after dismissal also clears the skip handoff.
      cleanupTimer = setTimeout(cleanRoot, 1000)
    }
    function onClick() { end(true) }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        end(true)
      }
    }
    function onAnimationEnd(event: AnimationEvent) {
      if (event.target === intro && event.animationName === 'i-end') end(false)
    }

    root.setAttribute('data-intro', '')
    root.setAttribute('data-scene', String(scene))
    root.classList.add(run)
    if (OFF[scene]) offsetTimer = setTimeout(() => root.classList.add('intro-run'), OFF[scene])
    else root.classList.add('intro-run')
    reclaimFocus()
    window.addEventListener('keydown', onKeyDown, true)
    document.addEventListener('focusin', reclaimFocus, true)
    wrapper.addEventListener('click', onClick)
    wrapper.addEventListener('animationend', onAnimationEnd)

    return () => {
      clearTimeout(offsetTimer)
      clearTimeout(cleanupTimer)
      detachListeners()
      if (!ended) releaseFocus()
      cleanRoot()
    }
  }, [scene])

  if (!visible) return null
  // The design markup is static and trusted; only HTML-escaped i18n strings are dynamic.
  return (
    <div
      ref={wrapperRef}
      className="intro-overlay"
      aria-hidden="true"
      dangerouslySetInnerHTML={{ __html: renderIntroMarkup({
        skip: t('intro.skip'), quest: t('intro.quest'), start: t('intro.start'), questTag: t('intro.questTag'),
        sprite: t('intro.sprite'), loading: t('intro.loading'), ready: t('intro.ready'),
        inserted: t('intro.inserted'), plus: t('intro.plus'), brand: t('intro.brand'),
      }) }}
    />
  )
}
