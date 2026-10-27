import { useCallback, useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import { Link } from 'react-router-dom'
import { loadRecruitmentJobs } from '../interviews/useRecruitmentJob'
import { getJobDescription } from './api'
import type { JobDescriptionSummary, SavedJobDescription, ScreeningProfile } from './api'
import type { AssistantConversation } from './assistantApi'
import RecruiterChat from './RecruiterChat'
import type { RecruiterChatHandle } from './RecruiterChat'
import ReferenceDocuments from './ReferenceDocuments'
import JobEditor from './JobEditor'
import JobSuggestions, { UnapprovedSuggestions } from './JobSuggestions'
import type { JobEditorTarget } from './JobEditor'
import type { JDConversation } from './api'
import './JobDescriptions.css'

type Tab = 'description' | 'criteria' | 'assistant' | 'suggestions'

const dimensionDetails: Record<string, { label: string; description: string }> = {
  technical_skills: { label: 'Technical skills', description: 'Tools, technologies and practical capabilities' },
  work_experience: { label: 'Work experience', description: 'Relevant roles, responsibilities and experience' },
  qualifications: { label: 'Qualifications', description: 'Education, certifications and credentials' },
  additional_criteria: { label: 'Additional criteria', description: 'Other requirements defined for this role' },
}

function Criteria({ profile }: { profile: ScreeningProfile | null }) {
  if (!profile) return <div className="ui-state"><h3>Screening criteria are not set up</h3><p>Ask the assistant to define the requirements and weights for this job.</p></div>
  const total = Object.values(profile.dimension_weights).reduce((sum, weight) => sum + weight, 0)
  const validTotal = Math.abs(total - 100) < 0.001
  return <div className="jd-criteria">
    <h3>Screening criteria</h3><p className="muted">Essential criteria are shown for recruiter review. Nobody is rejected automatically.</p>
    <div className="criteria-table-scroll"><table className="criteria-table"><thead><tr><th>Criterion</th><th>Dimension</th><th>Priority</th></tr></thead>
      <tbody>{profile.criteria.map(criterion => <tr key={criterion.id}><td><strong>{criterion.text}</strong>{criterion.source === 'suggested' && <small>AI suggested · review before approving</small>}</td><td>{criterion.dimension.replaceAll('_', ' ')}</td><td><span className={`ui-badge ${criterion.priority === 'Preferred' ? 'is-warning' : 'is-info'}`}>{criterion.priority === 'Must have' ? 'Essential' : criterion.priority ?? 'Unconfirmed'}</span></td></tr>)}</tbody></table></div>
    <section className="jd-weights" aria-label="Dimension weights">
      <header className="jd-weights-heading"><div><h3>Dimension weights</h3><p>How each area contributes to the overall candidate score.</p></div>
        <span className={`jd-weights-total${validTotal ? '' : ' is-invalid'}`}><span aria-hidden="true">{validTotal ? '✓' : '!'}</span> {total}% <span>of 100% allocated</span></span>
      </header>
      <div className="jd-weights-grid">{Object.entries(profile.dimension_weights).map(([dimension, weight]) => {
        const details = dimensionDetails[dimension] ?? { label: dimension.replaceAll('_', ' '), description: 'Requirements defined for this role' }
        const count = profile.criteria.filter(criterion => criterion.dimension === dimension).length
        return <div className={`jd-weight-card${weight === 0 ? ' is-zero' : ''}`} data-dimension={dimension} key={dimension}>
          <div className="jd-weight-top"><h4><span className="jd-weight-dot" aria-hidden="true" />{details.label}</h4><strong>{weight}<span>%</span></strong></div>
          <p className="jd-weight-description">{details.description}</p>
          <progress max="100" value={weight} aria-label={`${details.label} weight`} />
          <div className="jd-weight-meta"><span>{count} {count === 1 ? 'criterion' : 'criteria'}</span><span>{weight === 0 ? 'No score contribution' : `${weight}% of overall score`}</span></div>
        </div>
      })}</div>
      {!validTotal && <p className="ui-error">Weights must total 100%. Adjust them before approving.</p>}
    </section>
  </div>
}

function ApprovalReview({ disabled, errors, profile, onApprove }: { disabled: boolean; errors: string[]; profile: ScreeningProfile | null; onApprove: () => void }) {
  const [checks, setChecks] = useState<boolean[]>([false, false, false])
  const valid = !!profile && Math.abs(Object.values(profile.dimension_weights).reduce((sum, value) => sum + value, 0) - 100) < 0.001
  return <aside className="jd-context-card"><h3>Before you can approve</h3>
    {['Read the job description', 'Confirm the essential criteria are the real must-haves', 'Check each criterion and the dimension weights'].map((label, index) => <label className="jd-review-check" key={label}><input type="checkbox" checked={checks[index]} onChange={event => setChecks(current => current.map((value, i) => i === index ? event.target.checked : value))} />{label}</label>)}
    {errors.length > 0 && <ul className="ui-error">{errors.map(error => <li key={error}>{error}</li>)}</ul>}
    {!valid && <p className="ui-error">Screening criteria and weights totaling 100% are required.</p>}
    <button className="button-primary" disabled={disabled || !checks.every(Boolean) || !valid || errors.length > 0} onClick={onApprove}>Approve and save</button>
    <p className="muted">Approval saves the job description together with the displayed screening criteria.</p>
  </aside>
}

export default function JobDescriptions({ token, userId }: { token: string; userId: string }) {
  const chat = useRef<RecruiterChatHandle>(null)
  const referenceDialog = useRef<HTMLDialogElement>(null)
  const [tab, setTab] = useState<Tab>('description')
  const [selected, setSelected] = useState('')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('all')
  const [revision, setRevision] = useState(0)
  const [library, setLibrary] = useState<{ jobs: JobDescriptionSummary[]; error: string; loading: boolean }>({ jobs: [], error: '', loading: true })
  const [detail, setDetail] = useState<{ id: string; job?: SavedJobDescription; error?: string } | null>(null)
  const [assistant, setAssistant] = useState<AssistantConversation | null>(null)
  const [busy, setBusy] = useState(false)
  const [editor, setEditor] = useState<JobEditorTarget | null>(null)
  const [editNotice, setEditNotice] = useState('')
  const onStateChange = useCallback((conversation: AssistantConversation | null, working: boolean) => {
    setAssistant(conversation); setBusy(working)
    const savedId = conversation?.jd_conversation?.job_description?.id
    if (savedId) setSelected(current => current === 'draft' ? savedId : current)
  }, [])
  const onSaved = useCallback(() => setRevision(value => value + 1), [])
  const activeId = selected || library.jobs[0]?.id || ''
  const jd = assistant?.jd_conversation
  const draft = !!jd && !!jd.draft_jd && jd.status !== 'approved' && jd.status !== 'cancelled'
  const showingDraft = activeId === 'draft'
  const document = detail?.id === activeId ? detail.job : undefined
  const profile = showingDraft ? jd?.screening_profile ?? null : document?.screening_profile ?? null
  const content = showingDraft ? jd?.draft_jd : document?.content
  const title = showingDraft ? (jd?.draft_jd?.match(/^#\s+(.+)$/m)?.[1] ?? 'Job description draft') : document?.title ?? library.jobs.find(job => job.id === activeId)?.title
  const visibleJobs = library.jobs.filter(job => `${job.title} ${job.number}`.toLowerCase().includes(query.toLowerCase()) && status !== 'draft')
  const editingLocked = showingDraft ? Boolean(jd?.editing_locked)
    : Boolean(document?.editing_locked || (jd?.job_description?.id === activeId && jd?.editing_locked))
  const editVersion = showingDraft ? jd?.edit_version ?? jd?.checkpoint_id : document?.edit_version

  function beginEdit() {
    if (!editVersion || busy || editingLocked || !content) return
    setEditNotice('')
    setEditor({
      conversationId: showingDraft ? jd!.conversation_id : document!.conversation_id,
      editVersion, content: (showingDraft ? jd?.editable_content : document?.editable_content) ?? content,
      profile, approved: !showingDraft, section: tab === 'criteria' ? 'criteria' : 'description',
    })
  }

  function finishEditing(updated: JDConversation) {
    setEditor(null)
    setEditNotice('Changes saved. The assistant will use the updated description and screening criteria.')
    if (showingDraft) {
      setAssistant(current => current ? { ...current, jd_conversation: updated } : current)
      chat.current?.reload()
    } else if (document) {
      onSaved()
      if (assistant?.jd_conversation?.job_description?.id === document.id) chat.current?.reload()
    }
  }

  function cancelEditing() {
    setEditor(null)
    if (showingDraft) chat.current?.reload()
    else onSaved()
  }

  useEffect(() => {
    const controller = new AbortController()
    void loadRecruitmentJobs(token, controller.signal).then(jobs => {
      if (!controller.signal.aborted) setLibrary({ jobs, error: '', loading: false })
    }).catch(error => { if (!controller.signal.aborted) setLibrary({ jobs: [], error: error instanceof Error ? error.message : 'Could not load jobs.', loading: false }) })
    return () => controller.abort()
  }, [token, revision])

  useEffect(() => {
    if (!activeId || activeId === 'draft') return
    const controller = new AbortController()
    void getJobDescription(token, activeId, controller.signal).then(job => {
      if (!controller.signal.aborted) setDetail({ id: activeId, job })
    }).catch(error => { if (!controller.signal.aborted) setDetail({ id: activeId, error: error instanceof Error ? error.message : 'Could not load the job.' }) })
    return () => controller.abort()
  }, [token, activeId, revision])

  function selectJob(id: string) {
    setEditNotice('')
    setSelected(id)
    if (tab === 'assistant' && id !== 'draft') chat.current?.openJob(id)
  }
  function changeTab(next: Tab) {
    if (next === 'assistant' && activeId && activeId !== 'draft' && jd?.job_description?.id !== activeId) chat.current?.openJob(activeId)
    if (next !== 'assistant' && tab === 'assistant') {
      if (draft) setSelected('draft')
      else if (jd?.job_description) setSelected(jd.job_description.id)
    }
    setTab(next)
  }
  function newJob() { setEditNotice(''); chat.current?.startNew(); setSelected('draft'); setTab('assistant') }

  return <section className={`job-descriptions-page${tab === 'assistant' ? ' is-assistant' : tab === 'suggestions' ? ' is-suggestions' : ''}`}>
    <header className="redesign-header"><div><p className="workspace-eyebrow">Recruitment workspace</p><h1>Job Descriptions</h1><p className="muted">Find a job, review its criteria, or draft with the assistant.</p></div>{tab === 'assistant' && <label className="jd-job-picker">Job<select aria-label="Job for assistant" value={activeId} disabled={busy || Boolean(editor)} onChange={event => selectJob(event.target.value)}><option value="draft">{draft ? 'Current draft · not approved' : 'New job description'}</option>{library.jobs.map(job => <option key={job.id} value={job.id}>{job.title} (JD {job.number})</option>)}</select></label>}<button className="button-primary" disabled={busy || Boolean(editor)} onClick={newJob}>New job description</button></header>

    {tab === 'suggestions' && <label className="jd-mobile-job-picker">Job<select aria-label="Job for suggestions" value={activeId} disabled={busy || Boolean(editor)} onChange={event => selectJob(event.target.value)}>{draft && <option value="draft">Current draft · not approved</option>}{library.jobs.map(job => <option key={job.id} value={job.id}>{job.title} · JD {job.number}</option>)}</select></label>}
    <div className="jd-redesign-workspace">
      {tab !== 'assistant' && <aside className="jd-redesign-library"><h2>Job library</h2><div className="jd-library-filters"><label>Search jobs<input type="search" placeholder="Title or JD number" value={query} onChange={event => setQuery(event.target.value)} /></label><label>Status<select aria-label="Status" value={status} onChange={event => setStatus(event.target.value)}><option value="all">All</option><option value="approved">Approved</option><option value="draft">Current draft</option></select></label></div>
        <div className="jd-library-scroll">
          {library.loading && <p role="status">Loading job library…</p>}
          {library.error && <div role="alert"><p>{library.error}</p><button className="button-secondary" onClick={onSaved}>Try again</button></div>}
          {draft && status !== 'approved' && (!query || (jd.draft_jd ?? '').toLowerCase().includes(query.toLowerCase())) && <button className={`jd-library-item${showingDraft ? ' is-selected' : ''}`} disabled={busy || Boolean(editor)} onClick={() => selectJob('draft')}><strong>Current job draft</strong><span className="ui-badge is-warning">Draft · not approved</span></button>}
          {visibleJobs.map(job => <button key={job.id} className={`jd-library-item${activeId === job.id ? ' is-selected' : ''}`} aria-pressed={activeId === job.id} disabled={busy || Boolean(editor)} onClick={() => selectJob(job.id)}><strong>{job.title}</strong><small>JD {job.number}</small><span className="ui-badge is-success">✓ Approved</span></button>)}
          {!library.loading && !library.error && !visibleJobs.length && !(draft && status !== 'approved') && <div className="ui-state"><h3>{query || status !== 'all' ? 'No jobs match these filters' : 'No approved jobs yet'}</h3><p>{query || status !== 'all' ? 'Change the search or status to see more jobs.' : 'Create a job description with the assistant to get started.'}</p></div>}
        </div><button className="button-secondary" onClick={() => referenceDialog.current?.showModal()}>Reference documents ›</button>
      </aside>}
      <section className="jd-detail-panel">
        {tab !== 'assistant' && <header className="jd-detail-header"><div><h2>{title ?? 'Select or create a job'}</h2>{title && <p className="muted">{showingDraft ? 'Review this draft before using it for screening.' : `JD ${document?.number ?? '…'} · Screening criteria ${document?.profile_version != null ? `v${document.profile_version}` : 'not set up'}`}</p>}</div><div className="jd-detail-actions">{tab !== 'suggestions' && content && editVersion && !editor && <button className="button-secondary" disabled={busy || editingLocked} onClick={beginEdit}>{tab === 'criteria' ? 'Edit screening criteria' : 'Edit description'}</button>}{title && <span className={`ui-badge ${showingDraft ? 'is-warning' : 'is-success'}`}>{showingDraft ? 'Draft · not approved' : '✓ Approved'}</span>}{!showingDraft && document && <Link className="button-primary" to={`/recruiter/cv-screening?job=${document.id}`}>Go to CV Screening</Link>}{showingDraft && <button className="button-secondary" disabled={busy || Boolean(editor)} onClick={() => changeTab('assistant')}>Ask the assistant to revise</button>}</div></header>}
        <nav className="redesign-tabs" aria-label="Job description sections">{(['description', 'criteria', 'suggestions', 'assistant'] as const).map(value => <button key={value} aria-current={tab === value ? 'page' : undefined} onClick={() => changeTab(value)} disabled={Boolean(editor) || (busy && value === 'assistant')}>{value === 'description' ? 'Description' : value === 'criteria' ? 'Screening criteria' : value === 'suggestions' ? 'Suggestions' : 'AI assistant'}</button>)}</nav>
        <div className="jd-assistant-mount" hidden={tab !== 'assistant'}><RecruiterChat ref={chat} token={token} userId={userId} onSaved={onSaved} onStateChange={onStateChange} onReviewTab={changeTab} /></div>
        {editNotice && (tab === 'description' || tab === 'criteria') && <p className="jd-save-notice" role="status">{editNotice}</p>}
        {editingLocked && tab !== 'assistant' && tab !== 'suggestions' && <p className="jd-save-notice">This job is posted or posting is pending. Its description and screening criteria are locked.</p>}
        {editor && <JobEditor token={token} target={editor} onSaved={finishEditing} onCancel={cancelEditing} />}
        {tab === 'suggestions' && (showingDraft ? <UnapprovedSuggestions onNavigate={changeTab} /> : document ? <JobSuggestions key={`${token}:${document.id}:${document.edit_version}`} token={token} job={document} onSaved={finishEditing} onNavigate={changeTab} onReload={onSaved} /> : <p role="status">Select a saved job to view suggestions.</p>)}
        {tab !== 'assistant' && tab !== 'suggestions' && !editor && <div className={`jd-document-layout${showingDraft && content ? '' : ' is-full-width'}`}><div className="jd-document-scroll">
          {activeId && !showingDraft && detail?.id !== activeId && <div className="ui-state" role="status">Loading job description…</div>}
          {detail?.id === activeId && detail.error && <div className="ui-state" role="alert"><p>{detail.error}</p><button className="button-secondary" onClick={onSaved}>Try again</button></div>}
          {!activeId || (showingDraft && !content) ? <div className="ui-state"><h3>Start with a job description</h3><p>Tell the assistant about the role, seniority, and requirements.</p><button className="button-primary" onClick={() => changeTab('assistant')}>Open AI assistant</button></div> : content && (tab === 'description' ? <div className="markdown"><Markdown skipHtml>{content}</Markdown></div> : <Criteria profile={profile} />)}
        </div>{content && showingDraft && <aside className="jd-context"><ApprovalReview key={`${assistant?.conversation_id}:${jd?.checkpoint_id}`} disabled={busy || !jd?.interrupt?.payload.allowed_actions.includes('approve')} errors={jd?.acceptance_errors ?? []} profile={profile} onApprove={() => { chat.current?.approve(); setTab('assistant') }} /></aside>}</div>}
      </section>
    </div>
    <dialog className="jd-reference-dialog" ref={referenceDialog} aria-label="Reference documents"><header><h2>Reference documents</h2><button className="button-secondary" onClick={() => referenceDialog.current?.close()}>Close</button></header><ReferenceDocuments token={token} /></dialog>
  </section>
}
