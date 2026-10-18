import { useEffect, useRef, useState } from 'react'
import { downloadFullReport, loadFullReport } from './api'
import type { FullReport as Document } from './api'

export default function FullReport({ token, runId, completed }: { token: string; runId: string; completed: boolean }) {
  const [report, setReport] = useState<Document | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<'view' | 'download' | null>(null)
  const [error, setError] = useState('')
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])

  async function act(action: 'view' | 'download') {
    if (controller.current && !controller.current.signal.aborted) return
    const request = new AbortController()
    controller.current = request
    setBusy(action); setError('')
    try {
      if (action === 'view') {
        const data = report ?? await loadFullReport(token, runId, request.signal)
        if (!request.signal.aborted) { setReport(data); setOpen(true) }
      } else {
        const blob = await downloadFullReport(token, runId, request.signal)
        if (!request.signal.aborted) {
          const url = URL.createObjectURL(blob)
          const link = document.createElement('a')
          link.href = url; link.download = `distill-assessment-${runId}.pdf`
          link.click()
          setTimeout(() => URL.revokeObjectURL(url), 1000)
        }
      }
    } catch (caught) {
      if (!request.signal.aborted) setError(caught instanceof Error ? caught.message : 'Could not prepare the report. Please try again.')
    } finally {
      if (!request.signal.aborted) { setBusy(null); controller.current = null }
    }
  }
  return <section className="assessment-report-tools" aria-label="Full assessment report">
    <div><h3>Full candidate report</h3><p>CV screening, interview flow, scores, consolidated findings and complete transcript.</p></div>
    <div className="assessment-report-actions">
      <button className="button-secondary" disabled={!completed || !!busy} onClick={() => open ? setOpen(false) : void act('view')}>{busy === 'view' ? 'Loading report…' : open ? 'Close full report' : 'View full report'}</button>
      <button className="button-primary" disabled={!completed || !!busy} onClick={() => void act('download')}>{busy === 'download' ? 'Preparing PDF…' : 'Download full report (PDF)'}</button>
    </div>
    {!completed && <p className="assessment-muted">The report becomes available when this assessment completes.</p>}
    {error && <p className="assessment-error" role="alert">{error}</p>}
    {open && report && <article className="assessment-full-report" aria-label="Full report preview">
      <h2>{report.title}</h2><p className="assessment-kicker">{report.metadata.candidate.name}</p>
      <nav aria-label="Report contents">{report.sections.map((section, index) => <a href={`#report-${runId}-${index}`} key={section.title}>{section.title}</a>)}</nav>
      {report.sections.map((section, index) => <section id={`report-${runId}-${index}`} key={section.title}>
        <h3>{section.title}</h3>{section.blocks.map((block, ordinal) => block.kind === 'heading'
          ? <h4 key={ordinal}>{block.text}</h4> : block.kind === 'quote' ? <blockquote key={ordinal}>{block.text}</blockquote>
            : <p key={ordinal} className={`report-${block.kind}`}>{block.text}</p>)}
      </section>)}
    </article>}
  </section>
}
