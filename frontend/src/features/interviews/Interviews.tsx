import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { loadShortlist } from './api'
import type { SavedShortlist } from './api'
import type { JobDescriptionSummary } from '../jd/api'
import RecruitmentHeader from './RecruitmentHeader'
import RecruiterInterviewPanel from './RecruiterInterviewPanel'
import { useRecruitmentJob } from './useRecruitmentJob'
import './InterviewLobby.css'

function LiveWorkspace({ token, userId, job }: { token: string; userId: string; job: JobDescriptionSummary }) {
  const [saved, setSaved] = useState<SavedShortlist | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    void loadShortlist(token, job.id, controller.signal).then(next => {
      if (!controller.signal.aborted) { setSaved(next); setLoading(false); setError('') }
    }).catch(caught => {
      if (!controller.signal.aborted) { setError(caught instanceof Error ? caught.message : 'Could not load candidates.'); setLoading(false) }
    })
    return () => controller.abort()
  }, [token, job.id, revision])
  if (loading) return <p role="status">Loading saved interviewees…</p>
  if (error) return <div role="alert"><p>{error}</p><button className="button-secondary" onClick={() => { setLoading(true); setRevision(value => value + 1) }}>Try again</button></div>
  return <>
    <div className="recruitment-context"><span>Prepare and conduct interviews with your saved candidates.</span><Link to={`/recruiter/invitations?job=${encodeURIComponent(job.id)}`}>Manage invitations</Link></div>
    {saved?.outdated && <p className="cv-error">Screening results have changed. <Link to={`/recruiter/cv-screening?job=${encodeURIComponent(job.id)}#interview-shortlist`}>Review the saved shortlist.</Link></p>}
    <RecruiterInterviewPanel token={token} userId={userId} jobId={job.id} members={saved?.members ?? []} jobTitle={job.title} jobNumber={job.number} />
  </>
}

export default function Interviews({ token, userId }: { token: string; userId: string }) {
  const picker = useRecruitmentJob(token)
  return <section className="recruitment-page live-interviews-page">
    <RecruitmentHeader title="Interviews" description="Check your devices, admit a candidate and conduct the interview." jobs={picker.jobs} job={picker.job} onSelect={picker.selectJob} />
    {picker.loading && <p role="status">Loading jobs…</p>}
    {picker.error && <div role="alert"><p>{picker.error}</p><button className="button-secondary" onClick={picker.retry}>Try again</button></div>}
    {!picker.loading && !picker.error && !picker.job && <p>No jobs yet. <Link to="/recruiter/job-descriptions">Create a job description</Link> to get started.</p>}
    {!picker.loading && !picker.error && picker.job && <LiveWorkspace key={picker.job.id} token={token} userId={userId} job={picker.job} />}
  </section>
}
