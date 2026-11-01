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

export async function ingestApi<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(`/ingest-api${path}`, {
    credentials: 'same-origin',
    headers: opts.body ? { 'Content-Type': 'application/json', ...opts.headers } : opts.headers,
    ...opts,
  })
  if (res.status === 401) { location.href = '/login'; throw new ApiError(401, 'unauthorized') }
  if (!res.ok) {
    // В HTTP/2 statusText пустой, а необработанный 500 приходит телом-текстом, не JSON —
    // без запасной строки после «Не удалось выполнить действие:» оставалась пустота.
    let detail = res.statusText || `ошибка сервера ${res.status}`
    try { detail = (await res.json()).detail || detail } catch { /* not json */ }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

// Событие "очередь разбора изменилась" — страница «Разбор» шлёт его после каждого
// успешного действия (accept/reject/split/reparse), а счётчик в навигации
// пересчитывается по нему, а не только один раз при монтировании.
export const REVIEW_QUEUE_EVENT = 'review-queue-changed'

// Метрики очереди — считаются на сервере из самих строк ревью (GET /metrics),
// отдельного хранилища под них нет.
export type QueueMetrics = {
  median_minutes_to_decision: number | null
  reworked_share: number
  flag_counts: Record<string, number>
  awaiting: number
}

export type ReviewRow = {
  job_id: string; summary: string | null; count: number | null
  project: string | null; confidence: number | null
  origin: string | null; status: string
  recorded_at: string | null; gate_flags: string[]
  // Когда строка попала в очередь. Нужна как запасная дата: recorded_at сервер
  // достаёт из имени файла, а zoom-выгрузки и скачанные записи даты в имени не
  // несут — у них он пуст, и колонка «Дата» без ts оказывалась пустой.
  ts: string | null
  // Когда владелец принял решение по строке — по нему сортируется архив.
  decided_at: string | null
  diarized: boolean | null; speaker_count: number | null
  unknown_share: number | null; owner_identified: boolean | null
  atoms: { kind: string; text: string }[]
  tasks: { title: string }[]
  // Чем ещё колебался классификатор и что он сам назвал непонятным. Пустой список
  // и null — норма: у строк, записанных до появления колонок, их просто нет.
  alternatives: { key: string; confidence: number | null; rationale?: string }[]
  uncertainty: string | null
}
