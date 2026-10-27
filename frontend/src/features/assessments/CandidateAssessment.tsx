import { Fragment, useEffect, useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import type { Answer, F6Source, Result, Run } from './api'
import { dimensions } from './api'
import { candidateHistory, cvCoverage, findingLabels, findingsFor, findingSections, formatScoreValue, numericScore, savedReport } from './assessmentView'
import type { FindingSection } from './assessmentView'
import { errorLabel, scoreLabel, stageLabel, timeLabel } from './format'
import FullReport from './FullReport'
import AssessmentInfo from './AssessmentInfo'
import PostInterviewAssessment from './PostInterviewAssessment'
import type { Kind as InsightKind, Workspace as InsightWorkspace } from './PostInterviewAssessment'
import './CandidateAssessment.css'

const title = (value: string) => value.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase())
const date = (value: string) => new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Dhaka', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
type Tab = 'summary' | 'findings' | 'qa' | 'coverage' | 'verify' | InsightKind
type Drawer = 'transcript' | 'history' | 'report' | 'rationale' | 'sources' | null

function Score({ value, compact = false }: { value: number | string | null | undefined; compact?: boolean }) {
  const score = numericScore(value)
  if (score === null) return <span className="ca-unassessed">{compact ? '—' : 'Not assessed'}</span>
  return <span className="ca-score-value"><strong>{formatScoreValue(score)}</strong>{!compact && <span className="ca-meter" aria-hidden="true"><i style={{ width: `${score}%` }} /></span>}</span>
}

export function DrawerPanel({ title: heading, close, children, modal = false, className = '' }: { title: string; close: () => void; children: ReactNode; modal?: boolean; className?: string }) {
  const headingId = useId()
  const panel = useRef<HTMLElement>(null)
  const closeRef = useRef(close)
  useEffect(() => { closeRef.current = close }, [close])
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const workspace = document.querySelector<HTMLElement>('.workspace-shell')
    const wasInert = workspace?.inert ?? false
    if (workspace) workspace.inert = true
    panel.current?.focus()
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    function keydown(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); closeRef.current(); return }
      if (event.key !== 'Tab') return
      const controls = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], select:not(:disabled), input:not(:disabled), summary, [tabindex="0"]') ?? []).filter(element => element.getClientRects().length > 0)
      if (!controls.length) { event.preventDefault(); panel.current?.focus(); return }
      const first = controls[0], last = controls[controls.length - 1]
      if (!controls.includes(document.activeElement as HTMLElement)) { event.preventDefault(); (event.shiftKey ? last : first).focus() }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', keydown)
    return () => {
      document.removeEventListener('keydown', keydown)
      document.body.style.overflow = overflow
      if (workspace) workspace.inert = wasInert
      previous?.focus()
    }
  }, [])
  return createPortal(<div className={`ca-drawer-overlay motion-workspace${modal ? ' ca-modal-overlay' : ''}`} onClick={event => { if (event.target === event.currentTarget) close() }}>
    <aside className={`ca-drawer${modal ? ' ca-modal' : ''} ${className}`} ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={headingId}><header><h2 id={headingId}>{heading}</h2><button type="button" className="ca-button" onClick={close} aria-label={`Close ${heading}`}>Close ×</button></header><div className="ca-dialog-content">{children}</div></aside>
  </div>, document.body)
}

export function EvidenceSources({ sources, onEvidence }: { sources: F6Source[]; onEvidence: (id: string) => void }) {
  return <div className="ca-evidence-list">{!sources.length && <p>No source details are available in this saved result.</p>}{sources.map(source => <article key={source.id}>
    <strong className="ca-small-heading">{source.kind === 'cv' ? 'CV evidence' : source.kind === 'interview' ? `Interview · ${source.dimension ?? 'answer'}` : 'Interview coverage'}</strong>
    <p>{source.justification ?? (source.exploration === 'unasked' ? 'This criterion was not asked about.' : 'This criterion was discussed; discussion does not establish proficiency.')}</p>
    {!!source.unsupported_requirement_parts?.length && <p className="ca-inline-warning">Not documented: {source.unsupported_requirement_parts.join('; ')}</p>}
    {source.evidence?.map((evidence, index) => <div key={index}><blockquote>{evidence.quote}</blockquote>{evidence.turn_id
      ? <button type="button" className="ca-citation interview" onClick={() => onEvidence(evidence.turn_id!)}>INTERVIEW · {timeLabel(evidence.session_start_ms)} · View source</button>
      : <span className="ca-citation">CV · {evidence.source_id ?? 'Saved source'}{evidence.start !== undefined ? ` · chars ${evidence.start}–${evidence.end}` : ''}</span>}</div>)}
  </article>)}</div>
}

function QuestionDetail({ answer, data, onEvidence }: { answer: Answer; data: Result; onEvidence: (id: string) => void }) {
  const group = answer.group
  function speech(ids: string[], empty: string) {
    const selected = data.source_turns.filter(turn => ids.includes(turn.id))
    return selected.length ? selected.map(turn => <div key={turn.id}><blockquote>{turn.text}</blockquote><button type="button" className="ca-citation interview" onClick={() => onEvidence(turn.id)}>INTERVIEW · {turn.speaker_role === 'candidate' ? 'Candidate' : 'Interviewer'} · {timeLabel(turn.session_start_ms)} · View source</button></div>) : <p className="ca-muted">{empty}</p>
  }
  return <div className="ca-question-detail" id={`assessment-question-${group.id}`} tabIndex={-1}><div className="ca-conversation-grid"><section><h4>Question</h4>{speech(group.question_turn_ids, 'No saved question text.')}<h4>Follow-ups and clarifications</h4>{speech([...group.follow_up_turn_ids, ...group.clarification_turn_ids, ...group.revisit_turn_ids], 'None recorded.')}</section><section><h4>Answer</h4>{speech(group.answer_turn_ids, 'No usable candidate answer was recorded.')}</section></div>
    <h4>Why these scores</h4><div className="ca-explanations">{dimensions.map(dimension => {
      const evaluation = answer.evaluations[dimension]
      return <section key={dimension}><p><strong>{title(dimension)} · {scoreLabel(evaluation.score_100)}</strong> {evaluation.justification}</p>
        {evaluation.score === null && <p className="ca-muted">{title(evaluation.reason)}</p>}
        {evaluation.accuracy_warning && <p className="ca-inline-warning">Accuracy includes unverified model knowledge.</p>}
        {!!evaluation.reference_evidence.length && <details><summary>Supporting technical references</summary>{evaluation.reference_evidence.map((reference, index) => <blockquote key={index}>{reference.quote}<small>{reference.filename ?? reference.reference_id} · version {reference.version}</small></blockquote>)}</details>}
      </section>
    })}</div>{answer.reference_retrieval && ['failed', 'partial'].includes(answer.reference_retrieval.status) && <p className="ca-inline-warning">Technical reference retrieval was {answer.reference_retrieval.status}; review the saved evidence limits.</p>}
  </div>
}

export default function CandidateAssessment({ data, token, jobTitle, jobNumber, runs, busy, onBack, onSelect, onRetry, onRecover, onRerun, onConsolidate }: {
  data: Result; token: string; jobTitle: string; jobNumber: number; runs: Run[]; busy: boolean
  onBack: () => void; onSelect: (id: string) => void; onRetry: () => void; onRecover: () => void; onRerun: () => void; onConsolidate: (confirm: boolean) => void
}) {
  const [tab, setTab] = useState<Tab>('summary')
  const [filter, setFilter] = useState<'all' | FindingSection>('all')
  const [expanded, setExpanded] = useState('')
  const [question, setQuestion] = useState('')
  const [drawer, setDrawer] = useState<Drawer>(null)
  const [coverageCriterion, setCoverageCriterion] = useState('')
  const [highlight, setHighlight] = useState('')
  const [reviewed, setReviewed] = useState(false)
  const [insights, setInsights] = useState<InsightWorkspace | null>(null)
  const [insightError, setInsightError] = useState('')
  const [insightKind, setInsightKind] = useState<InsightKind>('interview_performance')
  const moreDetails = useRef<HTMLDetailsElement>(null)
  const finalScore = data.final_score
  const { run } = data
  const report = savedReport(data)
  const findings = findingsFor(report)
  const recommendations = report?.findings.recommended_areas ?? []
  const history = candidateHistory(run, runs)
  const sources = report?.source_registry ?? []
  const cvSources: F6Source[] = data.pinned_cv?.matches?.map((match, index) => ({
    id: `pinned-cv-${index}`, kind: 'cv', criterion_ids: [match.requirement_id], decision: match.decision,
    justification: match.justification, evidence: match.evidence, unsupported_requirement_parts: match.unsupported_requirement_parts,
  })) ?? sources.filter(source => source.kind === 'cv')
  const textOnly = data.source_turns.some(turn => turn.session_start_ms == null) || data.result?.warnings?.some(warning => warning.includes('TEXT_ONLY') || warning.includes('UNVERIFIED_TIMING'))
  const active = ['queued', 'running'].includes(run.status)
  const criterionName = (id: string) => data.criteria.find(criterion => criterion.id === id)?.text ?? 'Saved criterion'
  const sourceList = (ids: string[]) => sources.filter(source => ids.includes(source.id))
  const parentId = run.kind === 'f6' ? run.parent_run_id! : run.id
  const showingInsights = ['cv_claim_review', 'interview_performance', 'interview_quality'].includes(tab)
  const baseCompleted = run.kind === 'f6' || run.status === 'completed'
  function showInsight(kind: InsightKind) { if (moreDetails.current) moreDetails.current.open = false; setInsightKind(kind); setTab(kind) }
  const featureStates: {label: string; status: string; tab: Tab}[] = [
    {label: 'Q&A analysis', status: run.kind === 'f5' ? run.status : 'completed', tab: 'qa'},
    {label: 'Consolidated assessment', status: data.consolidation?.status ?? (report ? 'completed' : 'not_generated'), tab: 'findings'},
    ...(['cv_claim_review', 'interview_performance', 'interview_quality'] as InsightKind[]).map(kind => ({
      label: kind === 'cv_claim_review' ? 'CV claim review' : kind === 'interview_performance' ? 'Interview performance' : 'Interview quality',
      status: insights?.items.find(item => item.run.kind === kind)?.run.status ?? (baseCompleted ? insightError ? 'unavailable' : insights ? 'not_generated' : 'loading' : 'blocked'),
      tab: kind,
    })),
  ]
  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: 'summary', label: 'Summary' }, { id: 'findings', label: 'Assessment', count: report ? findings.length : undefined },
    { id: 'qa', label: 'Interview Q&A', count: data.answers.length }, { id: 'coverage', label: 'Criteria coverage', count: data.criteria.length },
    { id: 'verify', label: 'Verify next', count: report ? recommendations.length : undefined },
    { id: 'cv_claim_review', label: 'CV claims' }, { id: 'interview_performance', label: 'Performance' },
    { id: 'interview_quality', label: 'Interview quality' },
  ]
  useEffect(() => {
    if (drawer !== 'transcript' || !highlight) return
    const frame = requestAnimationFrame(() => { const row = document.getElementById(`assessment-turn-${highlight}`); row?.scrollIntoView({ block: 'center' }); row?.focus({ preventScroll: true }) })
    return () => cancelAnimationFrame(frame)
  }, [drawer, highlight])
  function openPanel(next: Drawer) {
    if (moreDetails.current?.open) { moreDetails.current.open = false; moreDetails.current.querySelector('summary')?.focus() }
    setDrawer(next)
  }
  function evidence(id: string) { setHighlight(id); setDrawer('transcript') }
  function openQuestion(id: string) {
    setQuestion(id); setTab('qa')
    requestAnimationFrame(() => { const detail = document.getElementById(`assessment-question-${id}`); detail?.scrollIntoView({block: 'start'}); detail?.focus({preventScroll: true}) })
  }
  function showFindings(section: 'all' | FindingSection) { setFilter(section); setTab('findings') }
  function findingPreview(section: FindingSection) {
    const items = findings.filter(finding => finding.section === section)
    return <section className="ca-card ca-preview"><h3>{section === 'evidence_gaps' ? 'Evidence gaps' : title(section)} <span className="ca-count">{report ? items.length : '—'}</span></h3>
      {!items.length && <p className="ca-muted">{report ? 'No supported finding saved.' : 'Available after a consolidated assessment is saved.'}</p>}
      {items.slice(0, 4).map(item => <button type="button" key={item.key} onClick={() => { showFindings(section); setExpanded(item.key) }}><strong>{criterionName(item.criterion_id)}</strong><span>{item.text}</span></button>)}
      {!!items.length && <button type="button" className="ca-link" onClick={() => showFindings(section)}>See all {items.length}</button>}
    </section>
  }
  return <div className="candidate-assessment">
    <header className="ca-header"><div><button type="button" className="ca-link ca-back" onClick={onBack}>← Assessments</button><div className="ca-title"><h1>{run.label}</h1><span className={`ca-status ${run.status}`}>{title(run.status)}</span>{run.synthetic && <span className="ca-demo">DEMO · sample data</span>}</div><p>{jobTitle} · JD{jobNumber} · criteria v{run.profile_version} · saved {date(run.created_at)} (Bangladesh time)</p></div>
      <div className="ca-header-actions"><select aria-label="Saved candidate analysis" value={run.id} onChange={event => onSelect(event.target.value)}>{history.map(item => <option key={item.id} value={item.id}>{date(item.created_at)} · {item.kind === 'f6' ? 'Assessment' : 'Q&A'} · {title(item.status)}</option>)}</select>
        <div className="ca-header-info"><AssessmentInfo label="Review notes" warning={textOnly}><p>{textOnly ? 'Transcript timing and speaker order are unverified. Missing discussion is not proof of inability or a CV contradiction.' : 'AI conclusions need human review. CV evidence and interview performance are shown separately.'}</p></AssessmentInfo>
          {run.recovered_from && <AssessmentInfo label="Recovered run"><p>This recovery retains previously validated answers.</p><button type="button" className="ca-link" onClick={() => onSelect(run.recovered_from!)}>View original run</button></AssessmentInfo>}
        </div>
      </div>
    </header>
    {run.synthetic && <p className="ca-source-note ca-muted">Synthetic transcript · authored test timing. Scores and durations describe sample data.</p>}
    {data.outdated && <p className="ca-care">Job criteria have changed. This saved analysis retains its original criteria.</p>}
    {run.error_code && <div className="assessment-error" role="alert"><p>{errorLabel(run.error_code, data.result?.validation_error)}</p>{run.can_retry && <button type="button" className="ca-button" onClick={onRetry}>Retry from saved work</button>}{run.can_recover && <button type="button" className="ca-button" disabled={busy} onClick={onRecover}>Resume from saved work</button>}</div>}
    {active && <div className="ca-progress" role="status"><strong>{stageLabel(run.stage)}</strong><span>{run.completed_groups} / {run.total_groups || '…'} {run.kind === 'f6' ? 'analysis branches' : 'question groups'} saved · attempt {run.attempt}/{run.max_attempts}</span>{run.heartbeat_at && <span>Last activity {date(run.heartbeat_at)} (Bangladesh time)</span>}{run.total_groups > 0 && <progress value={run.completed_groups} max={run.total_groups} />}</div>}
    {run.kind === 'f5' && !run.synthetic && ['failed', 'needs_review'].includes(run.status) && <div className="ca-care"><p>Run Q&amp;A again using the saved transcript and criteria with the current analysis settings. The previous run stays in history.</p><button type="button" className="ca-button" disabled={busy} onClick={onRerun}>{busy ? 'Starting…' : 'Rerun Q&A analysis'}</button></div>}
    <div className="ca-navigation"><nav className="redesign-tabs" aria-label="Candidate assessment sections">{tabs.map(item => <button type="button" key={item.id} aria-current={tab === item.id ? 'page' : undefined} onClick={() => ['cv_claim_review', 'interview_performance', 'interview_quality'].includes(item.id) ? showInsight(item.id as InsightKind) : setTab(item.id)}>{item.label}{item.count !== undefined && <span className="ca-count">{item.count}</span>}</button>)}</nav>
      <div className="ca-page-actions"><button type="button" className="ca-button primary" onClick={() => openPanel('report')}>Report &amp; export</button>
        <details ref={moreDetails} className="ca-more-details" onKeyDown={event => { if (event.key === 'Escape' && moreDetails.current) { moreDetails.current.open = false; moreDetails.current.querySelector('summary')?.focus() } }}><summary className="ca-button">More details</summary><div>
          <button type="button" onClick={() => { setHighlight(''); openPanel('transcript') }}>Source transcript · {data.source_turns.length}</button>
          <button type="button" onClick={() => openPanel('history')}>Run history</button><button type="button" onClick={() => openPanel('sources')}>Source versions</button>
          {report && <button type="button" onClick={() => openPanel('rationale')}>Assessment rationale</button>}
          {run.status === 'completed' && <button type="button" onClick={() => showInsight('interview_performance')}>Post-interview insights</button>}
        </div></details>
      </div></div>
    <div className="ca-content motion-tab-content">
      {tab === 'summary' && <div className="ca-feature-states" aria-label="Analysis status">{featureStates.map(feature => <button type="button" key={feature.label} onClick={() => ['cv_claim_review', 'interview_performance', 'interview_quality'].includes(feature.tab) ? showInsight(feature.tab as InsightKind) : setTab(feature.tab)}><strong>{feature.label}</strong><span className={`ca-status ${feature.status}`}>{title(feature.status)}</span></button>)}</div>}
      {tab === 'summary' && <>
        <div className="ca-summary-scores"><section className="ca-card ca-cv-score" aria-label="Final assessment score">
          <div className="ca-section-heading"><h3>Combined score</h3><span className="ca-finding-badge evidence_gaps">{finalScore?.score_100 != null ? 'Provisional' : 'Incomplete'}</span></div>
          <p className="ca-total">{formatScoreValue(finalScore?.score_100)} <small>/ 100</small></p>
          {finalScore?.score_100 != null && <div className="ca-score-track"><i style={{ width: `${numericScore(finalScore.score_100) ?? 0}%` }} /></div>}
          <p className="ca-muted">{finalScore?.policy.cv_weight ?? 30}% CV screening + {finalScore?.policy.interview_weight ?? 70}% interview performance</p>
          <dl className="ca-component-scores"><div><dt>CV screening</dt><dd>{formatScoreValue(finalScore?.cv_score_100 ?? report?.component_scores.cv.aggregate)}</dd></div><div><dt>Interview performance</dt><dd>{formatScoreValue(finalScore?.interview_score_100)}</dd></div></dl>
          <p className="ca-muted">{finalScore?.reason ?? 'A saved CV score and completed interview performance score are both required.'}</p>
          {finalScore?.interview_created_at && <p className="ca-muted">Performance source: {date(finalScore.interview_created_at)} · Bangladesh time</p>}
          {finalScore?.warnings.map(warning => <p className="ca-inline-warning" key={warning}>{warning}</p>)}
          <details><summary>Score breakdown</summary>{finalScore?.score_100 != null && <p>CV: {finalScore.cv_score_100} × {finalScore.policy.cv_weight}% = {finalScore.cv_contribution} points.<br />Interview: {finalScore.interview_score_100} × {finalScore.policy.interview_weight}% = {finalScore.interview_contribution} points.</p>}{finalScore?.interview_created_at && <p>Latest saved interview performance: {date(finalScore.interview_created_at)} · {finalScore.interview_run_id}.</p>}{report && <><h4>CV screening breakdown</h4>{report.component_scores.cv.dimensions.map(item => <p key={item.dimension}>{title(item.dimension)}: {scoreLabel(item.score)} · {item.approved_percentage}% weight</p>)}<p>{report.component_scores.cv.note}</p></>}</details>
          {run.status === 'completed' && <button type="button" className="ca-link ca-performance-link" onClick={() => showInsight('interview_performance')}>View interview performance</button>}
        </section>
          <section className="ca-card ca-answer-matrix"><h3>Q&A answer scores</h3><p className="ca-muted">Answer-level feedback; the combined score uses the separate six-dimension performance assessment.</p><div className="ca-dimension-summary">{dimensions.map(dimension => { const summary = data.dimension_summaries[dimension]; return summary && <span key={dimension}><strong>{title(dimension)}: {formatScoreValue(summary.score_100)}</strong><small>{summary.assessed_count} assessed · {summary.not_assessable_count} not assessed</small></span> })}</div><p className="ca-muted">{data.answers.length} questions, each scored 0 to 100 on four dimensions. “—” means not assessed, which is not the same as 0.</p><div className="ca-table-scroll"><table><thead><tr><th><span className="sr-only">Dimension</span></th>{data.answers.map((answer, index) => <th key={answer.group.id}>Q{index + 1}</th>)}</tr></thead><tbody>{dimensions.map(dimension => <tr key={dimension}><th scope="row">{title(dimension)}</th>{data.answers.map(answer => <td key={answer.group.id}><button type="button" aria-label={`${answer.group.topic}: ${dimension} ${scoreLabel(answer.evaluations[dimension].score_100)}`} onClick={() => { setQuestion(answer.group.id); setTab('qa') }}><Score compact value={answer.evaluations[dimension].score_100} /></button></td>)}</tr>)}</tbody></table></div>{!data.answers.length && <p className="ca-muted">Validated answers will appear as processing completes.</p>}<button type="button" className="ca-link" onClick={() => setTab('qa')}>Open all questions and answers</button></section></div>
        <div className="ca-preview-grid">{report ? findingSections.map(section => <Fragment key={section}>{findingPreview(section)}</Fragment>) : <section className="ca-card ca-not-generated"><h3>Consolidated assessment {data.consolidation ? title(data.consolidation.status) : 'not generated'}</h3><p className="ca-muted">Strengths, weaknesses and evidence gaps require a validated consolidated result. No finding counts are available yet.</p>{data.consolidation && <button type="button" className="ca-link" onClick={() => onSelect(data.consolidation!.id === run.id ? parentId : data.consolidation!.id)}>{data.consolidation.id === run.id ? 'Open source Q&A analysis' : 'Open saved consolidated run'}</button>}</section>}</div>
        <section className="ca-card ca-verify-preview"><div className="ca-section-heading"><h3>Verify next</h3>{report && <button type="button" className="ca-link" onClick={() => setTab('verify')}>See all {recommendations.length}</button>}</div><div>{recommendations.slice(0, 3).map((item, index) => <button type="button" key={index} onClick={() => setTab('verify')}><span className="ca-number">{index + 1}</span><span><strong>{criterionName(item.criterion_id)}</strong><span>{item.text}</span></span></button>)}</div>{!recommendations.length && <p className="ca-muted">{report ? 'No follow-up prompts were saved.' : 'Follow-up prompts appear after consolidation.'}</p>}</section>

        {run.kind === 'f5' && run.status === 'completed' && !data.consolidation && <section className="ca-card ca-consolidate"><h3>Complete the candidate assessment</h3><p className="ca-muted">Combine this saved Q&A analysis with its pinned CV screening results. Existing source scores are preserved.</p>{data.f6?.ready ? <>{data.f6.review_required && <label className="ca-consolidation-review"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} />I reviewed the saved questions, scores and evidence.</label>}<button type="button" className="ca-button primary" disabled={busy || (data.f6.review_required && !reviewed)} onClick={() => onConsolidate(reviewed)}>{busy ? 'Saving…' : 'Generate consolidated assessment'}</button></> : <p>{data.f6?.blocker ?? 'Consolidation is not ready.'}</p>}{data.f6?.review_required && !reviewed && <p className="ca-muted">Review the saved Q&A and evidence before generating the consolidated assessment.</p>}</section>}

      </>}
      {tab === 'findings' && report && <><section className="ca-card ca-rationale ca-table-note"><h3>Assessment rationale</h3><p className="ca-muted">{title(report.findings.suitability.position)} · Saved conclusion for human review</p><p>{report.findings.suitability.rationale}</p><details><summary>Supporting evidence</summary><EvidenceSources sources={sourceList(report.findings.suitability.source_ids)} onEvidence={evidence} /></details></section><div className="ca-findings-toolbar"><div className="ca-filters">{(['all', ...findingSections] as const).map(section => <button type="button" key={section} aria-pressed={filter === section} onClick={() => setFilter(section)}>{section === 'all' ? 'All' : section === 'evidence_gaps' ? 'Gaps' : title(section)} {section === 'all' ? findings.length : findings.filter(item => item.section === section).length}</button>)}</div><p className="ca-muted">Select a finding to read its explanation and evidence.</p></div><p className="ca-muted ca-source-note">Saved findings can cite CV documents, interview answers and coverage. Each source retains its original context.</p><div className="ca-table-scroll ca-table-card"><table className="ca-findings-table"><thead><tr><th>Type</th><th>Criterion</th><th>Finding</th><th>Evidence</th></tr></thead><tbody>{findings.filter(item => filter === 'all' || item.section === filter).map(item => <Fragment key={item.key}><tr className={expanded === item.key ? 'expanded' : ''}><td><span className={`ca-finding-badge ${item.section}`}>{findingLabels[item.section]}</span></td><th scope="row">{criterionName(item.criterion_id)}</th><td><button type="button" className="ca-row-trigger" aria-expanded={expanded === item.key} onClick={() => setExpanded(expanded === item.key ? '' : item.key)}>{item.text}</button></td><td><button type="button" className="ca-citation" aria-expanded={expanded === item.key} onClick={() => setExpanded(expanded === item.key ? '' : item.key)}>{item.source_ids.length} {item.source_ids.length === 1 ? 'SOURCE' : 'SOURCES'} {expanded === item.key ? '⌄' : '›'}</button></td></tr>{expanded === item.key && <tr className="expanded"><td colSpan={4}><div className="ca-finding-detail"><section><h4>Full finding</h4><p>{item.text}</p></section><section><h4>Evidence</h4><EvidenceSources sources={sourceList(item.source_ids)} onEvidence={evidence} /></section></div></td></tr>}</Fragment>)}</tbody></table></div>{!findings.length && <p className="ca-empty">{report ? 'No findings were saved.' : 'Consolidated findings have not been generated in this view. Interview Q&A and CV claim review are separate sections.'}</p>}</>}
      {tab === 'findings' && !report && <section className="ca-card ca-empty"><h3>Consolidated assessment {data.consolidation ? title(data.consolidation.status) : 'not generated'}</h3><p>Strengths, weaknesses and evidence gaps are unavailable until this analysis completes validation.</p>{data.consolidation ? <button className="ca-link" onClick={() => onSelect(data.consolidation!.id === run.id ? parentId : data.consolidation!.id)}>{data.consolidation.id === run.id ? 'Open source Q&A analysis' : 'Open saved consolidated run'}</button> : <button className="ca-link" onClick={() => setTab('summary')}>Review consolidation requirements in Summary</button>}</section>}
      {tab === 'qa' && <><div className="ca-table-scroll ca-table-card"><table className="ca-qa-table"><thead><tr><th>#</th><th>Question topic and criteria explored</th>{dimensions.map(dimension => <th key={dimension}>{title(dimension)}</th>)}<th><span className="sr-only">Expand</span></th></tr></thead><tbody>{data.answers.map((answer, index) => <Fragment key={answer.group.id}><tr className={question === answer.group.id ? 'expanded' : ''}><th scope="row">Q{index + 1}</th><td><button type="button" className="ca-row-trigger" aria-expanded={question === answer.group.id} onClick={() => setQuestion(question === answer.group.id ? '' : answer.group.id)}><strong>{answer.group.topic}</strong><small>{answer.group.criterion_ids.map(criterionName).join(' · ') || 'No criteria mapped'}</small></button></td>{dimensions.map(dimension => <td key={dimension}><Score value={answer.evaluations[dimension].score_100} /></td>)}<td><button type="button" className="ca-expand" aria-label={`${question === answer.group.id ? 'Collapse' : 'Expand'} question ${index + 1}`} aria-expanded={question === answer.group.id} onClick={() => setQuestion(question === answer.group.id ? '' : answer.group.id)}>{question === answer.group.id ? '⌄' : '›'}</button></td></tr>{question === answer.group.id && <tr className="expanded"><td colSpan={7}><QuestionDetail answer={answer} data={data} onEvidence={evidence} /></td></tr>}</Fragment>)}</tbody></table></div><p className="ca-muted ca-table-note">Scores are 0 to 100. “Not assessed” means there was insufficient usable evidence to score; it is different from 0.</p>{!data.answers.length && <p className="ca-empty">Validated questions and answers will appear as processing completes.</p>}</>}
      {tab === 'coverage' && <><p className="ca-muted ca-table-note">CV evidence and interview coverage are shown side by side. They are not combined into one result, and “Not asked” does not mean weak. “Explored” records discussion, not proficiency.</p><div className="ca-table-scroll ca-table-card"><table className="ca-coverage-table"><thead><tr><th>Criterion</th><th>What the CV shows</th><th>In the interview</th></tr></thead><tbody>{data.criteria.map(criterion => { const cv = cvCoverage(cvSources, criterion.id); const coverage = data.result?.coverage?.find(item => item.criterion_id === criterion.id); const groups = coverage?.group_ids ?? data.answers.filter(answer => answer.group.criterion_ids.includes(criterion.id)).map(answer => answer.group.id); const explored = groups.length > 0; return <Fragment key={criterion.id}><tr><th scope="row">{criterion.text}{criterion.priority && <small className="ca-criterion-priority">{criterion.priority}</small>}</th><td>{cvSources.some(source => source.criterion_ids.includes(criterion.id)) ? <button type="button" className={`ca-finding-badge ${cv.tone}`} aria-label={`CV evidence for ${criterion.text}`} aria-expanded={coverageCriterion === criterion.id} onClick={() => setCoverageCriterion(coverageCriterion === criterion.id ? '' : criterion.id)}>{cv.label} {coverageCriterion === criterion.id ? '⌄' : '›'}</button> : <span className="ca-unassessed">{data.pinned_cv === null ? 'No CV assessment pinned' : cv.label}</span>}</td><td><button type="button" className={explored ? 'ca-finding-badge strengths' : 'ca-unassessed'} onClick={() => { setQuestion(groups[0] ?? ''); setTab('qa') }}>{explored ? `Explored · ${groups.length} ${groups.length === 1 ? 'question' : 'questions'}` : 'Not asked'}</button></td></tr>{coverageCriterion === criterion.id && <tr className="expanded"><td colSpan={3}><div className="ca-coverage-detail"><EvidenceSources sources={cvSources.filter(source => source.criterion_ids.includes(criterion.id))} onEvidence={evidence} /></div></td></tr>}</Fragment> })}</tbody></table></div></>}
      {tab === 'verify' && <><p className="ca-muted ca-table-note">Suggested things to check in your next conversation. They are prompts, not decisions.</p><div className="ca-verification-list">{recommendations.map((item, index) => <article className="ca-card" key={index}><span className="ca-number">{index + 1}</span><div><h3>{criterionName(item.criterion_id)}</h3><p>{item.text}</p><details><summary>Evidence · {item.source_ids.length} sources</summary><EvidenceSources sources={sourceList(item.source_ids)} onEvidence={evidence} /></details></div></article>)}</div>{!recommendations.length && <p className="ca-empty">{report ? 'No verification prompts were saved.' : 'Generate a consolidated assessment to see evidence-backed follow-up prompts.'}</p>}</>}
      {baseCompleted ? <div hidden={!showingInsights} className="ca-insights-panel"><PostInterviewAssessment token={token} parentId={parentId} onEvidence={evidence} reviewConfirmed={reviewed} onReviewChange={setReviewed} embedded visible={showingInsights} activeKind={insightKind} onKindChange={showInsight} onSnapshot={setInsights} onLoadError={setInsightError} contributingPerformanceId={finalScore?.interview_run_id} criteria={data.criteria} questions={data.answers.map(answer => ({id: answer.group.id, topic: answer.group.topic}))} onQuestion={openQuestion} onCoverage={() => setTab('coverage')} onTranscript={() => { setHighlight(''); setDrawer('transcript') }} /></div>
        : showingInsights && <section className="ca-card"><h2>Complete Q&amp;A analysis first</h2><p>Post-interview insights require a completed, validated Q&amp;A result. Saved source evidence is retained.</p></section>}
    </div>
    {drawer && <DrawerPanel modal={['report', 'rationale'].includes(drawer)} title={{transcript: 'Source transcript', history: 'Run history', report: 'Report & export', rationale: 'Assessment rationale', sources: 'Source versions'}[drawer]} close={() => setDrawer(null)}>
      {drawer === 'report' ? <FullReport token={token} runId={run.id} parentId={parentId} completed={run.status === 'completed'} />
        : drawer === 'rationale' && report ? <div className="ca-rationale"><p className="ca-muted">{title(report.findings.suitability.position)} · {run.status === 'completed' ? 'Saved conclusion for human review' : 'Draft awaiting validation'}</p><p>{report.findings.suitability.rationale}</p><EvidenceSources sources={sourceList(report.findings.suitability.source_ids)} onEvidence={evidence} /></div>
        : drawer === 'sources' ? <div className="ca-source-versions"><dl><dt>Selected run</dt><dd>{run.id}</dd><dt>Job criteria</dt><dd>Version {run.profile_version}</dd><dt>Analysis instructions / rubric</dt><dd>{run.prompt_version} / {run.rubric_version}</dd><dt>Q&amp;A source</dt><dd>{parentId}</dd><dt>Pinned CV assessment</dt><dd>{data.pinned_cv?.id ?? report?.provenance.cv_assessment_id ?? 'None available'}</dd><dt>Transcript</dt><dd>{data.source_metadata?.transcript_version ? `Version ${data.source_metadata.transcript_version}` : 'Version unavailable'} · {run.synthetic ? 'Synthetic sample' : 'Recorded interview'}</dd><dt>Timing basis</dt><dd>{data.source_metadata ? title(data.source_metadata.timing_basis) : 'See saved source limitations'}</dd>{report && <><dt>Consolidation repairs used</dt><dd>{report.provenance.repairs_used}</dd></>}</dl></div>
        : drawer === 'transcript' ? <><p className="ca-muted">{textOnly ? 'Saved text order is not verified cross-speaker chronology. Audio gaps may omit speech.' : 'Original segments from the saved assessment source.'}</p>{data.source_turns.map((turn, index) => <div className={`ca-transcript-turn ${highlight === turn.id ? 'highlighted' : ''}`} id={`assessment-turn-${turn.id}`} key={turn.id} tabIndex={-1}><small>Turn {index + 1} · {turn.speaker_role === 'candidate' ? 'Candidate' : 'Interviewer'} · {timeLabel(turn.session_start_ms)}</small><p>{turn.text}</p></div>)}</>
        : <><p className="ca-muted">Saved runs for this candidate in the loaded job history. Selecting a run preserves its original sources and scores.</p><div className="ca-history">{history.map(item => <button type="button" className={item.id === run.id ? 'selected' : ''} key={item.id} onClick={() => { setDrawer(null); onSelect(item.id) }}><strong>{item.kind === 'f6' ? 'Consolidated assessment' : 'Interview Q&A analysis'}</strong><span>{date(item.created_at)} · {title(item.status)} · criteria v{item.profile_version}</span></button>)}</div></>}
    </DrawerPanel>}

  </div>
}
