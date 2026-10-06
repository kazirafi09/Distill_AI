import { apiRequest } from '../../services/api'
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
) {
  return apiRequest<AssistantConversation>(
    `/assistant/conversations/${conversation.conversation_id}/messages`,
    {
      method: 'POST',
      token,
      signal,
      body: {
        message,
        checkpoint_id: conversation.checkpoint_id,
      },
    },
  )
}

export function sendAssistantAction(
  token: string,
  conversation: AssistantConversation,
  action: JDAction,
  message?: string,
  signal?: AbortSignal,
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

  return apiRequest<AssistantConversation>(
    `/assistant/conversations/${conversation.conversation_id}/jd-actions`,
    {
      method: 'POST',
      token,
      signal,
      body: {
        checkpoint_id: conversation.checkpoint_id,
        jd_checkpoint_id: jd.checkpoint_id,
        interrupt_id: jd.interrupt?.id ?? 'approval-recovery',
        action,
        ...(message !== undefined ? { message } : {}),
      },
    },
  )
}