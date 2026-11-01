import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { downloadText, flatText, plainText, speakerLabel, ts, type Transcript } from '../lib/transcript'

type Job = {
  job_id: string; status: string; original_filename: string | null
  error_message: string | null; created_at: string
}

export default function JobView() {
  const { jobId } = useParams()
  const [job, setJob] = useState<Job | null>(null)
  const [tr, setTr] = useState<Transcript | null>(null)
  const [notReady, setNotReady] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    setJob(null); setTr(null); setNotReady(false)
    api<Job>(`/jobs/${jobId}`).then(setJob).catch(() => {})
    api<Transcript>(`/jobs/${jobId}/transcript`)
      .then(setTr)
      .catch(err => { if (err instanceof ApiError && err.status === 404) setNotReady(true) })
  }, [jobId])

  // цвет спикера — по порядку появления, циклится по 6
  const colorOf = useMemo(() => {
    const order = new Map<string, number>()
    tr?.segments?.forEach(s => {
      const sp = s.speaker
      if (sp && sp !== 'UNKNOWN' && !order.has(sp)) order.set(sp, order.size % 6)
    })
    return (sp?: string) => (sp && order.has(sp) ? order.get(sp)! : null)
  }, [tr])

  const copy = async () => {
    if (!tr) return
    await navigator.clipboard.writeText(plainText(tr))
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  const base = (job?.original_filename ?? jobId) as string
  const dlSpeakers = () => tr && downloadText(`${base}.speakers.txt`, plainText(tr))
  const dlFlat = () => tr && downloadText(`${base}.txt`, flatText(tr))

  if (!job) return <div className="muted">Загрузка…</div>

  return (
    <div>
      <h2 style={{ overflowWrap: 'anywhere' }}>{job.original_filename ?? job.job_id}</h2>
      <div className="row" style={{ margin: '14px 0 20px' }}>
        <span className="muted mono">{new Date(job.created_at).toLocaleString('ru')}</span>
        {tr && <>
          <button className="secondary" onClick={copy}>{copied ? 'Скопировано ✓' : 'Копировать текст'}</button>
          <button className="secondary" onClick={dlSpeakers}>Скачать .txt со спикерами</button>
          <button className="secondary" onClick={dlFlat}>Скачать .txt сплошняком</button>
          <a className="secondary" href={`/api/jobs/${job.job_id}/audio`} download
             style={{ textDecoration: 'none' }}>Скачать аудио</a>
        </>}
      </div>

      {job.error_message && <div className="error">{job.error_message}</div>}
      {notReady && !tr && (
        <div className="card muted">
          Расшифровка ещё не готова — статус «{job.status}». Обнови страницу позже.
        </div>
      )}

      {tr && (
        <div className="card transcript">
          {tr.segments?.length
            ? tr.segments.map((s, i) => {
                const ci = colorOf(s.speaker)
                const spk = speakerLabel(s.speaker)
                return (
                  <div key={i} className={ci !== null ? `turn spk-color-${ci}` : 'turn flat'}>
                    <div className="meta">
                      <span className="ts">{ts(s.start)}</span>
                      {spk && <span className="spk">{spk}</span>}
                    </div>
                    <div className="said">{s.text.trim()}</div>
                  </div>
                )
              })
            : <div className="turn flat"><div className="said">{tr.text}</div></div>}
        </div>
      )}
    </div>
  )
}
