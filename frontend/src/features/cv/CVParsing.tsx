import { sourceLabel, statusLabel, warningLabel } from './parsingLabels'
import { useEffect, useRef, useState } from 'react'
import { getCVProfile, parseCV, parseJobCVs } from './api'
import type { ParseResult } from './api'

const terminal = new Set(['completed', 'needs_review', 'failed', 'not_started'])

function ProfileValue({ value }: { value: unknown }) {
  if (value == null) return <span>Unknown</span>
  if (Array.isArray(value)) return value.length ? <ul>{value.map((item, index) => <li key={index}><ProfileValue value={item} /></li>)}</ul> : <span>Not stated</span>
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    if ('value' in record) return <span>{String(record.value)}</span>
    return <dl>{Object.entries(record).map(([key, item]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd><ProfileValue value={item} /></dd></div>)}</dl>
  }
  return <span>{String(value)}</span>
}

export function CVParseControl({ token, versionId, enabled, filename, readOnly = false }: { token: string; versionId: string; enabled: boolean; filename: string; readOnly?: boolean }) {
  const [result, setResult] = useState<ParseResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const lock = useRef(false)
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const next = await getCVProfile(token, versionId, controller.signal)
        if (!controller.signal.aborted) {
          setResult(next)
          timer = setTimeout(() => void poll(), busy || !terminal.has(next.status) ? 2500 : 10000)
        }
      } catch (caught) {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Could not load parsing status.')
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, versionId, busy])

  async function parse() {
    if (lock.current) return
    lock.current = true
    setBusy(true); setError('')
    try { setResult(await parseCV(token, versionId)) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Parsing failed.') }
    finally { lock.current = false; setBusy(false) }
  }
  return <div className="cv-parsing" data-file-version-id={versionId}>
    <strong className="cv-parsing-filename">{filename}</strong>
    {!readOnly && <button className="button-secondary" disabled={!enabled || busy} onClick={() => void parse()}>{busy ? 'Processing…' : result?.status === 'completed' ? 'Check saved parse' : 'Parse / resume CV'}</button>}
    <p role="status">{result ? statusLabel(result.status) : 'Loading status…'}{result && ` · OCR ${result.ocr_used ? 'used' : 'not used'}`}</p>
    {error && <p className="cv-error" role="alert">{error}</p>}
    {result?.error_code && <p className="cv-error">{warningLabel(result.error_code)}</p>}
    {!!result?.warnings.length && <ul>{result.warnings.map(warning => <li key={warning}>{warningLabel(warning)}</li>)}</ul>}
    {result?.profile && <details><summary>View CV profile and evidence</summary>
      <ProfileValue value={result.profile} />
      <h4>Supporting excerpts</h4>
      {result.evidence.map((item, index) => <blockquote key={index}><p>{item.quote}</p><small>{item.claim_path.replaceAll('_', ' ').replaceAll('.', ' › ')} · {sourceLabel(item.source_id)}{item.ocr_derived ? ' · OCR-derived' : ''}</small></blockquote>)}
    </details>}
  </div>
}

export function CVBatchParse({ token, jobId, onFinished }: { token: string; jobId: string; onFinished: () => void }) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const lock = useRef(false)
  const stop = useRef(false)
  useEffect(() => () => { stop.current = true }, [])
  async function run() {
    if (lock.current) return
    lock.current = true; stop.current = false; setBusy(true)
    let offset: number | null = 0, processed = 0
    try {
      while (offset !== null && !stop.current) {
        setMessage(`Processing CV ${processed + 1}…`)
        const next = await parseJobCVs(token, jobId, offset)
        processed += next.results.length
        const last = next.results.at(-1)
        setMessage(`${processed} processed${last ? ` · latest: ${statusLabel(last.status)}${last.error_code ? ` (${warningLabel(last.error_code)})` : ''}` : ''}`)
        offset = next.next_offset
      }
    } catch (caught) { setMessage(caught instanceof Error ? caught.message : 'Batch failed.') }
    finally { lock.current = false; setBusy(false); onFinished() }
  }
  return <div className="cv-parsing cv-batch-parsing"><button className="button-secondary" disabled={busy} onClick={() => void run()}>{busy ? 'Parsing downloaded CVs…' : 'Parse downloaded CVs for this JD'}</button>
    {busy && <button className="button-secondary" onClick={() => { stop.current = true }}>Stop after current CV</button>}
    <p role="status">{message}</p></div>
}
