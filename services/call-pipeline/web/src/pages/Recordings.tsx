import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../api'

type Rec = {
  recording_id: string; status: string; url: string
  num_speakers: number | null; duration_sec: number | null
  stop_reason: string | null; job_id: string | null
  error_message: string | null; created_at: string
}

const ACTIVE = ['requested', 'recording']

const STATUS_RU: Record<string, string> = {
  requested: 'в очереди', recording: 'идёт запись',
  recorded: 'записано', submitted: 'готово', failed: 'ошибка',
}

function StatusBadge({ s }: { s: string }) {
  if (s === 'recording') return <span className="badge badge--live"><span className="dot" />идёт запись</span>
  if (s === 'requested' || s === 'recorded') return <span className="badge badge--warn">{STATUS_RU[s] ?? s}</span>
  if (s === 'failed') return <span className="badge badge--fail">ошибка</span>
  // submitted = запись снята и отдана в расшифровку; для строки записи это финал
  return <span className="badge badge--done">{STATUS_RU[s] ?? s}</span>
}

export default function Recordings() {
  const [recs, setRecs] = useState<Rec[]>([])
  const [url, setUrl] = useState('')
  const [speakers, setSpeakers] = useState(2)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api<{ recordings: Rec[] }>('/recordings').then(r => setRecs(r.recordings)).catch(() => {})
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [load])

  const start = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError('')
    const form = new FormData()
    form.set('url', url)
    form.set('num_speakers', String(speakers))
    try {
      await api('/recordings', { method: 'POST', body: form })
      setUrl('')
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Не удалось запустить запись')
    } finally {
      setBusy(false)
    }
  }

  const stop = async (id: string) => {
    await api(`/recordings/${id}/stop`, { method: 'POST' })
    load()
  }

  const retry = (r: Rec) => { setUrl(r.url); setSpeakers(r.num_speakers ?? 2) }

  return (
    <div>
      <h2>Запись Телемоста</h2>

      <form className="card" onSubmit={start}>
        <div className="row">
          <input style={{ flex: 1, minWidth: 260 }} placeholder="https://telemost.yandex.ru/…"
                 value={url} onChange={e => setUrl(e.target.value)} required />
          <label className="row" style={{ gap: 6 }}>
            <span className="field-label">Участников</span>
            <input type="number" min={1} max={20} value={speakers}
                   onChange={e => setSpeakers(+e.target.value)} style={{ width: 68 }} />
          </label>
          <button className="btn" disabled={busy}>{busy ? 'Запуск…' : 'Записать'}</button>
        </div>
        {error && <div className="error" style={{ marginTop: 10 }}>{error}</div>}
      </form>

      <div className="table-wrap" style={{ marginTop: 20 }}>
        <table>
          <thead><tr><th>Когда</th><th>Ссылка</th><th>Статус</th><th></th></tr></thead>
          <tbody>
            {recs.map(r => (
              <tr key={r.recording_id}>
                <td className="mono" style={{ whiteSpace: 'nowrap' }}>
                  {new Date(r.created_at).toLocaleString('ru')}</td>
                <td className="muted" style={{ maxWidth: 240, overflow: 'hidden',
                      textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.url}</td>
                <td>
                  <StatusBadge s={r.status} />
                  {r.status === 'failed' && r.error_message &&
                    <div className="error" style={{ marginTop: 4 }}>{r.error_message}</div>}
                </td>
                <td className="actions">
                  <div className="row" style={{ gap: 8 }}>
                    {ACTIVE.includes(r.status) &&
                      <button className="secondary danger" onClick={() => stop(r.recording_id)}>Стоп</button>}
                    {r.status === 'failed' &&
                      <button className="secondary" onClick={() => retry(r)}>Повторить</button>}
                    {r.job_id && <Link to={`/jobs/${r.job_id}`}>Расшифровка</Link>}
                  </div>
                </td>
              </tr>
            ))}
            {recs.length === 0 && <tr><td colSpan={4} className="empty">
              Записей пока нет — вставь ссылку на Телемост и нажми «Записать»</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
