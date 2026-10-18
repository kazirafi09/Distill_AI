export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PUT'
  token?: string
  body?: unknown
  signal?: AbortSignal
  cache?: RequestCache
  responseType?: 'json' | 'blob'
}

let accessToken: string | null = null
let refreshPromise: Promise<string> | null = null
let sessionGeneration = 0

export function clearSession() {
  sessionGeneration += 1
  accessToken = null
  refreshPromise = null
}

async function refreshAccessToken() {
  if (!refreshPromise) {
    const generation = sessionGeneration
    const pending = apiRequest<LoginResponse>('/auth/refresh', { method: 'POST' })
      .then(result => {
        if (generation !== sessionGeneration) throw new Error('Session changed')
        accessToken = result.access_token
        return accessToken
      }).finally(() => {
        if (refreshPromise === pending) refreshPromise = null
      })
    refreshPromise = pending
  }
  return refreshPromise
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers = new Headers()
  const isFormData = options.body instanceof FormData

  if (options.body !== undefined && !isFormData) {
    headers.set('Content-Type', 'application/json')
  }

  const requestToken = options.token ? accessToken ?? options.token : undefined
  if (requestToken) {
    headers.set('Authorization', `Bearer ${requestToken}`)
  }

  const request = {
    method: options.method ?? 'GET',
    headers,
    body:
      options.body === undefined
        ? undefined
        : isFormData
          ? options.body as FormData
          : JSON.stringify(options.body),
    signal: options.signal,
    cache: options.cache,
    credentials: 'same-origin' as const,
  }
  let response = await fetch(`/api${path}`, request)
  if (response.status === 401 && options.token) {
    try {
      const token = accessToken && accessToken !== requestToken
        ? accessToken : await refreshAccessToken()
      headers.set('Authorization', `Bearer ${token}`)
      response = await fetch(`/api${path}`, request)
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        clearSession()
        window.dispatchEvent(new Event('distill-session-expired'))
      }
      throw error
    }
  }

  if (response.ok && options.responseType === 'blob') return await response.blob() as T
  const data: unknown = await response.json().catch(() => null)

  if (!response.ok) {
    let message = `Request failed (${response.status})`

    if (
      typeof data === 'object' &&
      data !== null &&
      'detail' in data
    ) {
      const detail = data.detail

      if (typeof detail === 'string') {
        message = detail
      } else if (Array.isArray(detail)) {
        message = 'Please check the information you entered.'
      } else if (
        typeof detail === 'object' &&
        detail !== null &&
        'message' in detail &&
        typeof detail.message === 'string'
      ) {
        message = detail.message
      }
    }

    throw new ApiError(response.status, message)
  }

  return data as T
}

export type UserProfile = {
  id: string
  email: string
  role: 'recruiter' | 'interviewer'
}

export type RecruiterSession = {
  token: string
  user: UserProfile
}

type LoginResponse = {
  access_token: string
  token_type: string
}

export function loginRecruiter(email: string, password: string) {
  return apiRequest<LoginResponse>('/auth/login', {
    method: 'POST',
    body: { email, password },
  })
}

export function signupRecruiter(email: string, password: string) {
  return apiRequest<LoginResponse>('/auth/signup', {
    method: 'POST', body: { email, password },
  })
}

export async function acceptSession(token: string): Promise<RecruiterSession> {
  clearSession()
  accessToken = token
  const user = await getCurrentUser(token)
  if (user.role !== 'recruiter') {
    await logoutRecruiter()
    throw new Error('Please sign in with a recruiter account.')
  }
  return { token: accessToken ?? token, user }
}

export async function restoreSession(): Promise<RecruiterSession | null> {
  try {
    const token = await refreshAccessToken()
    const user = await getCurrentUser(token)
    if (user.role !== 'recruiter') {
      await logoutRecruiter()
      return null
    }
    return { token, user }
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      clearSession()
      return null
    }
    throw error
  }
}

export async function logoutRecruiter() {
  // Wait for a pending renewal before revoking its server session.
  if (refreshPromise) await refreshPromise.catch(() => undefined)
  await apiRequest<void>('/auth/logout', { method: 'POST' })
  clearSession()
}

export function getCurrentUser(token: string) {
  return apiRequest<UserProfile>('/auth/me', { token })
}
