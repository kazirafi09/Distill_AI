import { ApiError, apiResponse } from './api.ts'

type ChatEvent<T> =
  | { type: 'started' | 'reset' }
  | { type: 'delta'; text: string }
  | { type: 'complete'; conversation: T }
  | { type: 'error'; status: number; detail: string }

/** Read our single-line JSON SSE events, including split UTF-8 and network chunks. */
async function* readEvents<T>(body: ReadableStream<Uint8Array>) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffered = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffered += decoder.decode(value, { stream: !done })
      let newline: number
      while ((newline = buffered.indexOf('\n')) !== -1) {
        const line = buffered.slice(0, newline).trimEnd()
        buffered = buffered.slice(newline + 1)
        if (line.startsWith('data:')) yield JSON.parse(line.slice(5)) as ChatEvent<T>
      }
      if (done) break
    }
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}

export async function streamChat<T>(
  path: string,
  token: string,
  body: unknown,
  signal?: AbortSignal,
  onText?: (text: string) => void,
): Promise<T> {
  // Use the same authentication renewal and HTTP errors as every other API call.
  const response = await apiResponse(path, {
    method: 'POST', token, body, signal, accept: 'text/event-stream',
  })
  // Older servers can still return their complete JSON response.
  if (response.headers.get('Content-Type')?.includes('application/json')) return response.json() as Promise<T>
  if (!response.body || !response.headers.get('Content-Type')?.includes('text/event-stream')) {
    throw new Error('Unable to read the reply. Reload the conversation before retrying.')
  }

  let text = ''
  for await (const event of readEvents<T>(response.body)) {
    signal?.throwIfAborted()
    switch (event.type) {
      case 'reset':
        text = ''
        onText?.(text)
        break
      case 'delta':
        text += event.text
        onText?.(text)
        break
      case 'complete':
        return event.conversation
      case 'error':
        throw new ApiError(event.status, event.detail)
    }
  }
  // Never retry a POST automatically after streaming has begun: a tool may
  // already have run, even when the final receipt did not reach this browser.
  signal?.throwIfAborted()
  throw new Error('The reply was interrupted. Reload the conversation before retrying.')
}
