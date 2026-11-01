import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth'

const MAX_BYTES = 1024 * 1024 * 1024  // лимит бэкенда: 1 ГБ

export default function Upload() {
  const { user } = useAuth()
  const [mode, setMode] = useState<'call' | 'short'>('call')
  const [speakers, setSpeakers] = useState(2)
  const [drag, setDrag] = useState(false)
  const [progress, setProgress] = useState<number | null>(null)
  const [doneJobId, setDoneJobId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)

  const upload = (file: File) => {
    if (file.size > MAX_BYTES) { setError('Файл больше 1 ГБ'); return }
    setError(''); setDoneJobId(null); setProgress(0)

    const form = new FormData()
    form.set('mode', mode)
    form.set('user_id', user!.id)
    // origin обязателен: без него сервер запишет NULL, сервис разбора схлопнет его
    // в "unknown", а гейт считает "unknown" признаком чужой записи — флаг
    // «похоже на присланный материал» вставал бы на каждой ручной загрузке.
    form.set('origin', 'web')
    if (mode === 'call') form.set('num_speakers', String(speakers))
    form.set('file', file)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/jobs')
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) setProgress(Math.round(e.loaded / e.total * 100))
    }
    xhr.onload = () => {
      setProgress(null)
      if (xhr.status === 202 || xhr.status === 200) {
        setDoneJobId(JSON.parse(xhr.responseText).job_id)
      } else {
        let detail = `Ошибка ${xhr.status}`
        try { detail = JSON.parse(xhr.responseText).detail ?? detail } catch { /* keep */ }
        setError(detail)
      }
    }
    xhr.onerror = () => { setProgress(null); setError('Сеть оборвалась — попробуй ещё раз') }
    xhr.send(form)
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDrag(false)
    const f = e.dataTransfer.files[0]
    if (f) upload(f)
  }

  const busy = progress !== null

  return (
    <div>
      <h2>Загрузка записи</h2>

      <div className="card" style={{ marginBottom: 18 }}>
        <div className="row">
          <label className="row" style={{ gap: 7 }}>
            <input type="radio" checked={mode === 'call'} onChange={() => setMode('call')} />
            Звонок — со спикерами
          </label>
          {mode === 'call' &&
            <label className="row" style={{ gap: 6 }}>
              <span className="field-label">Спикеров</span>
              <input type="number" min={1} max={20} value={speakers}
                     onChange={e => setSpeakers(+e.target.value)} style={{ width: 68 }} />
            </label>}
          <label className="row" style={{ gap: 7 }}>
            <input type="radio" checked={mode === 'short'} onChange={() => setMode('short')} />
            Короткая — без спикеров
          </label>
        </div>
      </div>

      <div className={drag ? 'dropzone drag' : 'dropzone'}
           onDragOver={e => { e.preventDefault(); setDrag(true) }}
           onDragLeave={() => setDrag(false)}
           onDrop={onDrop}
           onClick={() => !busy && fileInput.current?.click()}>
        {busy
          ? <div>
              <div>Загрузка… {progress}%</div>
              <div className="progress"><i style={{ width: `${progress}%` }} /></div>
            </div>
          : <div>
              Перетащи аудио или видео сюда, либо кликни для выбора<br />
              <span className="muted">mp3, ogg, wav, m4a, mp4, mkv, webm… до 1 ГБ</span>
            </div>}
        <input ref={fileInput} type="file" hidden accept="audio/*,video/*"
               onChange={e => e.target.files?.[0] && upload(e.target.files[0])} />
      </div>

      {error && <div className="error" style={{ marginTop: 14 }}>{error}</div>}
      {doneJobId && (
        <div className="card" style={{ marginTop: 14 }}>
          Файл принят, расшифровка запущена. <Link to={`/jobs/${doneJobId}`}>Открыть расшифровку →</Link>
        </div>
      )}
    </div>
  )
}
