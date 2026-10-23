import type { WindTunnelBatch, WindTunnelSuggestion } from './windTunnelApi'
import type { ScreeningProfile } from './api'

export type BatchView = {
  label: string; tone: 'success' | 'warning' | 'danger' | 'info' | 'neutral'
  title?: string; message?: string; retry?: boolean; destination?: 'description' | 'criteria'
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
  if (batch.status === 'failed') return { label: 'Failed', tone: 'danger', title: 'Suggestions could not be generated.', message: 'This batch stopped before it completed. Saved work is preserved and nothing about your job changed.', retry: batch.error_code !== 'DIAGNOSTIC_OWNER_UNAVAILABLE' }
  if (batch.status === 'limited') return { label: 'Limited results', tone: 'warning', title: batch.error_code === 'SUGGESTION_ANALYSIS_INCOMPLETE' ? 'Analysis incomplete.' : 'The analysis produced limited results.', message: batch.error_code === 'SUGGESTION_ANALYSIS_INCOMPLETE' ? 'Some synthetic CVs or assessments could not finish. Saved work is preserved, so you can resume the batch.' : 'This batch could not identify enough supported findings. Request a fresh batch to try again.', retry: batch.error_code === 'SUGGESTION_ANALYSIS_INCOMPLETE' }
  if (!batch.suggestions.length) return { label: 'Complete', tone: 'success', title: 'Analysis complete.', message: 'This batch did not identify any supported changes to suggest. You can review your criteria or request another batch.', destination: 'criteria' }
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
