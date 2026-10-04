export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

type RequestOptions = {
  method?: 'GET' | 'POST'
  token?: string
  body?: unknown
  signal?: AbortSignal
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers = new Headers()

  if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }

  if (options.token) {
    headers.set('Authorization', `Bearer ${options.token}`)
  }

  const response = await fetch(`/api${path}`, {
    method: options.method ?? 'GET',
    headers,
    body:
      options.body !== undefined
        ? JSON.stringify(options.body)
        : undefined,
    signal: options.signal,
  })

  const data: unknown = await response.json().catch(() => null)

  if (!response.ok) {
    let message = `Request failed (${response.status})`

    if (
      typeof data === 'object' &&
      data !== null &&
      'detail' in data
    ) {
      if (typeof data.detail === 'string') {
        message = data.detail
      } else if (Array.isArray(data.detail)) {
        message = 'Please check the information you entered.'
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

export function getCurrentUser(token: string) {
  return apiRequest<UserProfile>('/auth/me', { token })
}