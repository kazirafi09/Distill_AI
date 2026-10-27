import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { getCVProfile } from './api'
import type { CandidateApplication, ParseResult } from './api'
import { statusLabel, warningLabel } from './parsingLabels'
import { candidateFileLabel, profileItems, profileLink, profileList, profileRecord, profileText } from './profileView'
import { sourceLabel } from './screeningView'
import './CandidateProfile.css'

type ProfileEvidence = ParseResult['evidence']

function Evidence({ items, path, fileUrl }: { items: ProfileEvidence; path: string; fileUrl: string }) {
  const matched = items.filter(item => item.claim_path === path || item.claim_path.startsWith(`${path}.`))
  const unique = matched.filter((item, index) => matched.findIndex(other => other.source_id === item.source_id && other.quote === item.quote) === index)
  if (!unique.length) return null
  return <details className="profile-evidence"><summary>Supporting excerpts ({unique.length})</summary>{unique.map((item, index) => <blockquote key={`${item.source_id}:${index}`}><p>“{item.quote}”</p><footer><span>{sourceLabel(item.source_id)}{item.ocr_derived ? ' · Read from image' : ''}</span><a href={fileUrl} target="_blank" rel="noopener noreferrer">Open source CV ↗</a></footer></blockquote>)}</details>
}

function ProfileSection({ id, title, count, children }: { id: string; title: string; count?: number; children: ReactNode }) {
  return <section id={id} className="profile-section"><header><h3>{title}</h3>{count !== undefined && count > 0 && <span className="profile-count">{count}</span>}</header>{children}</section>
}

function Tags({ items }: { items: string[] }) {
  return <ul className="profile-tags">{items.map(item => <li key={item}>{item}</li>)}</ul>
}

function ProfileDocument({ token, versionId, fileUrl, onSource }: { token: string; versionId: string; fileUrl: string; onSource: () => void }) {
  const [result, setResult] = useState<ParseResult | null>(null)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const sectionRefs = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function load() {
      try {
        const next = await getCVProfile(token, versionId, controller.signal)
        if (!controller.signal.aborted) {
          setResult(next); setError('')
          if (!['completed', 'needs_review', 'failed', 'not_started'].includes(next.status)) timer = setTimeout(() => void load(), 2500)
        }
      } catch (caught) { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Could not load this CV profile.') }
    }
    void load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, versionId, revision])

  if (error) return <div className="screening-notice is-danger" role="alert"><strong>Could not load the profile</strong><p>{error}</p><button className="button-secondary" onClick={() => setRevision(value => value + 1)}>Try again</button></div>
  if (!result) return <div className="ui-state" role="status">Loading candidate profile…</div>
  if (!result.profile) return <div className="ui-state"><h3>{result.status === 'failed' ? 'This CV could not be read' : 'Profile not available yet'}</h3><p>{result.error_code ? warningLabel(result.error_code) : 'The CV must be read before its profile can be shown.'}</p><button className="button-secondary" onClick={onSource}>View file and processing status</button></div>

  const profile = result.profile
  const contact = profileRecord(profile.contact)
  const skills = profileItems(profile.skills)
  const work = profileList(profile.work_history).map(profileRecord)
  const education = profileList(profile.education).map(profileRecord)
  const projects = profileList(profile.projects).map(profileRecord)
  const certifications = profileList(profile.certifications).map(profileRecord)
  const sections = [
    ['skills', 'Skills', skills.length], ['work_history', 'Experience', work.length],
    ['education', 'Education', education.length], ['projects', 'Projects', projects.length], ['certifications', 'Certifications', certifications.length], ['contact', 'Contact', null],
  ] as const
  const warnings = [...new Set([...(result.warnings ?? []), ...(result.error_code ? [result.error_code] : [])])]
  const prefix = `profile-${versionId}`
  function jump(id: string) {
    const section = sectionRefs.current?.querySelector<HTMLElement>(`[data-section="${id}"]`)
    section?.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
  }
  const excerpt = (path: string) => <Evidence items={result!.evidence ?? []} path={path} fileUrl={fileUrl} />
  return <div className="candidate-profile" ref={sectionRefs}>
    {result.status !== 'completed' && <p className="profile-empty" role="status">{statusLabel(result.status)} · Check the saved details against the original CV.</p>}
    {warnings.length > 0 && <details className="profile-review-notice"><summary>{warnings.length} {warnings.length === 1 ? 'detail' : 'details'} to check in the CV</summary><ul>{warnings.map(warning => <li key={warning}>{warningLabel(warning)}</li>)}</ul></details>}
    <nav className="profile-section-nav" aria-label="Profile sections">{sections.map(([id, label, count]) => <button key={id} onClick={() => jump(id)}>{label}{count !== null && count > 0 && <span>{count}</span>}</button>)}</nav>

    <div data-section="skills"><ProfileSection id={`${prefix}-skills`} title="Skills" count={skills.length}>{skills.length ? <Tags items={skills} /> : <p className="profile-empty">No skills stated in the saved profile.</p>}{excerpt('skills')}</ProfileSection></div>
    <div data-section="work_history"><ProfileSection id={`${prefix}-experience`} title="Work experience" count={work.length}>{!work.length && <p className="profile-empty">No work experience stated in the saved profile.</p>}{work.map((role, index) => <article className="profile-entry" key={index}><header><div><h4>{profileText(role.title) || 'Role not stated'}</h4><p>{profileText(role.employer) || 'Employer not stated'}</p></div><div className="profile-entry-meta">{profileText(role.dates) && <span>{profileText(role.dates)}</span>}{profileText(role.stated_duration) && <span>{profileText(role.stated_duration)}</span>}</div></header>{profileItems(role.responsibilities).length > 0 && <ul className="profile-responsibilities">{profileItems(role.responsibilities).map(item => <li key={item}>{item}</li>)}</ul>}{excerpt(`work_history.${index}`)}</article>)}</ProfileSection></div>
    <div data-section="education"><ProfileSection id={`${prefix}-education`} title="Education" count={education.length}>{!education.length && <p className="profile-empty">No education stated in the saved profile.</p>}{education.map((item, index) => <article className="profile-entry" key={index}><header><div><h4>{[profileText(item.qualification), profileText(item.field)].filter(Boolean).join(' · ') || 'Qualification not stated'}</h4><p>{profileText(item.institution) || 'Institution not stated'}</p></div>{profileText(item.dates) && <span className="profile-entry-meta">{profileText(item.dates)}</span>}</header>{profileText(item.grade) && <p className="profile-grade">Grade: {profileText(item.grade)}</p>}{excerpt(`education.${index}`)}</article>)}</ProfileSection></div>
    <div data-section="projects"><ProfileSection id={`${prefix}-projects`} title="Projects" count={projects.length}>{!projects.length && <p className="profile-empty">No projects stated in the saved profile.</p>}{projects.map((item, index) => <article className="profile-entry" key={index}><header><h4>{profileText(item.name) || 'Project name not stated'}</h4>{profileText(item.dates) && <span className="profile-entry-meta">{profileText(item.dates)}</span>}</header>{profileText(item.description) && <p className="profile-project-description">{profileText(item.description)}</p>}{profileItems(item.technologies).length > 0 && <Tags items={profileItems(item.technologies)} />}{excerpt(`projects.${index}`)}</article>)}</ProfileSection></div>
    <div data-section="certifications"><ProfileSection id={`${prefix}-certifications`} title="Certifications" count={certifications.length}>{!certifications.length && <p className="profile-empty">No certifications stated in the saved profile.</p>}{certifications.map((item, index) => <article className="profile-entry" key={index}><header><div><h4>{profileText(item.name) || 'Certification not stated'}</h4>{profileText(item.issuer) && <p>{profileText(item.issuer)}</p>}</div>{profileText(item.dates) && <span className="profile-entry-meta">{profileText(item.dates)}</span>}</header>{excerpt(`certifications.${index}`)}</article>)}</ProfileSection></div>
    <div data-section="contact"><ProfileSection id={`${prefix}-contact`} title="Contact details">
      <dl className="profile-contact-grid">{[['name', 'Name in CV'], ['email', 'Email in CV'], ['phone', 'Phone'], ['address', 'Location']].map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{profileText(contact[key]) || 'Not stated'}</dd></div>)}</dl>
      {profileList(contact.links).length > 0 && <div className="profile-contact-links">{profileList(contact.links).map((link, index) => profileText(link) && (profileLink(link) ? <a key={index} href={profileLink(link)!} target="_blank" rel="noopener noreferrer">{profileText(link)} ↗</a> : <span key={index}>{profileText(link)}</span>))}</div>}
      {excerpt('contact')}
    </ProfileSection></div>
  </div>
}

export default function CandidateProfile({ token, application, initialFileId, onSource }: { token: string; application: CandidateApplication; initialFileId?: string; onSource: () => void }) {
  const [selectedId, setSelectedId] = useState(initialFileId ?? '')
  const files = application.cv_documents
  const file = files.find(item => item.id === selectedId) ?? files[0]
  if (!file) return <div className="ui-state"><h3>No CV linked</h3><p>Sync the application source to import this candidate’s CV.</p><button className="button-secondary" onClick={onSource}>View source information</button></div>
  return <div className="profile-document">{files.length > 1 && <header className="profile-document-header"><label>CV document<select aria-label="CV document" value={file.id} onChange={event => setSelectedId(event.target.value)}>{files.map((item, index) => <option value={item.id} key={item.id}>{candidateFileLabel(application.full_name, item.original_filename, index, files.length)}</option>)}</select></label></header>}{file.file_version_id ? <ProfileDocument key={file.file_version_id} token={token} versionId={file.file_version_id} fileUrl={`https://drive.google.com/file/d/${encodeURIComponent(file.drive_file_id)}/view`} onSource={onSource} /> : <div className="ui-state"><h3>CV download needed</h3><p>The profile will be available after this CV has been downloaded and read.</p><button className="button-secondary" onClick={onSource}>View source information</button></div>}</div>
}
