import type { WindTunnelBatch, WindTunnelSuggestion } from './windTunnelApi'
import type { ScreeningProfile } from './api'

export type BatchView = {
  label: string; tone: 'success' | 'warning' | 'danger' | 'info' | 'neutral'
  title?: string; message?: string; retry?: boolean; retryLabel?: string; destination?: 'description' | 'criteria'
}

export function batchView(batch: WindTunnelBatch): BatchView {
  if (batch.status === 'queued') return { label: 'Queued', tone: 'info', title: 'Waiting for the background worker.', message: 'This batch will start on its own. You can leave this page; it stays queued.' }
  if (batch.status === 'running') return { label: 'Running', tone: 'info', title: 'Analysis is underway.', message: 'Leaving this page will not stop it. Suggestions will appear here when they are ready.' }
  const applied = batch.suggestions.some(suggestion => suggestion.status === 'applied')
  const allApplied = batch.suggestions.length > 0 && batch.suggestions.every(suggestion => suggestion.status === 'applied')
  if ((batch.stale || batch.status === 'stale') && !allApplied) {
    return { label: 'Stale', tone: 'warning', title: 'The job changed after this batch.', message: 'These suggestions cannot be applied to the current version. Request a new batch with More suggestions.' }
  }
  if (batch.error_code === 'SUGGESTION_INPUT_TOO_LARGE') return { label: 'Limited results', tone: 'warning', title: 'Input too large.', message: 'The suggestion input exceeded its size limit. Completed assessments are saved. Request More suggestions to use the current settings. Retrying this batch unchanged may fail the same way.', destination: 'description' }
  if (batch.error_code === 'DIAGNOSTIC_CONFIG_CHANGED_REQUEST_MORE' || batch.error_code === 'SUGGESTION_CONFIG_MISMATCH') return { label: 'Limited results', tone: 'warning', title: 'Configuration changed.', message: 'The analysis settings changed after this batch started, so it cannot continue. Start a new batch with More suggestions.' }
  if (batch.error_code === 'SUGGESTION_SOURCE_STALE') return { label: 'Limited results', tone: 'warning', title: 'Job changed.', message: 'You saved changes after this batch started. Request suggestions for the current version.' }
  if (batch.status === 'cancelled') return { label: 'Cancelled', tone: 'neutral', title: 'You cancelled this batch.', message: 'Further processing has been stopped. Saved work remains and nothing about your job changed.' }
  if (batch.generation_failures?.keyword_stuffer === 'CV_KEYWORD_PROBE_UNAVAILABLE') return { label: 'Limited results', tone: 'warning', title: 'This profile is not supported by the current rehearsal.', message: 'This profile has no technical skill criteria for the keyword-only test. The current rehearsal cannot complete the required CV assessments for this profile. Saved results remain available.' }
  if (batch.error_code === 'SUGGESTION_OUTPUT_INVALID') return { label: 'Failed', tone: 'danger', title: 'The AI response could not be validated.', message: 'The CV analysis was saved, but the proposed changes were not usable. Choose More suggestions to start a batch with the latest settings.' }
  if (batch.needs_new_batch && ['limited', 'failed'].includes(batch.status)) return { label: 'New batch needed', tone: 'warning', title: 'Updated analysis settings are available.', message: 'Choose More suggestions to use the current analysis settings. This batch keeps its original settings; completed results remain in history.' }
  if (batch.error_code === 'SUGGESTION_SEMANTIC_REVIEW_FAILED') {
    const reasons: Record<string, string> = {
      adds_evidence_burden: 'The proposed wording introduced a stronger experience or evidence requirement.',
      adds_requirement: 'The proposed wording added a requirement that was not in the saved job.',
      unsupported_source: 'The proposed wording was not supported by the saved job.',
      prose_profile_conflict: 'The proposed description and screening criteria were inconsistent.',
      unsupported_outcome_claim: 'The proposed changes claimed benefits that were not established by the analysis.',
      ungrouped_changes: 'Related changes were not grouped together for joint review.',
      no_meaningful_change: 'The proposed wording did not offer a meaningful improvement.',
    }
    const explanation = [...new Set(batch.review_rejection_reasons ?? [])].map(code => reasons[code]).filter(Boolean).join(' ')
    const complete = !!batch.coverage && batch.coverage.expected_assessments > 0 && batch.coverage.completed_assessments === batch.coverage.expected_assessments
    return { label: 'No changes approved', tone: 'info', title: complete ? 'CV analysis completed; no changes approved.' : 'No changes approved.',
      message: `The proposed changes did not pass all review checks. ${explanation ? `${explanation} ` : ''}Your job is unchanged. Saved analysis is retained for another attempt.`,
      retry: true, retryLabel: 'Try suggestions again', destination: 'criteria' }
  }
  if (batch.status === 'failed') return { label: 'Failed', tone: 'danger', title: 'Suggestions could not be generated.', message: 'This batch stopped before it completed. Saved work is preserved and nothing about your job changed.', retry: batch.error_code !== 'DIAGNOSTIC_OWNER_UNAVAILABLE' }
  if (batch.error_code === 'SUGGESTION_NO_NEW_EDITS') return { label: 'Complete', tone: 'success', title: 'No new exact edits.', message: 'The proposed edits repeat suggestions already in your batch history. Review those suggestions below.' }
  if (batch.status === 'limited') return { label: 'Limited results', tone: 'warning', title: batch.error_code === 'SUGGESTION_ANALYSIS_INCOMPLETE' ? 'Analysis incomplete.' : 'The analysis produced limited results.', message: batch.error_code === 'SUGGESTION_ANALYSIS_INCOMPLETE' ? 'Some synthetic CVs or assessments could not finish. Saved work is preserved, so you can resume the batch.' : 'This batch could not identify enough supported findings. Request a fresh batch to try again.', retry: batch.error_code === 'SUGGESTION_ANALYSIS_INCOMPLETE' }
  if (batch.needs_new_batch) return { label: 'New batch needed', tone: 'info', title: 'Generate exact proposed changes.', message: 'This older batch produced a question rather than an edit you can apply. Choose More suggestions to generate exact changes with the updated feature.' }
  if (!batch.suggestions.length) return { label: 'Complete', tone: 'success', title: 'Analysis complete.', message: 'This batch did not identify any supported changes to suggest. You can review your criteria or request another batch.', destination: 'criteria' }
  if (batch.requested_suggestions && batch.suggestions.length < batch.requested_suggestions) return {
    label: allApplied ? 'Applied' : 'Ready', tone: 'info', title: `${batch.suggestions.length} of ${batch.requested_suggestions} requested suggestions approved.`,
    message: 'Only these changes passed validation and review. The remaining proposals could not be approved within this batch. You can review the approved changes below.' }
  return { label: applied && batch.suggestions.every(suggestion => suggestion.status === 'applied') ? 'Applied' : 'Ready', tone: 'success' }
}

export function batchDate(date: string) {
  const value = new Date(date)
  return `${value.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}, ${value.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`
}

export function displayValue(value: unknown): string {
  if (value == null || value === '') return 'Not set'
  if (typeof value === 'object') return Object.entries(value).map(([key, entry]) => `${key.replaceAll('_', ' ')}: ${entry}%`).join(' · ')
  return value === 'Must have' ? 'Essential' : String(value)
}

export function patchLabel(patch: WindTunnelSuggestion['patches'][number], profile: ScreeningProfile | null) {
  if (patch.kind === 'description') return 'Description · wording'
  if (patch.kind === 'weights') return 'Screening criteria · dimension weights'
  if (patch.kind === 'metadata') return `Screening criteria · ${patch.field?.replaceAll('_', ' ')}`
  const criterion = profile?.criteria.find(item => item.id === patch.criterion_id)
  return `Criterion · ${criterion?.text ?? 'Saved requirement'} · ${patch.field ?? 'wording'}`
}

export function generationFailure(code: string): string {
  const messages: Record<string, string> = {
    CV_PROSE_INVALID: 'The generated CV did not meet document format limits.',
    CV_DRAFT_INVALID: 'The model returned an invalid CV structure.',
    CV_REVIEW_SCHEMA_INVALID: 'The reviewer could not return a valid evidence response after repair.',
    CV_SUPPORT_INVALID: 'The reviewer could not provide consistent supporting evidence after repair.',
    CV_REVIEW_EVIDENCE_INVALID: 'The review referenced text or passages outside this CV.',
    CV_REVIEW_COVERAGE_INVALID: 'The review did not cover every screening criterion.',
    CV_PERSONA_DIFFERENCE_INVALID: 'The CV did not match its intended test scenario.',
    CV_FAITHFULNESS_INVALID: 'The generated CV did not preserve the required facts.',
    CV_BASELINE_UNAVAILABLE: 'A valid all-rounder CV is needed before this variant can be created.',
    CV_KEYWORD_PROBE_UNAVAILABLE: 'This profile has no technical skill criteria for the keyword-only test.',
    CV_GENERATION_PROVIDER_FAILED: 'The model service was unavailable. Saved progress is retained.',
  }
  return messages[code] ?? 'This synthetic CV could not finish validation. Saved progress is retained.'
}
