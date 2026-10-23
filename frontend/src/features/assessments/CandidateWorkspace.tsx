import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ActionButton, DemoBanner, Notice, Panel, StatusBadge } from '../../components/recruitment/RecruitmentUI'
import type { Workspace } from './api'
import { actionLabel, candidateRows, filterRows, statusOptions } from './workspace'
import CandidateComparison from './CandidateComparison'
import { comparisonSelection, toggleComparison } from './comparison'
import type { ComparisonSelection } from './comparison'
import './CandidateWorkspace.css'

function dateLabel(date?: string | null) {
  if (!date) return '—'
  return new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(date))
}

export default function CandidateWorkspace({ data, error, jobId, token, jobTitle, busy, hidden = false, onRetry, onOpen, onStart, onPage }: {
  data: Workspace | null; error: string; jobId: string; token: string; jobTitle: string; busy: boolean
  hidden?: boolean
  onRetry: () => void; onOpen: (id: string) => void; onStart: (id: string) => void; onPage: (offset: number) => void
}) {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')
  const [attention, setAttention] = useState(false)
  const [selected, setSelected] = useState<ComparisonSelection[]>([])
  const [comparison, setComparison] = useState<ComparisonSelection[] | null>(null)
  const rows = data ? candidateRows(data) : []
  const pageSize = data?.page_size ?? 5
  const visible = filterRows(rows, search, status, attention)
  const metrics = [
    ['Interviews held', rows.filter(row => row.interview.key === 'completed').length],
    ['Assessments completed', rows.filter(row => row.report.key === 'completed').length],
    ['Older criteria', rows.filter(row => row.report.key === 'outdated' || row.qa.key === 'outdated').length],
    ['Needs review', rows.filter(row => [row.qa, row.report, row.transcript].some(item => item.key === 'needs_review')).length],
    ['Processing', rows.filter(row => [row.qa, row.report].some(item => item.key === 'processing')).length],
    ['Blocked', rows.filter(row => row.qa.key === 'blocked' || row.report.key === 'blocked').length],
  ] as const
  return <div className="candidate-workspace" hidden={hidden}>
    {error && <Notice title={data ? 'Could not refresh assessments' : 'We could not load assessments'} tone="danger" actions={<ActionButton onClick={onRetry}>Try again</ActionButton>}>
      <p>{error}</p><p>{data ? 'Showing the last loaded records. Your filters and job selection are kept.' : 'Your job selection is kept. Try loading these records again.'}</p>
    </Notice>}
    {!data && !error && <Panel className="workspace-loading"><div role="status"><h2>Loading assessments</h2><p>Fetching candidate statuses for this job.</p></div><div className="recruitment-skeleton" aria-hidden="true">{[0, 1, 2, 3, 4].map(item => <span key={item} />)}</div></Panel>}
    {data && <>
      {rows.some(row => row.candidate.synthetic) && <DemoBanner>Some candidates on this page are synthetic test records. They are labelled individually.</DemoBanner>}
      <div className="workspace-metrics" aria-label="Candidate statuses on this page">{metrics.map(([label, count]) => <Panel key={label}><strong>{count}</strong><span>{label}</span></Panel>)}</div>
      {!data.profile_ready && <Notice title="Job criteria are needed" tone="warning" actions={<Link className="recruitment-button" to="/recruiter/job-descriptions">Go to Job Descriptions</Link>}>Save this job’s screening criteria before starting a new analysis. Saved reports remain available.</Notice>}
      <Panel className="candidate-list-panel">
        <div className="candidate-toolbar">
          <div className="candidate-list-heading"><h2>Candidate assessments</h2><p>{visible.length} of {rows.length} shown{data.profile_version ? ` · criteria v${data.profile_version}` : ''}</p></div>
          <label className="candidate-search">Search candidates<input type="search" placeholder="Name or email" value={search} onChange={event => setSearch(event.target.value)} /></label>
          <label>Assessment status<select aria-label="Assessment status" value={status} onChange={event => setStatus(event.target.value)}>{statusOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="attention-toggle"><input type="checkbox" checked={attention} onChange={event => setAttention(event.target.checked)} />Only needing attention</label>
        </div>
        <div className="workspace-selection"><div><strong>{selected.length} of 2 selected</strong><p>Select completed assessments to compare. Selection is kept across pages.</p></div><ActionButton variant="primary" disabled={selected.length !== 2 || busy || !!error} onClick={() => setComparison([...selected])}>Compare selected</ActionButton></div>
        {!!selected.length && <ul className="selected-candidates" aria-label="Selected candidates">{selected.map(item => <li key={item.id}>{item.name}<button type="button" aria-label={`Remove ${item.name} from comparison`} onClick={() => setSelected(current => current.filter(candidate => candidate.id !== item.id))}>×</button></li>)}</ul>}
        {!rows.length ? <div className="workspace-empty"><Notice title="No candidates for this job yet" actions={<Link className="recruitment-button" to={`/recruiter/interviews?job=${jobId}`}>Go to Interviews</Link>}>Candidates and their assessment statuses appear here once they are added to this job.</Notice></div>
          : !visible.length ? <div className="workspace-empty"><Notice title="No candidates match these filters" actions={<ActionButton onClick={() => { setSearch(''); setStatus('all'); setAttention(false) }}>Clear filters</ActionButton>}>Try another name, email, or status.</Notice></div>
            : <div className="candidate-table-scroll" role="region" aria-label="Candidate assessments" tabIndex={0}>
              <table className="candidate-table"><thead><tr><th scope="col" className="candidate-check">Compare</th><th scope="col">Candidate</th><th scope="col">Interview</th><th scope="col">Transcript</th><th scope="col">Q&A analysis</th><th scope="col">Assessment</th><th scope="col" className="candidate-version-heading">Source versions</th><th scope="col">Updated</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
                <tbody>{visible.map(row => {
                  const checked = selected.some(item => item.id === row.candidate.id)
                  const choice = comparisonSelection(row.candidate)
                  const reason = !choice ? 'A completed consolidated assessment is required.' : selected.length >= 2 ? 'Remove a candidate to select another.' : selected.length && selected[0].synthetic !== choice.synthetic ? 'Compare sample candidates separately from real candidates.' : ''
                  return <tr key={row.candidate.id} className={checked ? 'is-selected' : ''}>
                    <td className="candidate-check"><label className="candidate-check-label" title={checked ? 'Remove from comparison' : reason || 'Select for comparison'}><input type="checkbox" aria-label={`Compare ${row.candidate.name}`} checked={checked} disabled={!checked && (!!reason || busy || !!error)} onChange={() => setSelected(current => checked ? current.filter(item => item.id !== row.candidate.id) : choice ? toggleComparison(current, choice) : current)} /><span className="sr-only">{reason}</span></label></td>
                    <th scope="row" className="candidate-name" title={[row.candidate.name, row.candidate.synthetic ? 'Sample data' : '', row.candidate.email, row.note].filter(Boolean).join(' · ')}><strong>{row.candidate.name}</strong>{row.candidate.synthetic && <StatusBadge tone="warning">Sample data</StatusBadge>}<span>{row.candidate.email}</span>{row.candidate.interviewed_at && <span>Interviewed {dateLabel(row.candidate.interviewed_at)}</span>}{row.note && <p>{row.note}</p>}</th>
                    {([['Interview', row.interview], ['Transcript', row.transcript], ['Q&A analysis', row.qa], ['Assessment', row.report]] as const).map(([label, item]) => <td key={label} data-label={label}><StatusBadge tone={item.tone}>{item.label}</StatusBadge></td>)}
                    <td data-label="Source versions" className="candidate-versions">{row.versions}{row.candidate.transcript_version && <span>Transcript v{row.candidate.transcript_version} (latest)</span>}</td>
                    <td data-label="Updated" className="candidate-updated">{dateLabel(row.run?.updated_at)}</td>
                    <td className="candidate-actions"><ActionButton variant={row.report.key === 'completed' ? 'primary' : 'secondary'} disabled={busy || !!error || (!row.run && (!row.candidate.ready || !data.profile_ready))} onClick={() => row.run ? onOpen(row.run.id) : onStart(row.candidate.id)}>{actionLabel(row)}</ActionButton>{!row.run && !row.candidate.ready && <Link to={`/recruiter/interviews?job=${jobId}`}>Open interview</Link>}{row.candidate.assessment_blocker && !row.assessment && <Link to={`/recruiter/cv-screening?job=${jobId}`}>Go to CV Screening</Link>}</td>
                  </tr>
                })}</tbody>
              </table>
            </div>}
        <div className="candidate-pagination"><ActionButton disabled={data.offset === 0} onClick={() => { onPage(Math.max(0, data.offset - pageSize)) }}>Previous</ActionButton><span>Page {Math.floor(data.offset / pageSize) + 1} · {pageSize} per page<span className="pagination-scope"> · Counts and filters apply to this page</span></span><ActionButton disabled={!data.has_more_candidates} onClick={() => { onPage(data.offset + pageSize) }}>Next</ActionButton></div>
      </Panel>
      <p className="workspace-footnote">Statuses are independent. CV screening and interview scores stay separate. Candidates are not ranked and nothing is rejected automatically.</p>
    </>}
    {comparison && !hidden && <CandidateComparison token={token} jobId={jobId} jobTitle={jobTitle} selected={comparison} close={() => setComparison(null)} />}
  </div>
}
