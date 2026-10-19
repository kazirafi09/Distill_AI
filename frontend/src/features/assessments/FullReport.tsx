import { useEffect, useRef, useState } from 'react'
import { createPortal, flushSync } from 'react-dom'
import { downloadFullReport, loadFullReport } from './api'
import type { FullReport as Document, ReportType } from './api'
import ReportDocument from './ReportDocument'

export default function FullReport({ token, runId, completed }: { token: string; runId: string; completed: boolean }) {
  const [report, setReport] = useState<Document | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<'view' | 'download' | 'print' | null>(null)
  const [error, setError] = useState('')
  const [reportType, setReportType] = useState<ReportType>('recruiter')
  const [includeEvidence, setIncludeEvidence] = useState(false)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])

  async function act(action: 'view' | 'download' | 'print') {
    if (controller.current && !controller.current.signal.aborted) return
    const request = new AbortController()
    controller.current = request
    setBusy(action); setError('')
    try {
      if (action === 'view' || action === 'print') {
        const data = report ?? await loadFullReport(token, runId, request.signal, reportType, includeEvidence)
        if (!request.signal.aborted) {
          // Commit the separate print document before asking the browser to capture it.
          flushSync(() => { setReport(data); setOpen(true) })
          if (action === 'print') {
            await document.fonts.ready
            await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
            if (request.signal.aborted) return
            const previousTitle = document.title
            document.title = data.filename.replace(/\.pdf$/i, '')
            try { window.print() } finally { document.title = previousTitle }
          }
        }
      } else {
        const blob = await downloadFullReport(token, runId, request.signal, reportType, includeEvidence)
        if (!request.signal.aborted) {
          const url = URL.createObjectURL(blob)
          const link = document.createElement('a')
          link.href = url; link.download = `distill-assessment-${runId}${reportType === 'technical' ? '-technical' : includeEvidence ? '-evidence' : ''}.pdf`
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
    <div><h3>Candidate report</h3><p>Scores, key findings, and requirement coverage. The full technical export retains detailed analysis and the transcript.</p></div>
    <div className="assessment-report-options">
      <label>Report type<select value={reportType} disabled={!!busy} onChange={event => { setReportType(event.target.value as ReportType); setReport(null); setOpen(false); setError('') }}><option value="recruiter">Recruiter report</option><option value="technical">Full technical report</option></select></label>
      <label><input type="checkbox" checked={includeEvidence} disabled={!!busy || reportType === 'technical'} onChange={event => { setIncludeEvidence(event.target.checked); setReport(null); setOpen(false); setError('') }} />Include evidence appendix</label>
    </div>
    <div className="assessment-report-actions">
      <button className="button-secondary" disabled={!completed || !!busy} onClick={() => open ? setOpen(false) : void act('view')}>{busy === 'view' ? 'Loading report…' : open ? 'Close report' : 'Preview report'}</button>
      <button className="button-primary" disabled={!completed || !!busy} onClick={() => void act('print')}>{busy === 'print' ? 'Preparing print…' : 'Print / Save as PDF'}</button>
      <button className="button-secondary" disabled={!completed || !!busy} onClick={() => void act('download')}>{busy === 'download' ? 'Preparing PDF…' : 'Download report (PDF)'}</button>
    </div>
    {!completed && <p className="assessment-muted">The report becomes available when this assessment completes.</p>}
    {completed && <p className="assessment-muted">{reportType === 'recruiter' ? 'The recruiter report is a short overview. Evidence is optional; the full transcript and technical details are available in the full technical report.' : 'The full technical report includes all saved Q&A, the complete transcript, source details, and evidence appendix.'} Print / Save as PDF opens your browser’s print dialog.</p>}
    {error && <p className="assessment-error" role="alert">{error}</p>}
    {open && report && <div className="assessment-full-report" aria-label="Full report preview"><ReportDocument report={report} prefix={`preview-${runId}`} /></div>}
    {report && createPortal(<div className="assessment-print-root"><ReportDocument report={report} prefix={`print-${runId}`} /></div>, document.body)}
  </section>
}
