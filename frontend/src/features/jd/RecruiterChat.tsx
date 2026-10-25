import { Fragment, useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { Ref } from 'react'

import ChatInput from '../../components/chat/ChatInput'
import ChatMessage from '../../components/chat/ChatMessage'
import ChatWindow from '../../components/chat/ChatWindow'
import TypingIndicator from '../../components/motion/TypingIndicator'
import ConversationHistory from './ConversationHistory'
import { ApiError } from '../../services/api'
import {
  createAssistant,
  getAssistant,
  sendAssistantMessage,
  openSavedJD,
  sendAssistantAction,
} from './assistantApi'
import type { AssistantConversation } from './assistantApi'
import {
  usePersistentAssistantConversation,
} from './usePersistentAssistantConversation'

type RecruiterChatProps = {
  token: string
  userId: string
  onSaved: () => void
  interviewSessionId?: string
  interviewCandidateName?: string
  conversationScope?: string
  startFresh?: boolean
  compact?: boolean
  ref?: Ref<RecruiterChatHandle>
  onStateChange?: (conversation: AssistantConversation | null, busy: boolean) => void
  onReviewTab?: (tab: 'description' | 'criteria') => void
}

export type RecruiterChatHandle = {
  reload: () => void
  openJob: (id: string) => void
  startNew: () => void
  approve: () => void
  send: (message: string) => void
}

export default function RecruiterChat({
  token,
  userId,
  onSaved,
  interviewSessionId,
  conversationScope,
  startFresh = false,
  compact = false,
  ref,
  onStateChange,
  onReviewTab,
}: RecruiterChatProps) {
  const {
    conversation,
    setConversation,
    restoring,
    restoreError,
    resetConversation,
    retryRestore,
  } = usePersistentAssistantConversation(token, userId, interviewSessionId ? `interview:${interviewSessionId}` : conversationScope, !startFresh)

  const draftStorageKey = `distill-ai:assistant-drafts:${userId}:${interviewSessionId ?? conversationScope ?? 'jobs'}`
  const [drafts, setDrafts] = useState<Record<string, string>>(() => {
    try {
      const saved: unknown = JSON.parse(sessionStorage.getItem(draftStorageKey) ?? '{}')
      if (saved && typeof saved === 'object' && !Array.isArray(saved)) return Object.fromEntries(Object.entries(saved).filter(([, value]) => typeof value === 'string'))
    } catch { /* Draft storage may be disabled. */ }
    return {}
  })
  const draftKey = conversation?.conversation_id ?? 'new'
  const input = drafts[draftKey] ?? ''
  function setInput(value: string) { setDrafts(current => ({ ...current, [draftKey]: value })) }
  useEffect(() => {
    try { sessionStorage.setItem(draftStorageKey, JSON.stringify(drafts)) } catch { /* Continue composing without browser storage. */ }
  }, [draftStorageKey, drafts])
  const [busy, setBusy] = useState(false)
  const [stopped, setStopped] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pendingMessage, setPendingMessage] = useState<string | null>(null)
  const [replyText, setReplyText] = useState('')

  const requestRef = useRef<AbortController | null>(null)
  const interviewLogRef = useRef<HTMLDivElement | null>(null)
  const isBusy = busy || restoring

  useEffect(() => { onStateChange?.(conversation, isBusy) }, [conversation, isBusy, onStateChange])

  useImperativeHandle(ref, () => ({
    send: sendMessage,
    reload: () => { if (conversation) void runRequest(signal => getAssistant(token, conversation.conversation_id, signal)) },
    openJob: (id) => { void runRequest(signal => openSavedJD(token, id, signal)) },
    startNew: startNewConversation,
    approve: () => {
      if (conversation) void runRequest(signal => sendAssistantAction(token, conversation, 'approve', undefined, signal))
    },
  }))

  useEffect(() => {
    return () => requestRef.current?.abort()
  }, [])

  useEffect(() => {
    if (interviewSessionId && interviewLogRef.current) {
      interviewLogRef.current.scrollTop = interviewLogRef.current.scrollHeight
    }
  }, [interviewSessionId, conversation?.checkpoint_id, busy, replyText])

  const jd = interviewSessionId ? null : conversation?.jd_conversation
  const allowedActions = jd?.interrupt?.payload.allowed_actions ?? []

  const canReply = allowedActions.includes('reply')
  const canRevise = allowedActions.includes('revise')
  const canRecoverSave = jd?.status === 'approved' && !jd.job_description

  const canCompose =
    !restoring &&
    !restoreError &&
    !stopped &&
    (
      !conversation ||
      conversation.mode === 'chat' ||
      canReply ||
      canRevise ||
      canRecoverSave ||
      jd?.status === 'failed'
    )

  const document = jd?.approved_jd || jd?.draft_jd

  async function runRequest(
    operation: (signal: AbortSignal) => Promise<AssistantConversation>,
    clearInput = false,
  ) {
    if (requestRef.current || restoring || restoreError) return

    const controller = new AbortController()
    requestRef.current = controller
    setBusy(true)
    setError(null)
    setReplyText('')

    try {
      const updated = await operation(controller.signal)

      if (controller.signal.aborted) return

      setConversation(updated)
      setStopped(false)

      const saved = updated.jd_conversation?.job_description

      if (saved && saved.id !== jd?.job_description?.id) {
        onSaved()
      }

      if (updated.error_code) {
        setError(
          updated.error_code === 'ASSISTANT_MODEL_RATE_LIMITED'
            ? 'The model provider is rate-limiting requests. Your conversation is saved. Try again when the provider limit resets.'
            : `Assistant request failed: ${updated.error_code}. Reload before retrying.`,
        )
      } else if (clearInput) {
        setInput('')
      }
    } catch (caught) {
      if (controller.signal.aborted) return

      if (caught instanceof ApiError && caught.status === 401) {
        setError('Your session expired. Sign out and sign in again.')
      } else if (
        caught instanceof ApiError &&
        caught.status === 409 &&
        conversation
      ) {
        try {
          const updated = await getAssistant(
            token,
            conversation.conversation_id,
            controller.signal,
          )

          if (!controller.signal.aborted) {
            setConversation(updated)
            setError(
              interviewSessionId ? caught.message : 'The conversation was updated. Review it before continuing.',
            )
          }
        } catch {
          if (!controller.signal.aborted) {
            setError(
              'Unable to reload. Use Reload conversation before retrying.',
            )
          }
        }
      } else {
        setStopped(true)
        setError(
          caught instanceof Error
            ? caught.message
            : 'Unable to complete the request. Reload before retrying.',
        )
      }
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null
        setBusy(false)
        setPendingMessage(null)
        setReplyText('')
      }
    }
  }

  function sendMessage(suggested?: string) {
    const message = (suggested ?? input).trim()

    if (!message || requestRef.current || !canCompose) return
    if (suggested) setInput(suggested)

    void runRequest(async (signal) => {
      setPendingMessage(message)
      let current = conversation

      if (!current) {
        current = await createAssistant(token, signal)

        if (signal.aborted) return current

        // Remember the server ID before running the LLM request.
        setConversation(current)
      }

      return sendAssistantMessage(token, current, message, signal, interviewSessionId, text => {
        if (!signal.aborted) setReplyText(text)
      })
    }, true)
  }

  function stopWaiting() {
    requestRef.current?.abort()
    requestRef.current = null
    setBusy(false)
    setPendingMessage(null)
    setReplyText('')
    setStopped(true)
    setError('Stopped waiting for this reply. Reload chat to check whether it completed.')
  }

  // Partial text is a preview. Only the final server snapshot enters history.
  // Suppress per-token live announcements; the completed message is announced
  // by the chat log when it replaces this preview.
  const pendingMessages = pendingMessage && (
    <div aria-live="off" aria-label="Reply in progress" className="space-y-4">
      <ChatMessage role="user" content={pendingMessage} />
      {replyText && <ChatMessage role="assistant" content={replyText} name="Distill AI · writing…" />}
    </div>
  )

  function startNewConversation() {
    if (requestRef.current || restoring) return

    resetConversation()
    setDrafts(current => ({ ...current, new: '' }))
    setError(null)
    setStopped(false)
  }

  const messages = conversation?.messages ?? []

  const savedMessageIndex = messages.findIndex(
    (message) =>
      message.role === 'assistant' &&
      message.content.startsWith('Approved job description saved:'),
  )

  const beforeJD =
    savedMessageIndex < 0
      ? messages
      : messages.slice(0, savedMessageIndex)

  const afterJD =
    savedMessageIndex < 0
      ? []
      : messages.slice(savedMessageIndex)

  // The initial creation request appears in both graphs.
  // Display it once in the central conversation.
  const jdMessages = (jd?.messages ?? []).filter(
    (message, index) => !(
      index === 0 &&
      message.role === 'user' &&
      messages.some(
        (parent) =>
          parent.role === 'user' &&
          parent.content === message.content,
      )
    ),
  )

  if (interviewSessionId) return <div className="stage-chat">
    <header><span className="stage-badge"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m3 3 18 18M10 5h2c6 0 10 7 10 7a20 20 0 0 1-4 4M6 6a20 20 0 0 0-4 6s4 7 10 7c2 0 4-.6 6-2" /><path d="M9 9a4 4 0 0 0 6 6" /></svg>Only you can see this chat</span><button className="stage-outline" disabled={isBusy} onClick={startNewConversation}>New chat</button></header>
    <div ref={interviewLogRef} className={`stage-chat-log${!messages.length ? ' is-empty' : ''}`} role="log" aria-label="Interview assistant" aria-busy={isBusy}>
      {!messages.length && !isBusy && !restoreError && <div className="stage-assistant-welcome"><strong>Ask the assistant about this interview</strong><p>It uses the job description and this candidate’s screening data. It can make mistakes, so check anything important.</p><div>{['Suggest follow-up questions', 'Which criteria should I probe?', 'Compare the CV with this job'].map(prompt => <button className="stage-outline" key={prompt} disabled={!canCompose} onClick={() => sendMessage(prompt)}>{prompt}</button>)}</div></div>}
      {messages.map((message, index) => <ChatMessage key={message.id ?? index} role={message.role} content={message.content} />)}
      {pendingMessages}
      {isBusy && <div className="stage-reply-status"><p role="status">{restoring ? 'Restoring chat…' : 'The assistant is writing…'}<TypingIndicator /></p>{busy && <button className="stage-outline" onClick={() => {
        stopWaiting()
      }}>Stop waiting</button>}</div>}
      {(error || restoreError) && <div role="alert"><p>{error ?? restoreError}</p><button className="stage-outline" disabled={isBusy} onClick={restoreError ? retryRestore : () => { if (conversation) void runRequest(signal => getAssistant(token, conversation.conversation_id, signal)); else { setError(null); setStopped(false) } }}>{restoreError ? 'Try again' : 'Reload chat'}</button></div>}
    </div>
    <footer><label>Message to the assistant</label><ChatInput value={input} onChange={setInput} onSend={sendMessage} disabled={isBusy || !canCompose} placeholder="Ask while you interview" /></footer>
  </div>

  return (
    <div className="jd-chat jd-assistant-layout">
      {!compact && <ConversationHistory token={token} activeId={conversation?.conversation_id}
        jdOnly={Boolean(onReviewTab)}
        refreshKey={conversation?.checkpoint_id ?? 'new'} disabled={isBusy || Boolean(restoreError)}
        onSelect={(id) => { void runRequest((signal) => getAssistant(token, id, signal)) }} />}
      <ChatWindow
        title="Distill AI Assistant"
        busy={isBusy}
        scrollKey={
          `${conversation?.checkpoint_id ?? 'new'}:` +
          `${jd?.checkpoint_id ?? ''}:${isBusy}:${replyText.length}`
        }
        actions={
          <>
            <div className="flex flex-wrap gap-2">
              {busy && <button type="button" className="button-secondary" onClick={stopWaiting}>Stop waiting</button>}
              {restoreError && (
                <button
                  type="button"
                  className="button-secondary"
                  disabled={isBusy}
                  onClick={retryRestore}
                >
                  Retry restoration
                </button>
              )}

              {conversation && (
                <button
                  type="button"
                  className="button-secondary"
                  disabled={isBusy}
                  onClick={() => {
                    void runRequest(
                      (signal) => getAssistant(
                        token,
                        conversation.conversation_id,
                        signal,
                      ),
                    )
                  }}
                >
                  Reload conversation
                </button>
              )}

            </div>
          </>
        }
        composer={
          canCompose ? (
            <ChatInput
              value={input}
              onChange={setInput}
              onSend={sendMessage}
              disabled={isBusy}
              placeholder={
                interviewSessionId
                  ? 'Suggest questions I can ask this candidate...'
                  : canRevise
                  ? 'Request changes, accept and save, or cancel...'
                  : canReply
                    ? 'Answer the JD question...'
                    : 'Ask a question or request a job description...'
              }
            />
          ) : undefined
        }
      >
        {interviewSessionId && conversation?.mode === 'jd_workflow' && (
          <p role="status" className="text-sm text-slate-600">
            This conversation has an active JD workflow. Finish it in Job Descriptions,
            or choose New conversation here to ask interview questions.
          </p>
        )}
        {!conversation && !restoring && !restoreError && (
          <ChatMessage
            role="assistant"
            content="Hello! I’m your Distill AI assistant. How can I help you?"
          />
        )}

        {beforeJD.filter((message) => !message.jd_after_message_id).map((message, index) => (
          <ChatMessage
            key={message.id ?? `assistant-before-${index}`}
            role={message.role}
            content={message.content}
          />
        ))}

        {jdMessages.map((message, index) => (
          <Fragment key={message.id ?? `jd-message-${index}`}>
          {onReviewTab && message.draft_revision !== undefined ? <div className="jd-chat-draft-notice"><strong>{jd?.status === 'approved' ? 'Job description saved' : 'Draft saved · not approved'}</strong><p>Review the description and screening criteria before using this job for screening.</p><button className="button-secondary" onClick={() => onReviewTab('description')}>Open Description</button> <button className="button-secondary" onClick={() => onReviewTab('criteria')}>Open Screening criteria</button></div> : <ChatMessage
            key={message.id ?? `jd-message-${index}`}
            role={message.role}
            content={message.content}
            name={message.draft_revision !== undefined ? (message.draft_revision === 0 ? 'Suggested JD — initial draft' : `Suggested JD — revision ${message.draft_revision}`) : undefined}
          />}
          {messages.filter((entry) => entry.jd_after_message_id === message.id).map((entry, entryIndex) => (
            <ChatMessage key={entry.id ?? `context-${index}-${entryIndex}`} role={entry.role} content={entry.content} />
          ))}
          </Fragment>
        ))}
        {messages.filter((message) => message.jd_after_message_id === 'jd-end').map((message, index) => (
          <ChatMessage key={message.id ?? `jd-end-${index}`} role={message.role} content={message.content} />
        ))}

        {jd?.rag_status === 'retrieved' && (
          <div
            className="rounded-xl border border-emerald-100
              bg-emerald-50 px-4 py-3"
          >
            <p className="text-xs font-semibold text-emerald-800">
              Reference excerpts supplied to the JD workflow
            </p>

            <p className="mt-1 break-words text-xs text-emerald-700">
              {[
                ...new Set(
                  (jd.reference_sources ?? []).map(
                    (source) => source.filename,
                  ),
                ),
              ].join(', ')}
            </p>
          </div>
        )}

        {!onReviewTab && document && !jdMessages.some((message) => message.draft_revision !== undefined && message.content === document) && (
          <ChatMessage
            role="assistant"
            name={
              jd?.status === 'approved'
                ? 'Approved job description'
                : 'Draft job description'
            }
            content={document}
          />
        )}

        {canReply && <ChatMessage role="assistant" content="Reply with the job details, or say **cancel** to stop this workflow." />}
        {canRecoverSave && <ChatMessage role="assistant" content="Your JD was approved but its save needs to be recovered. Say **save** to retry." />}

        {document && jd?.screening_profile && !onReviewTab && (
          <div className="rounded-xl border border-slate-200 p-4 text-sm">
            <p className="font-semibold">Screening profile — version {jd.profile_version}</p>
            <p className="text-slate-500">Recruiter review only. Dimension weights are excluded from the public advertisement.</p>
            <table className="mt-3 w-full text-left">
              <thead><tr><th>ID</th><th>Dimension</th><th>Criterion</th><th>Priority</th></tr></thead>
              <tbody>{jd.screening_profile.criteria.map((criterion) => (
                <tr key={criterion.id}>
                  <td className="break-all pr-2">{criterion.id}</td>
                  <td className="pr-2">{criterion.dimension.replaceAll('_', ' ')}</td>
                  <td className="pr-2">{criterion.text}{criterion.source === 'suggested' && <span className="block text-xs text-slate-500">Suggested from role knowledge — review this criterion</span>}</td>
                  <td>{criterion.priority ?? 'Needs confirmation'}{criterion.priority_source === 'suggested' && <span className="block text-xs text-slate-500">AI suggested</span>}</td>
                </tr>
              ))}</tbody>
            </table>
            <p className="mt-3">Minimum experience: {jd.screening_profile.minimum_years_experience ?? 'Unspecified'}</p>
            <p>Fresher eligibility: {jd.screening_profile.fresher_eligibility ?? 'Unspecified'}</p>
            <p className="mt-3 font-semibold">Dimension weights {jd.screening_profile.weights_confirmed ? '(confirmed)' : jd.screening_profile.weights_source === 'suggested' ? '(AI suggested; included in JD approval)' : '(needs confirmation)'}</p>
            {Object.entries(jd.screening_profile.dimension_weights).map(([dimension, weight]) => (
              <p key={dimension}>{dimension.replaceAll('_', ' ')}: {weight}%</p>
            ))}
            <p>Google Form: {jd.screening_profile.google_form_url ?? 'Not provided (optional)'}</p>
            <p className="text-slate-500">URL format is validated; form accessibility and configuration are not checked.</p>
          </div>
        )}

        {onReviewTab && document && !jdMessages.some(message => message.draft_revision !== undefined) && <div className="jd-chat-draft-notice"><strong>{jd?.status === 'approved' ? 'Approved job description' : 'Draft · not approved'}</strong><p>{jd?.status === 'approved' ? 'View the saved description and the criteria used for screening.' : 'Candidates cannot be screened against this draft until you approve it.'}</p><button className="button-secondary" onClick={() => onReviewTab('description')}>Open Description</button> <button className="button-secondary" onClick={() => onReviewTab('criteria')}>Open Screening criteria</button></div>}

        {jd?.status === 'awaiting_review' && (
          <ChatMessage
            role="assistant"
            content={'Review the JD and screening profile above. AI suggestions are marked. Tell me what to add, change, or remove. Say **accept and save** to approve the JD together with the displayed criteria, priorities, and weights, or **cancel** to discard this workflow.'}
          />
        )}

        {jd?.status === 'awaiting_review' && (jd.acceptance_errors ?? []).length > 0 && (
          <ChatMessage role="assistant" content={'Before I can save this JD, please send the missing details:\n\n' + jd.acceptance_errors.map((message) => `- ${message}`).join('\n')} />
        )}
        {document && (jd?.screening_warnings ?? []).length > 0 && (
          <ChatMessage role="assistant" content={(jd?.status === 'approved' ? 'This JD was saved with incomplete requirements.' : 'You can save this JD now, even with incomplete requirements.') + ' These details remain unresolved:\n\n' + (jd?.screening_warnings ?? []).map((message) => `- ${message}`).join('\n')} />
        )}

        {jd?.status === 'approved' && (
          <p role="status" className="text-sm text-emerald-700">
            {jd.job_description
              ? `Saved Job #${jd.job_description.number}: ${jd.job_description.title} — ${
                  new Date(
                    jd.job_description.created_at,
                  ).toLocaleString('en-GB', {
                    timeZone: 'Asia/Dhaka',
                  })
                } (Bangladesh time)`
              : 'Approved. Recover the save before continuing.'}
          </p>
        )}

        {jd?.status === 'cancelled' && (
          <p role="status" className="text-sm text-slate-500">
            JD workflow cancelled. Start a new conversation for another JD.
          </p>
        )}

        {jd?.status === 'failed' && (
          <p role="alert" className="text-sm text-red-700">
            {jd.error_code === 'JD_MODEL_RATE_LIMITED'
              ? 'The model provider is rate-limiting requests. Your conversation is saved. Try again later by asking me to retry, or cancel this workflow.'
              : 'The last draft attempt failed. Ask me to retry, or cancel this workflow.'}
          </p>
        )}

        {afterJD.filter((message) => !message.jd_after_message_id).map((message, index) => (
          <ChatMessage
            key={message.id ?? `assistant-after-${index}`}
            role={message.role}
            content={message.content}
          />
        ))}

        {pendingMessages}

        {(error || restoreError) && (
          <p role="alert" className="text-sm text-red-700">
            {error || restoreError}
          </p>
        )}
      </ChatWindow>
    </div>
  )
}
