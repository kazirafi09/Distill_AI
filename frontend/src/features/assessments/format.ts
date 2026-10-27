export function scoreLabel(value: string | number | null | undefined) {
  if (value === null || value === undefined) return 'Not assessed'
  return `${Number(value).toFixed(Number(value) % 1 ? 2 : 0)}/100`
}

export function timeLabel(ms: number | null | undefined) {
  if (ms == null) return 'Timing unavailable'
  const seconds = Math.floor(ms / 1000)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

export function stageLabel(stage: string) {
  if (stage.startsWith('f6-repair')) return 'Repairing assessment'
  if (stage.startsWith('f6-')) return ({ 'f6-strengths': 'Analyzing strengths', 'f6-weaknesses': 'Analyzing weaknesses',
    'f6-evidence_gaps': 'Analyzing evidence gaps', 'f6-recommended_areas': 'Recommending verification areas',
    'f6-merge': 'Merging assessment', 'f6-validation': 'Validating traceability' } as Record<string, string>)[stage.replace(/-attempts$/, '')] ?? 'Consolidating assessment'
  if (stage.startsWith('evaluation-')) return 'Evaluating answers'
  return ({ queued: 'Queued', retry_wait: 'Waiting to retry', loading: 'Loading saved inputs',
    'model-version': 'Checking model', grouping: 'Grouping questions and answers', mapping: 'Mapping job requirements',
    completed: 'Completed', failed: 'Failed', needs_review: 'Needs review' } as Record<string, string>)[stage] ?? 'Processing'
}

export function errorLabel(code: string, validationError?: string | null) {
  if (code === 'F6_INPUT_TOO_LARGE') return 'The saved assessment evidence exceeds the consolidation input budget. F5 results are preserved.'
  if (code === 'F6_TRACEABILITY_NEEDS_REVIEW') {
    const reason = ({
      F6_FINDING_SUPPORT_INVALID: 'A finding cites evidence that does not support its classification as a strength, weakness or gap.',
      F6_SUITABILITY_OVERCONFIDENT: 'The suitability conclusion is too confident for the available evidence.',
      F6_CITATION_INVALID: 'A finding cites evidence that does not match its job requirement.',
      F6_SCHEMA_INVALID: 'The generated assessment has an invalid structure.',
    } as Record<string, string>)[validationError ?? ''] ?? 'The generated assessment failed evidence validation.'
    return `${reason} Two repair attempts did not resolve it. The full report and PDF are unavailable until validation passes.`
  }
  if (code === 'F6_SOURCE_CHANGED') return 'The pinned CV or interview result changed. Start a new compatible assessment.'
  return ({ MODEL_AUTH_FAILED: 'Model authentication failed. Check the API configuration before retrying.',
    MODEL_API_KEY_MISSING: 'Configure the model API key before retrying.',
    MODEL_RATE_LIMITED: 'The model rate limit was reached.', MODEL_NOT_AVAILABLE: 'The model is temporarily unavailable.',
    ANALYSIS_MODEL_TIMEOUT: 'The model request timed out.', ANALYSIS_MODEL_REQUEST_FAILED: 'The model request failed.',
    ANALYSIS_OUTPUT_NEEDS_REVIEW: 'Model output failed validation. Saved evidence is retained for review.',
    ANALYSIS_INPUT_TOO_LARGE: 'This transcript exceeds the current model context limit.',
    EXTENSION_INPUT_TOO_LARGE: 'The saved interview evidence exceeds the model request budget. Your Q&A results and original sources are preserved.',
    ASSESSMENT_VERSION_CHANGED: 'Processing instructions changed. Start a new assessment.',
    ASSESSMENT_ATTEMPTS_EXHAUSTED: 'The retry limit has been reached.',
    ASSESSMENT_SOURCE_CHANGED: 'The saved transcript changed and must be reviewed.',
    RECRUITER_ACCESS_REVOKED: 'Recruiter access is no longer available.' } as Record<string, string>)[code]
    ?? `Processing stopped (${code}). Saved work is retained.`
}
