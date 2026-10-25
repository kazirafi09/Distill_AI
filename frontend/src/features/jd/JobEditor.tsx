import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import { ApiError } from '../../services/api'
import { saveJobEdits, suggestJobEdits } from './api'
import type { EditableProfile, JDConversation, ScreeningProfile } from './api'
import type { WindTunnelSuggestion } from './windTunnelApi'
import SuggestionReview from './SuggestionReview'

const dimensions = [
  ['technical_skills', 'Technical skills'], ['work_experience', 'Work experience'],
  ['qualifications', 'Qualifications'], ['additional_criteria', 'Additional criteria'],
] as const

export type JobEditorTarget = {
  conversationId: string
  editVersion: string
  content: string
  profile: ScreeningProfile | null
  approved: boolean
  section: 'description' | 'criteria'
}

function editableProfile(profile: ScreeningProfile | null): EditableProfile {
  return {
    criteria: (profile?.criteria ?? []).map(({ id, dimension, text, priority }) => ({ id, dimension, text, priority })),
    dimension_weights: Object.fromEntries(dimensions.map(([key]) => [key, profile?.dimension_weights[key] ?? 0])),
    minimum_years_experience: profile?.minimum_years_experience ?? null,
    fresher_eligibility: profile?.fresher_eligibility ?? null,
    google_form_url: profile?.google_form_url ?? null,
  }
}

function criterionChanged(a: EditableProfile['criteria'][number], b: EditableProfile['criteria'][number]) {
  return a.text !== b.text || a.dimension !== b.dimension || a.priority !== b.priority
}

export default function JobEditor({ token, target, onSaved, onCancel, proposal }: {
  token: string
  target: JobEditorTarget
  onSaved: (conversation: JDConversation) => void
  onCancel: () => void
  proposal?: { content: string; screening_profile: EditableProfile; apply: (signal: AbortSignal) => Promise<JDConversation>; title: string; patches: WindTunnelSuggestion['patches']; position?: string; conflict?: boolean; applyLabel?: string }
}) {
  const [content, setContent] = useState(proposal?.content ?? target.content)
  const [profile, setProfile] = useState(() => proposal?.screening_profile ?? editableProfile(target.profile))
  const [section, setSection] = useState(target.section)
  const [changedSection, setChangedSection] = useState(target.section)
  const [reviewing, setReviewing] = useState(Boolean(proposal))
  const [reviewed, setReviewed] = useState(false)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [saveConflict, setSaveConflict] = useState(false)
  const request = useRef<AbortController | null>(null)
  const editor = useRef<HTMLElement | null>(null)
  const original = editableProfile(target.profile)
  const title = content.split('\n')[0].replace(/^#\s*/, '')
  const body = content.split('\n').slice(1).join('\n').replace(/^\n+/, '')
  const total = Object.values(profile.dimension_weights).reduce((sum, value) => sum + value, 0)
  const removed = original.criteria.filter(old => !profile.criteria.some(current => current.id === old.id))

  useEffect(() => () => request.current?.abort(), [])
  useEffect(() => { if (editor.current) editor.current.scrollTop = 0 }, [section, reviewing])

  function updateContent(next: string) {
    setContent(next)
    setChangedSection('description')
    setReviewed(false)
  }

  function updateProfile(next: EditableProfile) {
    setProfile(next)
    setChangedSection('criteria')
    setReviewed(false)
  }

  function updateCriterion(index: number, changes: Partial<EditableProfile['criteria'][number]>) {
    updateProfile({ ...profile, criteria: profile.criteria.map((criterion, i) => i === index ? { ...criterion, ...changes } : criterion) })
  }

  function validationError() {
    if (!title.trim()) return 'Enter a job title.'
    if (!profile.criteria.length || profile.criteria.some(criterion => !criterion.text.trim() || !criterion.priority)) {
      return 'Add at least one criterion and choose a priority for each criterion.'
    }
    const active = new Set(profile.criteria.map(criterion => criterion.dimension))
    if (Math.abs(total - 100) > 0.000001 || Object.entries(profile.dimension_weights).some(([key, value]) =>
      !Number.isFinite(value) || value < 0 || value > 100 || (!active.has(key) && value !== 0))) {
      return 'Weights must total 100%. Dimensions without criteria must have zero weight.'
    }
    return ''
  }

  async function reviewChanges() {
    // Description edits may establish the first criteria; allow the assistant
    // to propose them even if the current profile is empty or incomplete.
    const invalid = changedSection === 'criteria' ? validationError() : !title.trim() ? 'Enter a job title.' : ''
    if (invalid) { setError(invalid); return }
    const controller = new AbortController()
    request.current = controller
    setWorking(true)
    setError('')
    setNotice('')
    try {
      const proposal = await suggestJobEdits(token, target.conversationId, {
        edit_version: target.editVersion, content, screening_profile: profile, criteria_reviewed: false,
      }, changedSection, controller.signal)
      if (controller.signal.aborted) return
      setContent(proposal.content)
      setProfile(proposal.screening_profile)
      setSection(changedSection === 'description' ? 'criteria' : 'description')
      setReviewing(true)
      setReviewed(false)
      setNotice('Matching changes are suggested below. Review both tabs before saving. Nothing has been saved yet.')
    } catch (caught) {
      if (!controller.signal.aborted) {
        setError(caught instanceof Error ? caught.message : 'Could not suggest matching changes.')
        // An unavailable model should not prevent a recruiter making a fully
        // manual edit. Saving still validates and requires explicit review.
        setReviewing(!(caught instanceof ApiError && [401, 403, 404, 409, 422].includes(caught.status)))
        setReviewed(false)
      }
    } finally {
      if (!controller.signal.aborted) { request.current = null; setWorking(false) }
    }
  }

  async function save() {
    const invalid = validationError()
    if (invalid) { setError(invalid); return }
    const controller = new AbortController()
    request.current = controller
    setWorking(true)
    setError('')
    try {
      const updated = proposal ? await proposal.apply(controller.signal) : await saveJobEdits(token, target.conversationId, {
        edit_version: target.editVersion, content, screening_profile: profile, criteria_reviewed: reviewed,
      }, controller.signal)
      if (!controller.signal.aborted) onSaved(updated)
    } catch (caught) {
      if (!controller.signal.aborted) {
        setError(caught instanceof Error ? caught.message : 'Could not save changes.')
        if (proposal && caught instanceof ApiError && caught.status === 409) { setSaveConflict(true); setReviewed(false) }
      }
    } finally {
      if (!controller.signal.aborted) { request.current = null; setWorking(false) }
    }
  }

  if (proposal) return <SuggestionReview title={proposal.title} position={proposal.position} applyLabel={proposal.applyLabel} content={content} profile={profile} original={target.profile}
    patches={proposal.patches} working={working} conflict={saveConflict || Boolean(proposal.conflict)} error={error} reviewed={reviewed}
    onReviewed={setReviewed} onBack={onCancel} onApply={() => { void save() }} />

  return <section ref={editor} className="jd-editor" aria-label="Edit job" aria-busy={working}>
    <header>
      <h3>{reviewing ? 'Review changes' : 'Edit job'}</h3>
      <p className="muted">{target.approved
        ? 'Changes update this approved, unposted job. Earlier screening results retain their original criteria.'
        : 'Your draft remains unapproved. The assistant will use your changes after you save.'}</p>
      <p className="muted">{proposal ? 'Review this exact proposal in both tabs. Applying saves the description and criteria together.' : 'Requirements in the public description come from screening criteria. Other wording is checked for matching changes during review.'}</p>
    </header>
    <div className="jd-editor-tabs" role="group" aria-label="Editor sections">
      <button type="button" className="button-secondary" aria-pressed={section === 'description'} onClick={() => setSection('description')}>Description</button>
      <button type="button" className="button-secondary" aria-pressed={section === 'criteria'} onClick={() => setSection('criteria')}>Screening criteria</button>
    </div>
    {notice && <p className="jd-editor-notice" role="status">{notice}</p>}
    <fieldset disabled={working || Boolean(proposal)}>
      {section === 'description' ? <>
        <label>Job title<input value={title} maxLength={255} onChange={event => updateContent(`# ${event.target.value}\n\n${body}`)} /></label>
        <div className="jd-editor-field"><label htmlFor="jd-editor-description">Description wording (Markdown)</label><textarea id="jd-editor-description" rows={14} value={body} onChange={event => updateContent(`# ${title}\n\n${event.target.value}`)} /></div>
        <p className="muted">Edit the overview, responsibilities, and company details here. The Selection Criteria and Application Information sections are generated from the other tab.</p>
        {reviewing && <details><summary>Compare description changes</summary>
          <h4>Previous description</h4><div className="markdown"><Markdown skipHtml>{target.content}</Markdown></div>
          <h4>Proposed description</h4><div className="markdown"><Markdown skipHtml>{content}</Markdown></div>
        </details>}
      </> : <>
        <h4>Screening criteria</h4>
        {profile.criteria.map((criterion, index) => {
          const old = original.criteria.find(item => item.id === criterion.id)
          return <div className="jd-editor-criterion" key={criterion.id || `new-${index}`}>
            <div className="jd-editor-row">
              <strong>Criterion {index + 1}{!old ? ' · Added' : criterionChanged(old, criterion) ? ' · Changed' : ''}</strong>
              <button type="button" className="text-link" aria-label={`Remove criterion ${index + 1}`} onClick={() => updateProfile({ ...profile, criteria: profile.criteria.filter((_, i) => i !== index) })}>Remove</button>
            </div>
            {old && criterionChanged(old, criterion) && <p className="muted">Previously: {old.text} · {old.priority ?? 'Unconfirmed'} · {old.dimension.replaceAll('_', ' ')}</p>}
            <div className="jd-editor-field"><label htmlFor={`jd-editor-requirement-${index}`}>Requirement</label><textarea id={`jd-editor-requirement-${index}`} rows={2} maxLength={2000} value={criterion.text} onChange={event => updateCriterion(index, { text: event.target.value })} /></div>
            <div className="jd-editor-columns">
              <div className="jd-editor-field"><label htmlFor={`jd-editor-dimension-${index}`}>Dimension</label><select id={`jd-editor-dimension-${index}`} value={criterion.dimension} onChange={event => updateCriterion(index, { dimension: event.target.value })}>{dimensions.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></div>
              <div className="jd-editor-field"><label htmlFor={`jd-editor-priority-${index}`}>Priority</label><select id={`jd-editor-priority-${index}`} value={criterion.priority ?? ''} onChange={event => updateCriterion(index, { priority: (event.target.value || null) as typeof criterion.priority })}><option value="">Choose priority</option><option value="Must have">Essential</option><option value="Preferred">Preferred</option></select></div>
            </div>
          </div>
        })}
        {removed.length > 0 && <div className="jd-editor-notice"><strong>Removed criteria</strong><ul>{removed.map(criterion => <li key={criterion.id}>{criterion.text}</li>)}</ul></div>}
        <button type="button" className="button-secondary" onClick={() => updateProfile({ ...profile, criteria: [...profile.criteria, { id: '', dimension: 'technical_skills', text: '', priority: null }] })}>Add criterion</button>
        <h4>Dimension weights</h4>
        {proposal && <p className="muted">Previous weights: {dimensions.map(([key, label]) => `${label}: ${original.dimension_weights[key]}%`).join(' · ')}</p>}
        <div className="jd-editor-columns">{dimensions.map(([key, label]) => <label key={key}>{label} (%)<input type="number" min="0" max="100" step="0.1" value={profile.dimension_weights[key]} onChange={event => updateProfile({ ...profile, dimension_weights: { ...profile.dimension_weights, [key]: Number(event.target.value) } })} /></label>)}</div>
        <p className={Math.abs(total - 100) < 0.000001 ? 'muted' : 'ui-error'}>Total: {Number(total.toFixed(4))}% · Dimensions without criteria must have zero weight.</p>
        {([['minimum_years_experience', 'Minimum experience'], ['fresher_eligibility', 'Fresher eligibility'], ['google_form_url', 'Google Form URL']] as const).map(([key, label]) => <label key={key}>{label}{proposal && original[key] !== profile[key] && <small>Previously: {original[key] || 'Not set'}</small>}<input value={profile[key] ?? ''} onChange={event => updateProfile({ ...profile, [key]: event.target.value || null })} /></label>)}
      </>}
    </fieldset>
    <footer>
      {error && <p className="ui-error" role="alert">{error}</p>}
      {reviewing && <label className="jd-review-check"><input type="checkbox" checked={reviewed} disabled={working} onChange={event => setReviewed(event.target.checked)} />I reviewed the description and screening criteria together.</label>}
      <div className="jd-editor-row">
        <button type="button" className="button-secondary" disabled={working} onClick={onCancel}>Cancel editing</button>
        {!proposal && <button type="button" className="button-secondary" disabled={working} onClick={() => { void reviewChanges() }}>{working ? 'Working…' : reviewing ? 'Suggest matching changes again' : 'Review matching changes'}</button>}
        {reviewing && <button type="button" className="button-primary" disabled={working || !reviewed} onClick={() => { void save() }}>{working ? 'Saving…' : proposal ? 'Apply suggestion' : 'Save changes'}</button>}
      </div>
    </footer>
  </section>
}
