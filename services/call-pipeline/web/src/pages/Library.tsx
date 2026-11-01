import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'

type JobRow = {
  job_id: string; status: string; mode: string
  original_filename: string | null; error_message: string | null
  audio_duration_sec: number | null; recorded_at: string | null
  created_at: string
}

const STATUS_RU: Record<string, string> = {
  uploaded: 'в очереди',
  processing_whisper: 'расшифровка…',
  transcribed: 'готово',
  processing_classifier: 'анализ…',
  drafted: 'готово',
  done: 'готово',
  failed_whisper: 'ошибка расшифровки',
  failed_classifier: 'ошибка анализа',
}

// Статусы, при которых расшифровка уже доступна для чтения.
const READY = new Set(['transcribed', 'drafted', 'done'])

function badgeClass(s: string) {
  if (s.startsWith('failed')) return 'badge badge--fail'
  if (READY.has(s)) return 'badge badge--done'
  return 'badge badge--warn'
}

function fmtDur(sec: number | null) {
  if (!sec) return ''
  const m = Math.floor(sec / 60), s = Math.round(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function Library() {
  const [jobs, setJobs] = useState<JobRow[]>([])

  const load = useCallback(() => {
    api<{ jobs: JobRow[] }>('/jobs?limit=100').then(r => setJobs(r.jobs)).catch(() => {})
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [load])

  return (
    <div>
      <h2>Библиотека</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Когда</th><th>Файл</th><th>Длит.</th><th>Статус</th></tr></thead>
          <tbody>
            {jobs.map(j => (
              <tr key={j.job_id}>
                <td className="mono" style={{ whiteSpace: 'nowrap' }}>
                  {new Date(j.created_at).toLocaleString('ru')}</td>
                <td><Link to={`/jobs/${j.job_id}`}>
                  {j.original_filename ?? j.job_id.slice(0, 8)}</Link></td>
                <td className="mono">{fmtDur(j.audio_duration_sec)}</td>
                <td>
                  <span className={badgeClass(j.status)}>{STATUS_RU[j.status] ?? j.status}</span>
                  {j.error_message && <div className="error" style={{ marginTop: 4 }}>{j.error_message}</div>}
                </td>
              </tr>
            ))}
            {jobs.length === 0 && <tr><td colSpan={4} className="empty">
              Пока пусто — загрузи запись или поставь созвон на запись</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
