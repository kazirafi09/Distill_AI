import { useEffect, useRef, useState } from 'react'
import { apiRequest } from '../../services/api'
import { ActionButton, Notice, SectionTabs, StatusBadge } from '../../components/recruitment/RecruitmentUI'
import { errorLabel, timeLabel } from './format'
import { performanceDimensions } from './performanceDimensions'
import AssessmentInfo from './AssessmentInfo'
import type { Run } from './api'
import type { InterviewPolicy, PerformanceDimension } from './InterviewScoringSettings'
import './PostInterviewAssessment.css'

export type Kind = 'cv_claim_review' | 'interview_performance' | 'interview_quality'
type Quote = {turn_id: string; quote: string; speaker_role?: string; session_start_ms?: number | null}
type Claim = {id: string; claim: string; criterion_ids?: string[]; status: string; explanation: string; uncertainty?: string | null; scope_comparison: string; same_context?: boolean; follow_up: string; cv_evidence: {quote: string; source_id: string; start?: number; end?: number}; interview_evidence: Quote[]; search_basis?: {complete_saved_transcript: boolean; turn_count: number}}
type Dimension = {score: number | null; reason: string; justification: string; uncertainty?: string | null; basis: string; evidence: Quote[]; reference_evidence: {reference_id: string; quote: string; filename?: string; version?: string; location?: string; url?: string}[]}
type Finding = {category: string; observation: string; uncertainty?: string | null; improvement: string; group_ids: string[]; evidence: Quote[]}
type Review = {id: string; finding_id: string; action: string; note: string; created_at: string}
type PolicySnapshot = {revision: number; policy_sha256: string; policy: InterviewPolicy}
type Artifact = {artifact_sha256: string; claims?: Claim[]; selection_note?: string; dimensions?: Record<PerformanceDimension, Dimension>;
  summary?: {overall_score_100: string | null; status: string; assessed_dimensions: number; assessed_weight: number; missing_weight: number}; scoring_settings?: PolicySnapshot;
  findings?: Finding[]; process_summary?: string; coverage?: {criterion_id: string; text: string; status: string; group_ids: string[]}[];
  speaking_time?: {status: string; reason?: string; candidate_ms?: number; recruiter_ms?: number; note?: string}; warnings: string[]}
type Item = {run: Omit<Run, 'kind'> & {kind: Kind}; result: Artifact | null; reviews: Review[]; diagnostics?: {attempt: number; code: string; field_path: string; message: string}[]}
type Action = {action: 'start' | 'retry' | 'none'; reason: string; run_id: string | null}
export type Workspace = {items: Item[]; actions?: Record<Kind, Action>; rubric_versions?: Record<Kind, string>; blockers: Record<Kind, string | null>; scoring_settings: PolicySnapshot; review_required: boolean; source_warnings: string[]; prompt_version: string}
const names: Record<Kind, string> = {cv_claim_review: 'CV claim review', interview_performance: 'Interview performance', interview_quality: 'Interview quality'}
const claimLabels: Record<string, string> = {supported_in_interview:'Supported in interview',potential_contradiction:'Potential contradiction',discussed_not_substantiated:'Discussed, not substantiated',not_discussed:'Not discussed',unable_to_assess:'Unable to assess'}
const date = (value: string) => new Date(value).toLocaleString('en-GB', {timeZone: 'Asia/Dhaka'}) + ' (Bangladesh time)'
const label = (text: string) => text.replaceAll('_',' ').replace(/^./, value => value.toUpperCase())
const shortDate = (value: string) => new Intl.DateTimeFormat('en-GB', {timeZone: 'Asia/Dhaka', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}).format(new Date(value))
const descriptions: Record<Kind, string> = {
  cv_claim_review: 'Compare CV statements with what the candidate said in the interview.',
  interview_performance: 'Review demonstrated skills, then open a dimension to understand its score.',
  interview_quality: 'Review the questions and follow-ups to improve the interview process.',
}
type QuestionTopic = {id: string; topic: string}
function questionText(text: string, questions: QuestionTopic[]) {
  return text.replace(/\bgroups?(?=\s+g[1-9]\d*\b)/g, word => word === 'groups' ? 'questions' : 'question')
    .replace(/\bg[1-9]\d*\b/g, id => {
      const index = questions.findIndex(question => question.id === id)
      return index >= 0 ? `Q${index + 1}` : id
    })
}

function PerformanceResults({result, currentPolicy, onEvidence}: {result: Artifact; currentPolicy: PolicySnapshot; onEvidence: (id: string) => void}) {
  if (!result.dimensions || !result.summary) return null
  const summary = result.summary
  return <>
    <div className="post-interview-total pi-score-overview">
      <div><span className="pi-eyebrow">Overall interview score</span><strong>{summary.overall_score_100 === null ? 'Not assessed' : <>{summary.overall_score_100} <small>/ 100</small></>}</strong><p>Interview evidence only. CV score excluded.</p></div>
      <dl><div><dt>Dimensions assessed</dt><dd>{summary.assessed_dimensions} / {performanceDimensions.length}</dd></div><div><dt>Scoring weight covered</dt><dd>{summary.assessed_weight}%</dd></div></dl>
    </div>
    {(summary.missing_weight > 0 || summary.overall_score_100 === null) && <Notice title="Some skills could not be assessed" tone="warning">{summary.missing_weight}% of scoring weight has insufficient evidence. Missing evidence does not count as a zero score.</Notice>}
    <div className="pi-section-heading"><h3>Performance by dimension</h3><span>Open a row for the explanation and evidence</span></div>
    <div className="post-interview-dimensions">{performanceDimensions.filter(name => result.dimensions?.[name]).map(name => {
      const dimension = result.dimensions![name]
      const weight = result.scoring_settings?.policy.weights[name]
      return <article key={name}><details className="pi-dimension"><summary>
        <span className="pi-dimension-name"><strong>{label(name)}</strong><small>{weight == null ? 'Weight unavailable' : `${weight}% of interview score`}</small></span>
        <span className="pi-dimension-score">{dimension.score === null ? <span className="pi-not-assessed">Not assessed</span> : <><strong>{dimension.score * 25}<small> / 100</small></strong><progress aria-label={`${label(name)} score`} max={100} value={dimension.score * 25} /></>}</span>
        <span className="pi-disclosure-icon" aria-hidden="true">⌄</span>
      </summary><div className="pi-dimension-body"><h4>Why this score</h4><p>{dimension.justification}</p>
        {dimension.score === null && <p className="pi-evidence-basis">{label(dimension.reason)}</p>}
        <p className="pi-evidence-basis">{dimension.score === null ? 'No rating assigned' : `Saved rating: ${dimension.score} of 4`}{dimension.basis === 'interview_only' ? ' · Based on interview excerpts' : dimension.basis === 'saved_reference_and_interview' ? ' · Interview and saved technical references' : ''}</p>
        {dimension.uncertainty && <Notice title="Evidence needs verification" tone="warning">{dimension.uncertainty}</Notice>}
        {dimension.basis === 'model_knowledge' && <p className="pi-knowledge-note">Technical accuracy was judged using model knowledge. No saved technical reference was cited.</p>}
        <h4>Supporting evidence <span className="pi-subtle">({dimension.evidence.length} interview {dimension.evidence.length === 1 ? 'excerpt' : 'excerpts'})</span></h4>
        {!dimension.evidence.length && <p className="assessment-muted">No usable interview excerpt was saved for this dimension.</p>}
        <Evidence quotes={dimension.evidence} onEvidence={onEvidence} />
        {dimension.reference_evidence.map((reference,index) => <blockquote key={`${reference.reference_id}-${index}`}><small>{reference.filename ?? reference.reference_id}{reference.version && ` · version ${reference.version}`}{reference.location && ` · ${reference.location}`}</small>{reference.url && /^https?:\/\//i.test(reference.url) && <a href={reference.url} target="_blank" rel="noreferrer">Open reference</a>}<p>“{reference.quote}”</p></blockquote>)}
      </div></details></article>
    })}</div>
    {result.scoring_settings && <details className="pi-method"><summary>How this score is calculated</summary><p>Six dimensions are weighted using the saved job settings. Each rating runs from 0 to 4 and is displayed out of 100. These ratings are separate from the four answer-level Q&amp;A scores.</p><p>Scoring revision {result.scoring_settings.revision} · minimum {result.scoring_settings.policy.minimum_assessed_dimensions} assessed dimensions · required: {result.scoring_settings.policy.essential_dimensions.map(label).join(', ') || 'None'}.</p>{result.scoring_settings.policy_sha256 !== currentPolicy.policy_sha256 && <p className="pi-knowledge-note">Job settings have changed. This saved result keeps its original scoring policy.</p>}</details>}
  </>
}

function QualityResults({result, synthetic, onEvidence, questions, onQuestion}: {result: Artifact; synthetic: boolean; onEvidence: (id: string) => void; questions: QuestionTopic[]; onQuestion?: (id: string) => void}) {
  const coverage = result.coverage ?? []
  const explored = coverage.filter(item => item.group_ids.length > 0).length
  const findings = result.findings ?? []
  return <>
    <section className="pi-process-overview"><h3>Interview overview</h3><p>{questionText(result.process_summary ?? '', questions)}</p><p className="assessment-muted">This reviews the interview process, not the candidate’s ability.</p></section>
    {coverage.length > 0 && <section className="pi-coverage-overview"><div className="pi-section-heading"><h3>Job criteria discussed</h3><strong>{explored} / {coverage.length}</strong></div><p>{coverage.length - explored === 0 ? 'Every saved job criterion was discussed.' : `${coverage.length - explored} criteria were not discussed.`} Discussion does not establish proficiency.</p>
      <details className="pi-method"><summary>View coverage of {coverage.length} job criteria</summary><div className="pi-quality-coverage">{coverage.map(item => <div key={item.criterion_id}><span>{item.text}</span><strong className={item.group_ids.length ? 'pi-covered' : 'pi-not-assessed'}>{item.group_ids.length ? `${item.group_ids.length} ${item.group_ids.length === 1 ? 'question' : 'questions'}` : 'Not discussed'}</strong></div>)}</div></details></section>}
    <div className="pi-section-heading"><h3>Observations &amp; next steps</h3><span>{findings.length} saved {findings.length === 1 ? 'observation' : 'observations'}</span></div>
    <div className="pi-quality-findings">{findings.map((finding, index) => <article className="post-interview-finding" key={index}><div className="pi-finding-heading"><span className="pi-finding-index">{index + 1}</span><h4>{label(finding.category)}</h4></div><p className="pi-observation">{questionText(finding.observation, questions)}</p>
      {finding.uncertainty && <Notice title="Evidence needs verification" tone="warning">{finding.uncertainty}</Notice>}
      <div className="pi-next-step"><strong>For the next interview</strong><p>{questionText(finding.improvement, questions)}</p></div>
      <details className="pi-evidence-disclosure"><summary>View evidence · {finding.evidence.length} {finding.evidence.length === 1 ? 'excerpt' : 'excerpts'}</summary>
        <div className="pi-question-links"><h5>Related questions</h5>{finding.group_ids.map(id => {const index = questions.findIndex(question => question.id === id); const question = questions[index]; return onQuestion && question ? <button type="button" key={id} onClick={() => onQuestion(id)}>Q{index + 1} · {question.topic} →</button> : <span key={id}>{question?.topic ?? 'Saved interview question'}</span>})}</div>
        <Evidence quotes={finding.evidence} onEvidence={onEvidence} />
      </details></article>)}</div>
    {!findings.length && <p className="assessment-muted">No process observations were saved.</p>}
    {result.speaking_time && <details className="pi-method pi-timing"><summary>{synthetic || result.speaking_time.status === 'synthetic' ? 'Authored synthetic timing' : 'Speaking time'}</summary>{['measured','synthetic'].includes(result.speaking_time.status) ? <><dl><div><dt>Candidate</dt><dd>{((result.speaking_time.candidate_ms ?? 0)/60000).toFixed(1)} minutes</dd></div><div><dt>Interviewer</dt><dd>{((result.speaking_time.recruiter_ms ?? 0)/60000).toFixed(1)} minutes</dd></div></dl><p>{synthetic ? 'Authored test intervals, not recorded speech or microphone measurements.' : result.speaking_time.note}</p></> : <p>{result.speaking_time.reason}</p>}</details>}
  </>
}

function Evidence({quotes, onEvidence}: {quotes: Quote[]; onEvidence: (id: string) => void}) {
  return <>{quotes.map((quote, index) => <blockquote key={`${quote.turn_id}-${index}`}><small>{quote.speaker_role === 'recruiter' ? 'Interviewer' : quote.speaker_role === 'candidate' ? 'Candidate' : 'Saved interview quote'} · {timeLabel(quote.session_start_ms ?? null)}</small><p>“{quote.quote}”</p><button type="button" onClick={() => onEvidence(quote.turn_id)}>View in source transcript</button></blockquote>)}</>
}

function ClaimReview({item, token, onRefresh, onEvidence, criteria}: {criteria: {id: string; text: string}[]; item: Item; token: string; onRefresh: () => void; onEvidence: (id: string) => void}) {
  const [filter, setFilter] = useState<'all' | 'follow_up'>('all')
  const [notes, setNotes] = useState<Record<string,string>>({})
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const lock = useRef(false)
  async function review(id: string, action: 'reviewed' | 'clarification') {
    if(lock.current) return
    lock.current = true; setBusy(id); setError('')
    try {
      await apiRequest(`/assessments/runs/${item.run.id}/findings/${id}/reviews`, {token, method:'POST',body:{action,note:notes[id] ?? ''}})
      setNotes(current => ({...current,[id]:''})); onRefresh()
    } catch(caught) {setError(caught instanceof Error ? caught.message : 'Could not save review.')}
    finally {lock.current = false; setBusy('')}
  }
  const claims = item.result?.claims ?? []
  const followUp = claims.filter(claim => claim.status !== 'supported_in_interview')
  return <><p className="assessment-muted">{item.result?.selection_note}</p><div className="pi-claim-filters" role="group" aria-label="Filter CV claims"><button type="button" aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>All claims · {claims.length}</button><button type="button" aria-pressed={filter === 'follow_up'} onClick={() => setFilter('follow_up')}>Follow up · {followUp.length}</button></div>
    <Notice title="Prompts for human follow-up" tone="info">Interview support remains self-report. Potential contradictions need contextual review and never trigger an automatic rejection.</Notice>
    {error && <p role="alert" className="assessment-error">{error}</p>}
    {(filter === 'all' ? claims : followUp).map(claim => <article className="post-interview-finding" key={claim.id}>
      <StatusBadge tone={claim.status === 'supported_in_interview' ? 'success' : claim.status === 'potential_contradiction' ? 'warning' : 'neutral'}>{claimLabels[claim.status] ?? label(claim.status)}</StatusBadge>
      <h4>{claim.claim}</h4>{!!claim.criterion_ids?.length && <p className="assessment-muted">Criteria: {claim.criterion_ids.map(id => criteria.find(criterion => criterion.id === id)?.text ?? id).join('; ')}</p>}<p>{claim.explanation}</p><p className="assessment-muted">Context: {claim.scope_comparison}</p>
      {claim.uncertainty && <Notice title="Evidence needs verification" tone="warning">{claim.uncertainty}</Notice>}
      <details><summary>CV and interview evidence</summary><blockquote><small>CV · {claim.cv_evidence.source_id}</small><p>“{claim.cv_evidence.quote}”</p>{claim.cv_evidence.start !== undefined && <small>Characters {claim.cv_evidence.start}–{claim.cv_evidence.end}</small>}</blockquote><p className="assessment-muted">{claim.same_context === true ? 'Same context established.' : claim.same_context === false ? 'Same context not established.' : 'Context comparison unavailable.'} {claim.search_basis?.complete_saved_transcript && `Reviewed all ${claim.search_basis.turn_count} saved transcript turns.`}</p>
        <Evidence quotes={claim.interview_evidence} onEvidence={onEvidence} />{!claim.interview_evidence.length && <p className="assessment-muted">No supporting quote asserted. The complete saved transcript was supplied for review; missing discussion is not a failed claim.</p>}</details>
      <p><strong>Follow up:</strong> {claim.follow_up}</p>
      <details><summary>Recruiter review · {item.reviews.filter(review => review.finding_id === claim.id).length} saved</summary>
        {item.reviews.filter(review => review.finding_id === claim.id).map(review => <div className="post-interview-review" key={review.id}><strong>{label(review.action)}</strong><small>{date(review.created_at)}</small><p>{review.note || 'Reviewed without clarification.'}</p></div>)}
        <label className="post-interview-note">Clarification<textarea rows={3} maxLength={2000} value={notes[claim.id] ?? ''} disabled={!!busy} onChange={event => setNotes({...notes,[claim.id]:event.target.value})} /></label>
        <div className="post-interview-actions"><ActionButton disabled={!!busy} onClick={() => void review(claim.id,'reviewed')}>{busy === claim.id ? 'Saving…' : 'Mark reviewed'}</ActionButton>
          <ActionButton disabled={!!busy || !notes[claim.id]?.trim()} onClick={() => void review(claim.id,'clarification')}>Add clarification</ActionButton></div>
      </details>
    </article>)}
    {filter === 'follow_up' && !followUp.length && <p>No claims were flagged for follow-up in this saved result.</p>}
    {item.result?.claims?.length === 0 && <p>No substantive role-relevant CV claims were selected for review.</p>}
  </>
}

export default function PostInterviewAssessment({token, parentId, onEvidence, reviewConfirmed, onReviewChange, embedded = false, activeKind, onKindChange, onSnapshot, onLoadError, contributingPerformanceId, criteria = [], questions = [], onQuestion, onCoverage, onTranscript, visible = true}: {token: string; parentId: string; onEvidence: (id: string) => void; reviewConfirmed?: boolean; onReviewChange?: (value: boolean) => void; embedded?: boolean; activeKind?: Kind; onKindChange?: (value: Kind) => void; onSnapshot?: (value: Workspace) => void; onLoadError?: (message: string) => void; contributingPerformanceId?: string; criteria?: {id: string; text: string}[]; questions?: QuestionTopic[]; onQuestion?: (id: string) => void; onCoverage?: () => void; onTranscript?: () => void; visible?: boolean}) {
  const [data, setData] = useState<Workspace | null>(null)
  const [internalTab, setInternalTab] = useState<Kind>('interview_performance')
  const tab = activeKind ?? internalTab
  const setTab = (value: string) => onKindChange ? onKindChange(value as Kind) : setInternalTab(value as Kind)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reviewed, setReviewed] = useState(false)
  const [revision, setRevision] = useState(0)
  const [selectedRuns, setSelectedRuns] = useState<Partial<Record<Kind,string>>>({})
  const lock = useRef(false)
  const mounted = useRef(true)
  const panel = useRef<HTMLElement>(null)
  useEffect(() => {
    if (!visible) return
    panel.current?.closest('.ca-content')?.scrollTo({top: 0})
    const revealSection = () => panel.current?.closest('.candidate-assessment')?.querySelector('.ca-navigation button[aria-current]')?.scrollIntoView({block: 'nearest', inline: 'nearest'})
    revealSection()
    window.addEventListener('resize', revealSection)
    return () => window.removeEventListener('resize', revealSection)
  }, [tab, visible])
  useEffect(() => {mounted.current = true; return () => {mounted.current = false}},[])
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const result = await apiRequest<Workspace>(`/assessments/runs/${parentId}/post-interview`,{token,signal:controller.signal,cache:'no-store'})
        if(controller.signal.aborted) return
        setData(result); setError(''); onLoadError?.(''); onSnapshot?.(result); timer = setTimeout(() => void poll(),result.items.some(item => ['queued','running'].includes(item.run.status)) ? 3000 : 15000)
      } catch(caught) {if(!controller.signal.aborted) {const message = caught instanceof Error ? caught.message : 'Could not load post-interview analysis.'; setError(message); onLoadError?.(message); timer = setTimeout(() => void poll(),15000)}}
    }
    void poll(); return () => {controller.abort(); clearTimeout(timer)}
  },[token,parentId,revision,onSnapshot,onLoadError])
  const confirmed = reviewConfirmed ?? reviewed
  function actionFor(kind: Kind): Action {
    if(data?.actions?.[kind]) return data.actions[kind]
    const latest = data?.items.find(value => value.run.kind === kind)
    return {action: data?.blockers[kind] ? 'none' : !latest ? 'start' : latest.run.can_retry ? 'retry' : 'none', run_id: latest?.run.id ?? null,
      reason: data?.blockers[kind] ?? (latest?.run.status === 'needs_review' ? 'Validation attempts are exhausted. These inputs cannot be retried.' : latest ? 'A saved run already exists for these sources.' : 'Generate this analysis.')}
  }
  async function start(kind?: Kind) {
    if(lock.current) return
    lock.current = true; setBusy(true); setError('')
    try {
      const requested = (kind ? [kind] : Object.keys(names) as Kind[]).filter(value => actionFor(value).action !== 'none')
      for(const value of requested.filter(value => actionFor(value).action === 'retry')) {
        await apiRequest(`/assessments/runs/${actionFor(value).run_id}/retry`,{token,method:'POST'})
      }
      const features = requested.filter(value => actionFor(value).action === 'start')
      if(features.length) {
        const result = await apiRequest<{blockers: Record<string,string>}>(`/assessments/runs/${parentId}/post-interview`,{token,method:'POST',body:{features,confirm_f5_review:confirmed}})
        if(mounted.current && Object.keys(result.blockers).length) setError([...new Set(Object.values(result.blockers))].join(' '))
      }
      if(mounted.current) {setSelectedRuns(current => kind ? ({...current,[kind]:undefined}) : {}); setRevision(value => value + 1)}
    } catch(caught) {if(mounted.current) setError(caught instanceof Error ? caught.message : 'Could not start analysis.')}
    finally {lock.current = false; if(mounted.current) setBusy(false)}
  }
  const kind = tab as Kind
  const item = data?.items.find(value => value.run.kind === kind && value.run.id === selectedRuns[kind]) ?? data?.items.find(value => value.run.kind === kind)
  const result = item?.result
  const older = data?.items.filter(value => value.run.kind === kind && value.run.id !== item?.run.id) ?? []
  const available = (Object.keys(names) as Kind[]).filter(value => actionFor(value).action !== 'none')
  const action = actionFor(kind)
  return <section ref={panel} className="post-interview-assessment">
    <header className="pi-header"><div><h2>{embedded ? names[kind] : 'Post-interview insights'}</h2><p>{descriptions[kind]}</p></div>
      {!!data?.source_warnings?.length && <AssessmentInfo label="Transcript limitations" warning>{data.source_warnings.map(warning => <p key={warning}>{warning}</p>)}</AssessmentInfo>}
    </header>
    {!embedded && <SectionTabs tabs={Object.entries(names).map(([id,name]) => ({id,label:name}))} active={tab} onChange={setTab} />}
    {error && <p role="alert" className="assessment-error">{error}</p>}
    {!data && !error && <p role="status">Loading saved insights…</p>}
    {data && <>
      <div className="pi-result-toolbar">
        <div>{item && <StatusBadge tone={item.run.status === 'completed' ? 'success' : ['failed','needs_review'].includes(item.run.status) ? 'warning' : 'info'}>{label(item.run.status)}</StatusBadge>}</div>
        {item && (older.length ? <label className="post-interview-version">Saved analysis<select value={item.run.id} onChange={event => setSelectedRuns({...selectedRuns,[kind]:event.target.value})}>{data.items.filter(value => value.run.kind === kind).map((value,index,items) => <option key={value.run.id} value={value.run.id}>Result {items.length - index} · {shortDate(value.run.created_at)} · {label(value.run.status)}</option>)}</select></label> : <span className="pi-saved-at">Saved {shortDate(item.run.created_at)} · Bangladesh time</span>)}
      </div>
      {item && ((data.prompt_version && item.run.prompt_version !== data.prompt_version) || (data.rubric_versions?.[kind] && item.run.rubric_version !== data.rubric_versions[kind])) && <Notice title="Earlier analysis" tone="info">This result uses earlier evidence checks. {action.action === 'start' ? 'An updated analysis is available with the current settings.' : 'Its original result and validation limits are retained.'}</Notice>}
      {available.length > 0 && data.review_required && (reviewConfirmed === undefined || onReviewChange) && <label className="post-interview-confirm"><input type="checkbox" checked={confirmed} onChange={event => onReviewChange ? onReviewChange(event.target.checked) : setReviewed(event.target.checked)} />I reviewed the saved questions, scores and evidence.</label>}
      {available.length > 0 && data.review_required && !confirmed && reviewConfirmed !== undefined && !onReviewChange && <p className="assessment-muted">Confirm that you reviewed the saved Q&amp;A before starting another analysis.</p>}
      {(action.action !== 'none' || available.length > 1) && <div className="post-interview-actions">{action.action !== 'none' && <ActionButton variant="primary" disabled={busy || (data.review_required && !confirmed)} onClick={() => void start(kind)}>{busy ? 'Saving…' : action.action === 'retry' ? 'Retry saved analysis' : `Run ${names[kind].toLowerCase()}`}</ActionButton>}{available.length > 1 && <ActionButton disabled={busy || (data.review_required && !confirmed)} onClick={() => void start()}>{busy ? 'Saving…' : 'Run all available insights'}</ActionButton>}</div>}
      {data.blockers[kind] && <Notice title="Analysis is not ready" tone="warning">{data.blockers[kind]}</Notice>}
      {!item && !data.blockers[kind] && <Notice title="Not generated">Run this analysis to see {kind === 'cv_claim_review' ? 'how CV statements compare with the interview' : kind === 'interview_performance' ? 'scores across six performance dimensions' : 'observations about the interview process'}.</Notice>}
      {item?.run.error_code && <section className="pi-unavailable" aria-label="Analysis needs attention">
        <div className="pi-unavailable-heading"><span aria-hidden="true">!</span><div><h3>{kind === 'cv_claim_review' ? 'CV comparison unavailable' : 'This analysis needs attention'}</h3><p>{item.run.error_code === 'EXTENSION_OUTPUT_NEEDS_REVIEW' ? 'The generated findings could not be validated against the saved evidence. No findings were published.' : errorLabel(item.run.error_code)}</p></div></div>
        <p className="pi-retained-results">{kind === 'cv_claim_review' ? 'Your saved CV screening and Q&A results are still available.' : 'Your saved Q&A results and original source evidence are still available.'}</p>
        <div className="post-interview-actions">{onCoverage && <ActionButton onClick={onCoverage}>Review CV evidence</ActionButton>}{onTranscript && <ActionButton onClick={onTranscript}>Open transcript</ActionButton>}</div>
        <details className="pi-validation-details"><summary>View validation details</summary><p>{action.reason}</p>{item.diagnostics?.map(diagnostic => <div key={diagnostic.attempt}><strong>Attempt {diagnostic.attempt}</strong><p>{diagnostic.message}</p><code>{diagnostic.field_path}</code></div>)}{!item.diagnostics?.length && <p>No detailed diagnostic was saved for this run.</p>}</details>
      </section>}
      {item && ['queued','running'].includes(item.run.status) && <Notice title={item.run.status === 'queued' ? 'Waiting to start' : 'Analysis in progress'} tone="info"><p>{label(item.run.stage)} · attempt {item.run.attempt}/{item.run.max_attempts}.</p><p>{item.run.heartbeat_at ? `Last activity ${shortDate(item.run.heartbeat_at)} (Bangladesh time).` : 'No worker heartbeat saved yet.'} You can review other sections while this runs.</p></Notice>}
      {kind === 'cv_claim_review' && item && result && <ClaimReview key={item.run.id} item={item} token={token} onRefresh={() => setRevision(value => value + 1)} onEvidence={onEvidence} criteria={criteria} />}
      {kind === 'interview_performance' && result && <>
        {contributingPerformanceId && item?.run.id !== contributingPerformanceId && <Notice title="Historical performance result">The combined score uses a different saved performance run. Viewing this result does not change that score.</Notice>}
        <PerformanceResults key={item?.run.id} result={result} currentPolicy={data.scoring_settings} onEvidence={onEvidence} />
      </>}
      {kind === 'interview_quality' && result && <QualityResults key={item?.run.id} result={result} synthetic={item?.run.synthetic ?? false} onEvidence={onEvidence} questions={questions} onQuestion={onQuestion} />}
      {item && <details className="pi-method pi-run-details"><summary>Analysis details &amp; history</summary><dl><dt>Saved</dt><dd>{date(item.run.created_at)}</dd><dt>Run ID</dt><dd>{item.run.id}</dd><dt>Evidence checks / rubric</dt><dd>{item.run.prompt_version} / {item.run.rubric_version}</dd></dl><p>{action.reason}</p>{result?.warnings.map(warning => <p key={warning}>{warning}</p>)}{older.length > 0 && <><h4>Other saved results</h4>{older.map(previous => <button type="button" className="pi-history-link" key={previous.run.id} onClick={() => setSelectedRuns({...selectedRuns,[kind]:previous.run.id})}>{shortDate(previous.run.created_at)} · {label(previous.run.status)}{previous.result?.summary?.overall_score_100 != null ? ` · ${previous.result.summary.overall_score_100}/100` : ''}</button>)}</>}</details>}
    </>}
  </section>
}
