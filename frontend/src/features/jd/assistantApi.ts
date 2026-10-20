import { apiRequest } from '../../services/api'
import { streamChat } from '../../services/chatStream'
import type { JDAction, JDConversation } from './api'

export type AssistantConversation = {
  conversation_id: string
  created_at: string
  checkpoint_id: string | null
  mode: 'chat' | 'jd_workflow'
  messages: JDConversation['messages']
  error_code: string | null
  jd_conversation: JDConversation | null
}

export function createAssistant(
  token: string,
  signal?: AbortSignal,
) {
  return apiRequest<AssistantConversation>(
    '/assistant/conversations',
    {
      method: 'POST',
      token,
      signal,
    },
  )
}

export function getAssistant(
  token: string,
  conversationId: string,
  signal?: AbortSignal,
) {
  return apiRequest<AssistantConversation>(
    `/assistant/conversations/${conversationId}`,
    { token, signal },
  )
}

export function openSavedJD(
  token: string,
  descriptionId: string,
  signal?: AbortSignal,
) {
  return apiRequest<AssistantConversation>(
    `/assistant/job-descriptions/${descriptionId}/conversation`,
    {
      method: 'POST',
      token,
      signal,
    },
  )
}

export function sendAssistantMessage(
  token: string,
  conversation: AssistantConversation,
  message: string,
  signal?: AbortSignal,
  interviewSessionId?: string,
  onText?: (text: string) => void,
) {
  return streamChat<AssistantConversation>(
    `/assistant/conversations/${conversation.conversation_id}/messages`,
    token,
    {
      message,
      checkpoint_id: conversation.checkpoint_id,
      ...(interviewSessionId ? { interview_session_id: interviewSessionId } : {}),
    },
    signal,
    onText,
  )
}

export function sendAssistantAction(
  token: string,
  conversation: AssistantConversation,
  action: JDAction,
  message?: string,
  signal?: AbortSignal,
  onText?: (text: string) => void,
) {
  const jd = conversation.jd_conversation

  if (!jd?.checkpoint_id) {
    throw new Error('Reload the JD conversation before continuing.')
  }

  const recoveringSave =
    action === 'approve' &&
    jd.status === 'approved' &&
    !jd.job_description

  if (!jd.interrupt && !recoveringSave) {
    throw new Error('No JD action is pending.')
  }

  return streamChat<AssistantConversation>(
    `/assistant/conversations/${conversation.conversation_id}/jd-actions`,
    token,
    {
      checkpoint_id: conversation.checkpoint_id,
      jd_checkpoint_id: jd.checkpoint_id,
      interrupt_id: jd.interrupt?.id ?? 'approval-recovery',
      action,
      ...(message !== undefined ? { message } : {}),
    },
    signal,
    onText,
  )
}

export type ConversationSummary = {
  conversation_id: string
  created_at: string
  title: string
}

export function listAssistantConversations(token: string, offset = 0, signal?: AbortSignal) {
  return apiRequest<{ items: ConversationSummary[]; limit: number; offset: number }>(
    `/assistant/conversations?limit=20&offset=${offset}`, { token, signal },
  )
}
