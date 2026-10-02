import React, { useEffect, useRef, useState } from 'react'
import { loadAppearance, useAppearance } from '../../lib/appearance'
import { useLang } from '../../lib/i18n'
import { renderIntroMarkup } from './introMarkup'

const OFF: Record<number, number> = { 1: 0, 2: 600, 3: 0, 4: 0, 5: 0 }
// The longest scene ends at ~8 s. Past this the overlay is dismissed whatever state it is in, so a
// scene that never started (or lost its listeners) can never leave a screen that cannot be skipped.
export const INTRO_SAFETY_MS = 12_000

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
  // The running scene's `end`, for the safety net below; null while no scene is wired up.
  const endRef = useRef<((skip: boolean) => void) | null>(null)
  // The 1 s handoff after a scene ends outlives that scene's effect; only unmount cancels it.
  const handoffRef = useRef<{ timer?: ReturnType<typeof setTimeout>; clean?: () => void }>({})

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
    // Keyed on `visible` too: the overlay could be shown again with a fresh wrapper (dev hot reload
    // re-runs the mount effect) while `scene` stayed the same, and a [scene]-only effect then never
    // wired the new wrapper up: an overlay with no scene classes and no skip listeners, frozen on
    // screen (measured twice on 2 Oct).
    if (scene === null || !visible || !wrapper) return
    const root = document.documentElement
    const intro = wrapper.querySelector('#ov')
    const run = scene === 1 ? 'run' : `run${scene}`
    let ended = false
    let offsetTimer: ReturnType<typeof setTimeout> | undefined

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
      handoffRef.current = { timer: setTimeout(cleanRoot, 1000), clean: cleanRoot }
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

    // Fit the fixed 1600x1000 stage into the window (see intro.css), and keep it fitted on resize.
    const STAGE_W = 1600, STAGE_H = 1000
    // Where the lockup (stage 350,330, 900 wide, transform-origin 0 0) lands: the real sidebar logo,
    // mapped from window into stage coordinates. Measured 2 Oct: the sidebar mounts ~70 ms after the
    // overlay and sits 28 px left under its entrance slide until the handoff, so the target is
    // measured when the flight starts, from layout offsets (which ignore that transform). Without a
    // logo the CSS keeps the mockup's numbers.
    const LOCKUP_X = 350, LOCKUP_Y = 330, LOCKUP_W = 900
    let scale = 1
    function fit() {
      scale = Math.min(window.innerWidth / STAGE_W, window.innerHeight / STAGE_H)
      wrapper!.style.setProperty('--intro-fit', String(scale))
      // The stage background reaches just past the window edges, in stage pixels (+2 for rounding).
      wrapper!.style.setProperty('--bg-x', `${Math.max(0, (window.innerWidth / scale - STAGE_W) / 2) + 2}px`)
      wrapper!.style.setProperty('--bg-y', `${Math.max(0, (window.innerHeight / scale - STAGE_H) / 2) + 2}px`)
      aimHome()
    }
    function aimHome() {
      const brand = document.querySelector<HTMLElement>('.brand')
      const logo = brand?.querySelector('.brand-logo')
      if (!brand || !logo) return
      const logoRect = logo.getBoundingClientRect()
      const brandRect = brand.getBoundingClientRect()
      if (!logoRect.width) return
      let left = 0, top = 0
      for (let el: HTMLElement | null = brand; el; el = el.offsetParent as HTMLElement | null) {
        left += el.offsetLeft; top += el.offsetTop
      }
      left += logoRect.left - brandRect.left
      top += logoRect.top - brandRect.top
      const stageLeft = (window.innerWidth - STAGE_W * scale) / 2
      const stageTop = (window.innerHeight - STAGE_H * scale) / 2
      wrapper!.style.setProperty('--home-dx', `${(left - stageLeft) / scale - LOCKUP_X}px`)
      wrapper!.style.setProperty('--home-dy', `${(top - stageTop) / scale - LOCKUP_Y}px`)
      wrapper!.style.setProperty('--home-s', String(logoRect.width / scale / LOCKUP_W))
    }
    function onAnimationStart(event: AnimationEvent) {
      if (event.animationName === 'i-home') aimHome()
    }
    fit()
    window.addEventListener('resize', fit)
    wrapper.addEventListener('animationstart', onAnimationStart)

    endRef.current = end
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
      detachListeners()
      window.removeEventListener('resize', fit)
      wrapper.removeEventListener('animationstart', onAnimationStart)
      endRef.current = null
      // An ended scene keeps its root classes for the 1 s handoff (see `end`).
      if (!ended) {
        releaseFocus()
        cleanRoot()
      }
    }
  }, [scene, visible])

  useEffect(() => () => {
    clearTimeout(handoffRef.current.timer)
    handoffRef.current.clean?.()
  }, [])

  useEffect(() => {
    if (!visible) return
    const timer = setTimeout(() => {
      if (endRef.current) endRef.current(true)
      else setVisible(false)
    }, INTRO_SAFETY_MS)
    return () => clearTimeout(timer)
  }, [visible])

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
