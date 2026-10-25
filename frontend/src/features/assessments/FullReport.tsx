import { useEffect, useRef, useState } from 'react'
import { createPortal, flushSync } from 'react-dom'
import { downloadFullReport, loadFullReport } from './api'
import type { FullReport as Document, ReportType } from './api'
import ReportDocument from './ReportDocument'
import { apiRequest } from '../../services/api'

export default function FullReport({ token, runId, parentId, completed }: { token: string; runId: string; parentId: string; completed: boolean }) {
  const [report, setReport] = useState<Document | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<'view' | 'download' | 'print' | null>(null)
  const [error, setError] = useState('')
  const [reportType, setReportType] = useState<ReportType>('recruiter')
  const [includeEvidence, setIncludeEvidence] = useState(false)
  const [insightIds, setInsightIds] = useState<string[]>([])
  const [insightRuns, setInsightRuns] = useState<{id: string; kind: string}[]>([])
  const [includeInsights, setIncludeInsights] = useState(false)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])

  async function act(action: 'view' | 'download' | 'print') {
    if (controller.current && !controller.current.signal.aborted) return
    const request = new AbortController()
    controller.current = request
    setBusy(action); setError('')
    try {
      if (action === 'view' || action === 'print') {
        const data = report ?? await loadFullReport(token, runId, request.signal, reportType, includeEvidence, insightIds)
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
        const blob = await downloadFullReport(token, runId, request.signal, reportType, includeEvidence, insightIds)
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
  async function selectInsights(include: boolean) {
    setReport(null); setOpen(false); setError('')
    if(!include) {setInsightIds([]); setInsightRuns([]); setIncludeInsights(false); return}
    if(controller.current) return
    setIncludeInsights(true)
    const request = new AbortController(); controller.current = request; setBusy('view')
    try {
      const data = await apiRequest<{items: {run: {id: string; kind: string; status: string}; result: unknown}[]}>(`/assessments/runs/${parentId}/post-interview`, {token,signal:request.signal,cache:'no-store'})
      const seen = new Set<string>()
      const ids = data.items.filter(item => {
        if(item.run.status !== 'completed' || !item.result || seen.has(item.run.kind)) return false
        seen.add(item.run.kind); return true
      }).map(item => item.run.id)
      if(!request.signal.aborted) {setInsightIds(ids); setInsightRuns(data.items.filter(item => ids.includes(item.run.id)).map(item => ({id: item.run.id, kind: item.run.kind}))); if(!ids.length) {setIncludeInsights(false); setError('No completed post-interview insights yet. Use the CV claims, Performance or Interview quality tabs to generate analyses, then return to export.')}}
    } catch(caught) {if(!request.signal.aborted) {setIncludeInsights(false); setError(caught instanceof Error ? caught.message : 'Could not select saved insights.')}}
    finally {if(!request.signal.aborted) {setBusy(null); controller.current = null}}
  }
  return <section className="assessment-report-tools" aria-label="Full assessment report">
    <div><h3>Prepare your report</h3><p>Choose a concise recruiter overview or a full technical report with the saved analysis and transcript.</p></div>
    <div className="assessment-report-options">
      <label className="report-type-option">Report type<select aria-label="Report type" value={reportType} disabled={!!busy} onChange={event => { setReportType(event.target.value as ReportType); setReport(null); setOpen(false); setError('') }}><option value="recruiter">Recruiter overview</option><option value="technical">Full technical report</option></select></label>
      <label className="report-include-option"><input type="checkbox" checked={includeEvidence || reportType === 'technical'} disabled={!!busy || reportType === 'technical'} onChange={event => { setIncludeEvidence(event.target.checked); setReport(null); setOpen(false); setError('') }} /><span><strong>Include evidence appendix</strong><small>{reportType === 'technical' ? 'Always included in the full technical report.' : 'Add supporting quotations and source references.'}</small></span></label>
      <label className="report-include-option"><input type="checkbox" checked={includeInsights} disabled={!!busy || !completed} onChange={event => void selectInsights(event.target.checked)} /><span><strong>Include completed post-interview insights</strong><small>{insightIds.length > 0 ? `${insightIds.length} saved results selected.` : 'Attach available CV claim, performance and interview-quality results.'}</small></span></label>
    </div>
    {includeInsights && insightRuns.length > 0 && <details><summary>Saved insights selected for export</summary><p className="assessment-muted">Latest completed result for each feature. Browsing a historical result does not change this selection.</p>{insightRuns.map(item => <p className="assessment-muted" key={item.id}>{item.kind.replaceAll('_', ' ')} · {item.id}</p>)}</details>}
    <div className="assessment-report-actions">
      <button className="button-secondary" disabled={!completed || !!busy} onClick={() => open ? setOpen(false) : void act('view')}>{busy === 'view' ? 'Loading report…' : open ? 'Close report' : 'Preview report'}</button>
      <button className="button-secondary" disabled={!completed || !!busy} onClick={() => void act('print')}>{busy === 'print' ? 'Preparing print…' : 'Print'}</button>
      <button className="button-primary" disabled={!completed || !!busy} onClick={() => void act('download')}>{busy === 'download' ? 'Preparing PDF…' : 'Download PDF'}</button>
    </div>
    {!completed && <p className="assessment-muted">The report becomes available when this assessment completes.</p>}
    {completed && <p className="assessment-muted">{reportType === 'recruiter' ? 'The recruiter report is a short overview. Evidence is optional; the full transcript and technical details are available in the full technical report.' : 'The full technical report includes all saved Q&A, the complete transcript, source details, and evidence appendix.'} Print opens your browser’s print dialog.</p>}
    {error && <p className="assessment-error" role="alert">{error}</p>}
    {open && report && <div className="assessment-full-report" aria-label="Full report preview"><ReportDocument report={report} prefix={`preview-${runId}`} /></div>}
    {report && createPortal(<div className="assessment-print-root"><ReportDocument report={report} prefix={`print-${runId}`} /></div>, document.body)}
  </section>
}
