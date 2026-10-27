import { useId, useState } from 'react'
import type { ReactNode } from 'react'
import './AssessmentInfo.css'

export default function AssessmentInfo({ label, warning = false, children }: { label: string; warning?: boolean; children: ReactNode }) {
  const id = useId()
  const [open, setOpen] = useState(false)
  return <div className={`assessment-info${warning ? ' is-warning' : ''}`}
    onMouseEnter={() => setOpen(true)}
    onMouseLeave={event => { if (!event.currentTarget.contains(document.activeElement)) setOpen(false) }}
    onFocus={() => setOpen(true)}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false) }}
    onKeyDown={event => { if (event.key === 'Escape') { setOpen(false); event.stopPropagation() } }}>
    <button type="button" className="assessment-info-trigger" aria-expanded={open} aria-controls={id} onClick={() => setOpen(true)}>
      <span className="assessment-info-dot" aria-hidden="true" />{label}
    </button>
    <div id={id} className="assessment-info-panel" role="note" aria-label={label} hidden={!open}>{children}</div>
  </div>
}
