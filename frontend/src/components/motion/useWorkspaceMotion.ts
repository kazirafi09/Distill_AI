import { useEffect } from 'react'
import type { RefObject } from 'react'

const listRows = new Map([
  ['.jd-library-scroll', ':scope > .jd-library-item'],
  ['.jd-conversation-list', ':scope > .jd-library-item'],
  ['.screening-candidate-list', ':scope > .screening-candidate-row'],
  ['.invitation-recipients ul', ':scope > li'],
  ['.interview-table tbody', ':scope > tr'],
  ['.interview-saved-list', ':scope > li'],
])
const statusSelector = '[role="status"], .ui-badge, .lobby-badge, .screening-summary strong, .screening-action-group > span, .lobby-steps li span'
const entrySelector = '.motion-chat-message, .stage-transcript-turn, .screening-candidate-detail, .jd-document-layout, .screening-notice, [role="alert"], .interview-notice, .lobby-notices'

// Enhance existing DOM only: no keys, remounts, delayed focus, or request changes.
export function useWorkspaceMotion(ref: RefObject<HTMLElement | null>, scope: string, stage = false) {
  useEffect(() => {
    const root = ref.current
    if (!root || (!stage && !['/recruiter/job-descriptions', '/recruiter/cv-screening', '/recruiter/interviews', '/recruiter/invitations'].includes(scope))) return
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const styles = getComputedStyle(document.documentElement)
    const duration = (token: string) => Number.parseFloat(styles.getPropertyValue(`--motion-${token}`)) || 0
    const easing = styles.getPropertyValue('--motion-enter').trim()
    const seen = new WeakSet<Element>()
    const loadedLists = new WeakSet<Element>()
    const active = new Map<Element, Animation>()
    const indicators = new Set<HTMLElement>()
    const allowed = (element: Element) => !stage || !!element.closest('.stage-panel, .stage-transcript')

    function animate(element: Element, movement = false, delay = 0) {
      if (!allowed(element) || !(element instanceof HTMLElement) || element.closest('[hidden]')) return
      active.get(element)?.cancel()
      const reduced = media.matches
      const animation = element.animate(
        movement && !reduced ? [{ opacity: 0, transform: 'translateY(4px)' }, { opacity: 1, transform: 'translateY(0)' }] : [{ opacity: 0.45 }, { opacity: 1 }],
        { duration: duration(reduced ? 'reduced' : movement ? 'panel' : 'control'), delay: reduced ? 0 : delay, easing, fill: 'backwards' },
      )
      active.set(element, animation)
      animation.onfinish = () => { if (active.get(element) === animation) active.delete(element) }
      animation.oncancel = animation.onfinish
    }

    function indicator(nav: HTMLElement) {
      const selected = nav.querySelector<HTMLElement>('[aria-current="page"], [aria-selected="true"]')
      if (!selected || !nav.getClientRects().length) return
      const box = nav.getBoundingClientRect(), target = selected.getBoundingClientRect()
      nav.style.setProperty('--motion-indicator-x', `${target.left - box.left + nav.scrollLeft}px`)
      nav.style.setProperty('--motion-indicator-y', `${target.top - box.top + nav.scrollTop}px`)
      nav.style.setProperty('--motion-indicator-width', `${target.width}px`)
      nav.style.setProperty('--motion-indicator-height', `${target.height}px`)
      nav.dataset.motionIndicator = 'ready'
    }
    const resize = new ResizeObserver(() => indicators.forEach(indicator))
    function scan(scopeRoot: Element) {
      if (!stage) {
        const navs = [...scopeRoot.querySelectorAll<HTMLElement>('.redesign-tabs')]
        if (scopeRoot.matches('.redesign-tabs')) navs.push(scopeRoot as HTMLElement)
        for (const nav of navs) if (!indicators.has(nav)) { indicators.add(nav); indicator(nav); resize.observe(nav) }
        for (const [selector, rowSelector] of listRows) {
          const lists = [...scopeRoot.querySelectorAll(selector)]
          const ancestor = scopeRoot.closest(selector)
          if (ancestor) lists.push(ancestor)
          for (const list of new Set(lists)) {
            const rows = [...list.querySelectorAll(rowSelector)]
            const first = !loadedLists.has(list)
            if (rows.length) loadedLists.add(list)
            rows.forEach((row, index) => {
              if (seen.has(row)) return
              seen.add(row)
              if (!first || index < 10) animate(row, first, first ? index * duration('stagger') : 0)
            })
          }
        }
      }
      const entries = [...scopeRoot.querySelectorAll(entrySelector)]
      if (scopeRoot.matches(entrySelector)) entries.push(scopeRoot)
      const firstTranscriptRows = new Map<Element, number>()
      for (const element of entries) if (!seen.has(element)) {
        seen.add(element)
        const transcript = element.matches('.stage-transcript-turn') ? element.closest('.stage-transcript-turns') : null
        if (transcript && !loadedLists.has(transcript)) {
          const index = firstTranscriptRows.get(transcript) ?? 0
          firstTranscriptRows.set(transcript, index + 1)
          if (index >= 10) continue
        }
        animate(element, !element.matches('.ui-badge'))
      }
      firstTranscriptRows.forEach((_, list) => loadedLists.add(list))
      for (const placeholder of scopeRoot.querySelectorAll<HTMLElement>('[role="status"]')) {
        if (!stage && /^Loading /i.test(placeholder.textContent ?? '') && placeholder.closest('.jd-library-scroll, .jd-conversation-list')) placeholder.classList.add('motion-loading')
      }
    }
    function reduce() {
      // A changed OS preference also stops already-running Web Animations immediately.
      if (media.matches) { active.forEach(animation => animation.cancel()); active.clear() }
    }
    const observer = new MutationObserver(records => {
      const fades = new Set<Element>()
      const scans = new Set<Element>()
      for (const record of records) {
        const element = record.target instanceof Element ? record.target : record.target.parentElement
        if (!element || !allowed(element)) continue
        if (record.type === 'childList') {
          if ([...record.addedNodes].some(node => node instanceof Element)) scans.add(element)
        }
        if (record.type === 'characterData' || record.type === 'attributes' || (record.type === 'childList' && [...record.addedNodes].some(node => node.nodeType === Node.TEXT_NODE))) {
          const status = element.closest(statusSelector)
          if (status && allowed(status)) fades.add(status)
          if (record.type === 'attributes' && record.attributeName === 'aria-label' && element.matches('.stage-panel-content')) fades.add(element)
          if (record.type === 'attributes' && record.attributeName === 'hidden' && !element.hasAttribute('hidden')) fades.add(element)
          const nav = element.closest<HTMLElement>('.redesign-tabs')
          if (nav) indicator(nav)
          if (record.attributeName === 'class' && element.matches('.jd-library-item, .screening-candidate-row')) {
            const navs = root.querySelectorAll<HTMLElement>('.redesign-tabs')
            navs.forEach(indicator)
          }
        }
        if (!stage && record.type === 'childList' && element.closest('.jd-document-scroll')) fades.add(element.closest('.jd-document-scroll')!)
        if (!stage && record.type === 'childList' && element.matches('.screening-detail-scroll')) fades.add(element)
      }
      for (const element of scans) if (![...scans].some(parent => parent !== element && parent.contains(element))) scan(element)
      fades.forEach(element => animate(element))
    })
    scan(root)
    if (!stage) animate(root, true)
    observer.observe(root, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['aria-current', 'aria-selected', 'aria-label', 'hidden', 'class'] })
    media.addEventListener('change', reduce)
    return () => {
      observer.disconnect(); resize.disconnect(); media.removeEventListener('change', reduce)
      active.forEach(animation => animation.cancel()); active.clear()
      indicators.forEach(nav => { delete nav.dataset.motionIndicator })
    }
  }, [ref, scope, stage])
}

export function useSidebarIndicator(ref: RefObject<HTMLElement | null>, path: string) {
  useEffect(() => {
    const nav = ref.current
    if (!nav) return
    function update() {
      const active = nav!.querySelector<HTMLElement>('[aria-current="page"]')
      if (!active || !nav!.getClientRects().length) return
      const rect = active.getBoundingClientRect(), parent = nav!.getBoundingClientRect()
      nav!.style.setProperty('--motion-indicator-y', `${rect.top - parent.top + nav!.scrollTop}px`)
      nav!.style.setProperty('--motion-indicator-height', `${rect.height}px`)
      nav!.dataset.motionIndicator = 'ready'
    }
    update()
    const resize = new ResizeObserver(update)
    resize.observe(nav)
    return () => resize.disconnect()
  }, [ref, path])
}
