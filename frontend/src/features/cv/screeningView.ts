import type { CandidateApplication } from './api'
import type { Assessment, Evidence } from './CVResults'

export function validScore(item?: Assessment) {
  const raw = item?.score?.aggregate
  if (raw == null || String(raw).trim() === '') return null
  const value = Number(raw)
  return Number.isFinite(value) && value >= 0 && value <= 100 ? value : null
}

export function candidateStatus(application: CandidateApplication, assessment?: Assessment) {
  if (assessment?.outdated) return { key: 'outdated', label: 'Older results', tone: 'is-info' }
  if (assessment?.status === 'completed' && validScore(assessment) !== null) return { key: 'screened', label: 'Screened', tone: 'is-success' }
  if (assessment?.status === 'failed' || assessment?.status === 'needs_review' || assessment?.error_code || application.cv_documents.some(cv => cv.download_status === 'failed')) return { key: 'issue', label: 'Processing issue', tone: 'is-danger' }
  if (assessment?.status === 'running' || assessment?.status === 'queued') return { key: 'waiting', label: 'Screening pending', tone: '' }
  return { key: 'waiting', label: 'Waiting to be screened', tone: '' }
}

export function reviewCount(item?: Assessment) {
  return item?.status === 'completed' && !item.outdated && validScore(item) !== null ? item.score?.must_have_review_flags?.length ?? 0 : 0
}

export function sourceLabel(source: string) {
  return source.replace(/^pdf-page-(\d+)$/, 'CV · page $1').replace(/^docx-image-(\d+)$/, 'CV · image $1').replace('docx-body', 'CV · document body')
}

export function evidenceLink(application: CandidateApplication, evidence?: Evidence) {
  const document = evidence ? application.cv_documents.find(cv => cv.file_version_id === evidence.file_version_id) : application.cv_documents[0]
  if (!document) return null
  // Google Drive opens the exact source file. It does not reliably support a page anchor.
  return `https://drive.google.com/file/d/${encodeURIComponent(document.drive_file_id)}/view`
}
