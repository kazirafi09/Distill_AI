import { useLayoutEffect, useState, useEffect } from 'react'
import type { ReactNode, RefObject } from 'react'

export default function SidebarDrawer({ open, onClose, trigger, content, drawer, children }: {
  open: boolean; onClose: () => void; trigger: RefObject<HTMLButtonElement | null>; content: RefObject<HTMLElement | null>; drawer: RefObject<HTMLDivElement | null>; children: ReactNode
}) {
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width: 900px)').matches)
  useEffect(() => {
    const query = window.matchMedia('(max-width: 900px)')
    function update() { setMobile(query.matches); if (!query.matches) onClose() }
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [onClose])
  useLayoutEffect(() => {
    if (!mobile || !open) return
    const main = content.current, menu = trigger.current, panel = drawer.current
    const wasInert = main?.inert ?? false
    main?.toggleAttribute('inert', true)
    ;(panel?.querySelector<HTMLElement>('[aria-current="page"]') ?? panel?.querySelector<HTMLElement>('a, button'))?.focus()
    function keydown(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return }
      if (event.key !== 'Tab' || !panel) return
      const stops = [menu, ...panel.querySelectorAll<HTMLElement>('a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]')].filter((element): element is HTMLElement => !!element && element.getClientRects().length > 0)
      const index = stops.indexOf(document.activeElement as HTMLElement)
      event.preventDefault()
      stops[(index + (event.shiftKey ? stops.length - 1 : 1)) % stops.length]?.focus()
    }
    document.addEventListener('keydown', keydown)
    return () => {
      document.removeEventListener('keydown', keydown)
      main?.toggleAttribute('inert', wasInert)
      if (menu?.getClientRects().length) menu.focus()
    }
  }, [mobile, open, onClose, trigger, content, drawer])
  return <div ref={drawer} className={`sidebar-drawer${open ? ' is-open' : ''}`} inert={mobile && !open} aria-hidden={mobile && !open ? true : undefined}>{children}</div>
}
