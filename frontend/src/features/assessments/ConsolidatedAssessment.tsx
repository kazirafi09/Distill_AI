import type { F6Report, F6Source, Result } from './api'
import { scoreLabel, timeLabel } from './format'

const sections = { strengths: 'Strengths', weaknesses: 'Observed weaknesses', evidence_gaps: 'Evidence gaps', recommended_areas: 'Recommended areas to verify' } as const
const positions: Record<string, string> = { supported_for_further_consideration: 'Supported for further consideration', mixed_evidence: 'Mixed evidence', insufficient_evidence: 'Insufficient evidence', evidence_raises_concerns: 'Evidence raises concerns' }
const label = (text: string) => text.replaceAll('_', ' ')

function Source({ source, onEvidence }: { source: F6Source; onEvidence: (id: string) => void }) {
  return <div className="f6-source">
    <strong>{source.kind === 'cv' ? 'CV screening evidence' : source.kind === 'coverage' ? 'Interview coverage' : `Interview · ${source.dimension}`}</strong>
    <p>{source.justification ?? (source.exploration === 'unasked' ? 'This requirement was not asked about in the interview.' : 'This requirement was explored in the interview.')}</p>
    {source.decision && <p className="assessment-muted">Saved decision: {label(source.decision)}</p>}
    {source.score_100 !== undefined && <p className="assessment-muted">Saved score: {scoreLabel(source.score_100)}</p>}
    {source.evidence?.map((evidence, index) => <blockquote key={index}><p>{evidence.quote}</p>
      {evidence.turn_id ? <button type="button" onClick={() => onEvidence(evidence.turn_id!)}>View transcript · {timeLabel(evidence.session_start_ms)}</button>
        : <small>CV source {evidence.source_id} · characters {evidence.start}–{evidence.end}<br />File version {evidence.file_version_id} · parse {evidence.parse_id}</small>}
    </blockquote>)}
  </div>
}

export default function ConsolidatedAssessment({ data, onEvidence, onParent }: { data: Result; onEvidence: (id: string) => void; onParent: (id: string) => void }) {
  const report = data.result as F6Report
  const byId = new Map(report.source_registry.map(source => [source.id, source]))
  function citations(ids: string[]) {
    return <details className="f6-citations"><summary>Supporting evidence · {ids.length} sources</summary>{ids.map(id => {
      const source = byId.get(id)
      return source ? <Source key={id} source={source} onEvidence={onEvidence} /> : <p key={id}>Source unavailable.</p>
    })}</details>
  }
  return <section className="f6-assessment" aria-label="Consolidated assessment">
    <div className="f6-position"><p className="assessment-kicker">F6 · Consolidated findings</p><h2>{positions[report.findings.suitability.position] ?? 'Assessment'}</h2>
      <p>{report.findings.suitability.rationale}</p>{citations(report.findings.suitability.source_ids)}</div>
    <section className="f6-cv-score"><div><h3>Saved CV screening score</h3><strong>{scoreLabel(report.component_scores.cv.aggregate)}</strong></div>
      <p className="assessment-muted">{report.provenance.cv_assessment_id.startsWith('synthetic-') ? 'Development fixture with authored screening decisions. ' : ''}Documented CV credit is shown separately from interview performance.</p>
      <details><summary>CV score breakdown</summary>{report.component_scores.cv.dimensions.map(item => <p key={item.dimension}>{label(item.dimension)}: {scoreLabel(item.score)} · weight {item.approved_percentage}%</p>)}<p className="assessment-muted">{report.component_scores.cv.note}</p></details>
    </section>
    {(Object.keys(sections) as (keyof typeof sections)[]).map(section => <section key={section} className="f6-finding-section"><h3>{sections[section]}</h3>
      {!report.findings[section].length && <p className="assessment-muted">No supported finding recorded in this section.</p>}
      {report.findings[section].map((finding, index) => <article key={index}><p>{finding.text}</p><small>{data.criteria.find(criterion => criterion.id === finding.criterion_id)?.text ?? finding.criterion_id}</small>{citations(finding.source_ids)}</article>)}
    </section>)}
    <button className="button-secondary" onClick={() => onParent(report.provenance.f5_run_id)}>View original F5 analysis</button>
  </section>
}
