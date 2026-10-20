import { useEffect, useState } from 'react'
import CandidateProfile from '../cv/CandidateProfile'
import { getApplication } from '../cv/api'
import type { CandidateApplication } from '../cv/api'
import { AssessmentDetails } from '../cv/CVResults'
import { loadScreeningResults } from './api'
import type { SavedInterviewee, ScreeningResults } from './api'
import './InterviewCandidateProfile.css'

export default function InterviewCandidateProfile({ token, jobId, candidate }: {
  token: string; jobId: string; candidate: SavedInterviewee
}) {
  const [revision, setRevision] = useState(0)
  const key = `${jobId}:${candidate.application_id}`
  const [result, setResult] = useState<{
    key: string; application: CandidateApplication | null; screening: ScreeningResults | null
    error: string; screeningError: boolean
  } | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    void Promise.allSettled([
      getApplication(token, jobId, candidate.application_id, controller.signal),
      loadScreeningResults(token, jobId, controller.signal),
    ]).then(([application, screening]) => {
      if (!controller.signal.aborted) setResult({ key,
        application: application.status === 'fulfilled' ? application.value : null,
        screening: screening.status === 'fulfilled' ? screening.value : null,
        error: application.status === 'rejected' ? 'Could not load the saved CV details.' : '',
        screeningError: screening.status === 'rejected',
      })
    })
    return () => controller.abort()
  }, [token, jobId, candidate.application_id, key, revision])
  const current = result?.key === key ? result : null
  const screening = current?.screening
  const assessment = [...screening?.ranked ?? [], ...screening?.incomplete ?? []].find(item => item.application_id === candidate.application_id)
  const topics = assessment?.status === 'completed' && !assessment.outdated ? assessment.matches.filter(match => match.decision !== 'met') : []
  const screeningUrl = `/recruiter/cv-screening?${new URLSearchParams({ job: jobId, candidate: candidate.application_id })}`
  const aggregate = Number(candidate.score.aggregate)

  return <div className="interview-candidate-profile">
    <header><h2>{candidate.name}</h2><p>{candidate.email}</p></header>
    <dl className="interview-profile-metrics"><div><dt>Saved CV score</dt><dd>{Number.isFinite(aggregate) ? `${aggregate.toFixed(2)} / 100` : 'Unavailable'}</dd></div>
      <div><dt>Shortlist rank</dt><dd>#{candidate.rank}</dd></div></dl>
    <p className="interview-profile-note">Scores and rank come from the saved shortlist. CV details are candidate-reported, not identity verification or a live interview rating.</p>
    {candidate.outdated && <p className="interview-profile-warning">The shortlist has older screening inputs. Review current results before relying on its saved score.</p>}
    <a className="interview-profile-link" href={screeningUrl} target="_blank" rel="noopener noreferrer">Open screening and source files ↗</a>
    {!current ? <p role="status">Loading candidate background…</p> : <>
      <section className="interview-probe-topics"><h3>Areas to probe</h3>
        {current.screeningError ? <p>Screening review topics could not be loaded. The CV profile is still available below.</p>
          : !assessment || assessment.status !== 'completed' || assessment.outdated ? <p>No current screening review topics are available. Check the saved evidence in CV Screening.</p>
          : !topics.length ? <p>No unmet or partially supported criteria were flagged in the current screening results. Use the CV to explore depth and examples.</p>
          : <ul>{topics.map(topic => <li key={topic.requirement_id}>
            <strong>{screening?.profile?.criteria.find(criterion => criterion.id === topic.requirement_id)?.text ?? topic.requirement_id}</strong>
            <p>{topic.justification}</p>
          </li>)}</ul>}
        {assessment && <AssessmentDetails item={assessment} criteria={screening?.profile ?? null} />}
      </section>
      {current.error ? <div role="alert"><p>{current.error}</p><button className="button-secondary" onClick={() => setRevision(value => value + 1)}>Try again</button></div>
        : current.application ? <CandidateProfile key={current.application.id} token={token} application={current.application}
          onSource={() => { window.open(screeningUrl, '_blank', 'noopener,noreferrer') }} />
          : <p>No linked candidate application was found. Open CV Screening to check the saved source.</p>}
      {current.screeningError && <button className="button-secondary" onClick={() => setRevision(value => value + 1)}>Retry screening details</button>}
    </>}
  </div>
}
