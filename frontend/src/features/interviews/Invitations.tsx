import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import InvitationSending from './InvitationSending'
import { loadShortlist } from './api'
import type { SavedShortlist } from './api'
import RecruitmentHeader from './RecruitmentHeader'
import { useRecruitmentJob } from './useRecruitmentJob'
import './InterviewLobby.css'
import './Interviews.css'

function InvitationWorkspace({ token, jobId }: { token: string; jobId: string }) {
  const [shortlist, setShortlist] = useState<SavedShortlist | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    void loadShortlist(token, jobId, controller.signal).then(next => {
      if (!controller.signal.aborted) { setShortlist(next); setLoading(false); setError('') }
    }).catch(caught => {
      if (!controller.signal.aborted) { setLoading(false); setError(caught instanceof Error ? caught.message : 'Could not load shortlist.') }
    })
    return () => controller.abort()
  }, [token, jobId, revision])
  return <>
    <section className="invitation-recipients">
      <div className="recruitment-section-heading"><div><h2>Saved recipients</h2><p>{shortlist?.members.length ?? 0} candidates in this job’s saved shortlist</p></div><button className="button-secondary" disabled={loading} onClick={() => { setLoading(true); setRevision(value => value + 1) }}>Reload shortlist</button></div>
      {loading && <p role="status">Loading saved recipients…</p>}
      {error && <p role="alert" className="cv-error">{error}</p>}
      {shortlist?.outdated && <p className="cv-error">This shortlist needs updated screening results. Review and save it in CV Screening before sending.</p>}
      {!loading && !error && !shortlist?.members.length && <p>No saved recipients yet. Select candidates in CV Screening first.</p>}
      {!!shortlist?.members.length && <ul>{shortlist.members.map(member => <li key={member.application_id}><strong>{member.name}</strong><span>{member.email}</span><small>Saved rank #{member.rank} · Score {Number(member.score.aggregate).toFixed(2)} / 100</small></li>)}</ul>}
      <div className="recruitment-links"><Link to={`/recruiter/cv-screening?job=${encodeURIComponent(jobId)}#interview-shortlist`}>Edit shortlist</Link><Link to={`/recruiter/interviews?job=${encodeURIComponent(jobId)}`}>Prepare interview →</Link></div>
    </section>
    <InvitationSending token={token} jobId={jobId} canSend={!loading && !error && !!shortlist?.members.length && !shortlist.outdated} />
  </>
}

export default function Invitations({ token }: { token: string }) {
  const picker = useRecruitmentJob(token)
  return <section className="recruitment-page invitations-page">
    <RecruitmentHeader title="Invitations" description="Send interview invitations and manage email outcomes and candidate links." jobs={picker.jobs} job={picker.job} onSelect={picker.selectJob} />
    {picker.loading && <p role="status">Loading jobs…</p>}
    {picker.error && <div role="alert"><p>{picker.error}</p><button className="button-secondary" onClick={picker.retry}>Try again</button></div>}
    {!picker.loading && !picker.error && !picker.job && <p>No jobs yet. <Link to="/recruiter/job-descriptions">Create a job description</Link> to get started.</p>}
    {!picker.loading && !picker.error && picker.job && <InvitationWorkspace key={picker.job.id} token={token} jobId={picker.job.id} />}
  </section>
}
