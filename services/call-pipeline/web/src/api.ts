export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    credentials: 'same-origin',
    headers: opts.body instanceof FormData
      ? opts.headers
      : { 'Content-Type': 'application/json', ...opts.headers },
    ...opts,
  })
  if (res.status === 401) {
    if (location.pathname !== '/login') location.href = '/login'
    throw new ApiError(401, 'unauthorized')
  }
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* not json */ }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

export type Me = { id: string; name: string; email: string | null; is_admin: boolean }
