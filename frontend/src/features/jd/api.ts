import { apiRequest } from '../../services/api'

export type JDAction = 'reply' | 'revise' | 'approve' | 'cancel'

export type JDConversation = {
  conversation_id: string
  created_at: string
  checkpoint_id: string | null
  status: string
  messages: {
    id: string | null
    role: 'user' | 'assistant'
    content: string
  }[]
  interrupt: {
    id: string
    payload: {
      type?: 'requirements'
      question?: string
      draft_jd?: string
      revision_count?: number
      allowed_actions: JDAction[]
    }
  } | null
  draft_jd: string | null
  approved_jd: string | null
  revision_count: number
  error_code: string | null
  job_description?: {
    id: string
    title: string
    created_at: string
  } | null
}

export function createConversation(token: string, message: string) {
  return apiRequest<JDConversation>('/jd/conversations', {
    method: 'POST',
    token,
    body: { message },
  })
}

export function getConversation(
  token: string,
  conversationId: string,
  signal?: AbortSignal,
) {
  return apiRequest<JDConversation>(
    `/jd/conversations/${conversationId}`,
    { token, signal },
  )
}

export function sendConversationAction(
  token: string,
  conversation: JDConversation,
  action: JDAction,
  message?: string,
) {
  if (!conversation.checkpoint_id || !conversation.interrupt) {
    throw new Error('Reload the conversation before continuing.')
  }

  return apiRequest<JDConversation>(
    `/jd/conversations/${conversation.conversation_id}/actions`,
    {
      method: 'POST',
      token,
      body: {
        checkpoint_id: conversation.checkpoint_id,
        interrupt_id: conversation.interrupt.id,
        action,
        ...(message !== undefined ? { message } : {}),
      },
    },
  )
}


export type JobDescriptionSummary = {
  id: string
  conversation_id: string
  title: string
  created_at: string
}

export type SavedJobDescription = JobDescriptionSummary & {
  content: string
}

export function listJobDescriptions(
  token: string,
  offset = 0,
  signal?: AbortSignal,
) {
  return apiRequest<{
    items: JobDescriptionSummary[]
    limit: number
    offset: number
  }>(`/jd/descriptions?limit=10&offset=${offset}`, {
    token,
    signal,
  })
}

export function getJobDescription(
  token: string,
  descriptionId: string,
  signal?: AbortSignal,
) {
  return apiRequest<SavedJobDescription>(
    `/jd/descriptions/${descriptionId}`,
    { token, signal },
  )
}