import { Fragment, useEffect, useState } from 'react'
import { api, ingestApi, REVIEW_QUEUE_EVENT, type QueueMetrics, type ReviewRow } from '../api'
import { downloadText, flatText, plainText, type Transcript } from '../lib/transcript'

const ORIGIN_RU: Record<string, string> = {
  zoom: 'Zoom', downloads: 'Загрузки', 'telegram-desktop': 'Telegram',
  web: 'веб', telemost: 'Телемост', unknown: 'неизвестно',
  // Старый поток происхождение не сохранял. Это НЕ то же самое, что «неизвестно»:
  // «неизвестно» гейт считает признаком чужой записи, а эти звонки пришли через
  // вотчер из папок владельца — тревога была бы ложной и встала бы сразу на всех.
  legacy: 'старый поток',
}

const FLAG_RU: Record<string, string> = {
  project_unclear: 'проект неясен',
  commitment_with_stakes: 'обязательство с датой или суммой',
  attribution_unclear: 'непонятно, чей трек',
  foreign_recording: 'похоже на присланный материал',
}

// Семь статусов строки очереди. "split" — не решение о качестве разбора, а
// признак того, что строка расщеплена на потомков (по одному на проект);
// поэтому у него свой бейдж, отдельный и от "принято", и от "отклонено".
// "legacy" — строка, накопленная прежним (телеграм-) потоком: её материал уже
// в графе, атомов у строки нет, принимать нечего. Подпись нужна, чтобы такая
// строка в режиме «все» не выглядела поломкой.
// "done" — та же история, но разобранная старым телеграм-потоком уже после
// его перехода на этот статус; бейдж нейтральный, как у legacy — по смыслу
// это тот же «принимать нечего», просто другая метка на входе.
const STATUS_RU: Record<string, string> = {
  awaiting: 'ожидает', accepted: 'принято', rejected: 'отклонено',
  split: 'разнесено', reparsing: 'переразбор…', legacy: 'старый поток',
  // не звонок владельца: чужой источник, приехавший под его учёткой
  // (например, надиктовки из другого бота). Отдельно от «отклонено»:
  // это не его отказ по качеству разбора, и в метрику переигранных не идёт.
  foreign: 'чужой источник',
  done: 'разобрано',
}
const STATUS_BADGE: Record<string, string> = {
  awaiting: 'badge', accepted: 'badge badge--done', rejected: 'badge badge--fail',
  split: 'badge badge--warn', reparsing: 'badge badge--warn', legacy: 'badge',
  foreign: 'badge',
  done: 'badge',
}

// Вкладка «Очередь» — только то, что ждёт решения. reparsing здесь не для красоты:
// откат статуса живёт в обработчике исключения на сервере и не переживает убийства
// процесса — строка может застрять в reparsing навсегда. Без неё в этом списке
// застрявшая строка не видна нигде и исчезает молча, а это ровно то, чего очередь
// не допускает. Модульные константы, а не локальные: иначе они попадают в
// зависимости useEffect(reload).
const QUEUE_STATUSES = ['awaiting', 'reparsing']
// Вкладка «Архив» — всё, что решено на этой странице. Разнесённый звонок тоже
// здесь: в очереди остаются его ветки, а сам он до 10.09 висел в очереди вечно,
// даже когда все ветки давно решены, — принятое при этом уходило из вида, и
// страница вела себя непредсказуемо. Строки прежних потоков (legacy, done,
// foreign) сюда сознательно не входят: их решали не здесь.
const ARCHIVE_STATUSES = ['accepted', 'rejected', 'split']

// Дата строки: настоящая дата записи, если она известна, иначе — когда звонок
// попал в очередь. Пустой ячейки быть не должно: recorded_at сервер достаёт из
// имени файла, а имя несёт дату только у рекордера Телемоста — у zoom-выгрузок
// и скачанных записей (`video1855516632.opus`) её нет вовсе. Без запасного
// значения свежий звонок в очереди из полусотни строк не найти вообще.
function rowDate(r: ReviewRow): { text: string; exact: boolean } {
  const recorded = (r.recorded_at ?? '').slice(0, 10)
  if (recorded) return { text: recorded, exact: true }
  return { text: (r.ts ?? '').slice(0, 10), exact: false }
}

// Ключ сортировки — та же дата, но приведённая к одной точности: recorded_at
// это голый день, ts — день со временем, и без выравнивания строка с датой
// записи всегда проигрывала бы соседке того же дня со временем.
function dateKey(r: ReviewRow): string {
  const v = r.recorded_at ?? r.ts ?? ''
  return v.length <= 10 ? `${v} 00:00:00` : v
}

// Новые сверху. Внутри одного дня recorded_at у всех строк одинаковый (голый
// день), и без второго ключа порядок оставался серверным — по вставке, то есть
// от утра к вечеру (18.09). Поэтому при равной дате решает время попадания в
// очередь, тоже по убыванию.
function compareRows(a: ReviewRow, b: ReviewRow): number {
  return dateKey(b).localeCompare(dateKey(a)) || (b.ts ?? '').localeCompare(a.ts ?? '')
}

// Строка-потомок расщеплённого звонка имеет id вида "j1#demo-bot".
function isChildJob(jobId: string): boolean {
  return jobId.includes('#')
}

// Порог доли неопознанных сегментов, при котором атрибуция считается сомнительной.
// Значение должно совпадать с серверной константой UNKNOWN_SHARE_MAX
// (ops-ingest/ops_ingest/gate.py) — это копия для отображения, не отдельный
// источник правды; если серверный порог изменится, поправить и здесь.
const UNKNOWN_SHARE_GATE = 0.3

// Порог уверенности классификатора — копия серверной CONFIDENCE_MIN (gate.py).
// Ниже него выбор проекта считается ненадёжным, и рядом с выпадашкой показываются
// альтернативные кандидаты: иначе смена проекта делается вслепую, по списку всех
// проектов сразу, хотя модель уже назвала, между чем колебалась.
const CONFIDENCE_GATE = 0.7

// Действие не выполнено, потому что строка уже не ждёт решения — обновляем
// строку молча, это не сбой.
const ALREADY_RU: Record<string, string> = {
  accepted: 'уже принято', rejected: 'уже отклонено',
  split: 'уже разнесено на проекты', reparsing: 'уже переразбирается',
  legacy: 'из старого потока — материал уже в графе, принимать нечего',
}

const ERROR_RU: Record<string, string> = {
  reparse_on_child: 'переразбор — только для звонка целиком, не для отдельной ветки разнесения',
  split_on_child: 'разнесение — только для звонка целиком; ветку разнесения переиграй повторным разнесением родителя',
  split_has_accepted_children: 'нельзя переразнести — часть веток уже принята',
  project_not_set: 'сначала выбери проект',
  split_produced_nothing: 'модель не смогла распределить атомы ни по одному проекту',
}

type Attribution = { icon: string; label: string; cls: string; title?: string }

function attribution(r: ReviewRow): Attribution {
  if (!r.diarized) return { icon: '⚪', label: 'без диаризации', cls: 'badge' }
  if (r.owner_identified === true && (r.unknown_share ?? 0) <= UNKNOWN_SHARE_GATE)
    return { icon: '🟢', label: 'опознан', cls: 'badge badge--done',
             title: `спикеров: ${r.speaker_count ?? '?'}` }
  if (r.owner_identified === false)
    return { icon: '🟡', label: 'не опознан', cls: 'badge badge--warn',
             title: 'by_alex под подозрением' }
  // r.owner_identified === null: модель не определилась, а не подтвердила — это
  // не то же самое, что уверенный "опознан", хоть unknown_share и в норме.
  return { icon: '🟡', label: 'под вопросом', cls: 'badge badge--warn',
           title: 'модель не определила, опознан ли владелец' }
}

type Contradiction = { index: number; existing?: string; note: string }
type RowMsg = { kind: 'error' | 'notice'; text: string }

// Человекочитаемый итог успешного действия — чтобы "записано 12 атомов" и
// "записано 0" не выглядели одинаково молча.
function successText(path: string, res: Record<string, unknown>): string | null {
  if (path === 'accept') return `Принято — атомов записано: ${Number(res.written) || 0}`
  if (path === 'reject') return 'Отклонено'
  if (path === 'reparse') return `Переразобрано — атомов: ${Number(res.atoms) || 0}`
  if (path === 'split') {
    const into = Array.isArray(res.projects) ? (res.projects as string[]).join(', ') : ''
    const dropped = typeof res.dropped === 'number' && res.dropped > 0
      ? `; отброшено атомов: ${res.dropped}` : ''
    // Всё ушло в один проект — сервер не плодит ветку, а меняет проект звонка:
    // строка остаётся в очереди, дальше её просто принимают.
    if (res.retargeted) return `всё в один проект — проект звонка сменён на ${into}${dropped}`
    return `разнесено по проектам: ${into} (ветки — в очереди, сам звонок — в архиве)${dropped}`
  }
  return null
}

export default function Review() {
  const [rows, setRows] = useState<ReviewRow[]>([])
  const [projects, setProjects] = useState<{ key: string; name: string }[]>([])
  const [projectsError, setProjectsError] = useState<string | null>(null)
  // Две вкладки: «Очередь» (ждёт решения) и «Архив» (решено здесь) — см.
  // QUEUE_STATUSES / ARCHIVE_STATUSES.
  const [filter, setFilter] = useState<'queue' | 'archive'>('queue')
  const [open, setOpen] = useState<string | null>(null)
  // Клиентская пагинация: данные тянутся целиком, режется только отрисовка —
  // при 421 строке и 77 проектах в каждой выпадашке рендер всей таблицы разом
  // и был источником замирания интерфейса.
  const [page, setPage] = useState(1)
  const [prompts, setPrompts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<{ jobId: string; items: Contradiction[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [rowMsg, setRowMsg] = useState<Record<string, RowMsg>>({})
  // Итог accept/reject — строка после них уходит из вида по умолчанию вместе с
  // сообщением внутри неё, поэтому для этих двух действий результат живёт здесь,
  // на уровне страницы, и переживает reload().
  const [pageNotice, setPageNotice] = useState<string | null>(null)
  // Метрики очереди — рабочий пульт, одна строка без графиков. Пересчитываем
  // при каждом reload() строк, отдельного триггера им не нужно.
  const [metrics, setMetrics] = useState<QueueMetrics | null>(null)
  // Мультивыбор строк для массового отклонения. Только отклонение: массовой
  // приёмки нет по той же причине, что и «Принять» в свёрнутой строке, — писать
  // в граф, не посмотрев, что уедет, нельзя.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)

  const setMsg = (jobId: string, kind: RowMsg['kind'], text: string) =>
    setRowMsg(m => ({ ...m, [jobId]: { kind, text } }))
  const clearMsg = (jobId: string) =>
    setRowMsg(m => (jobId in m ? Object.fromEntries(Object.entries(m).filter(([k]) => k !== jobId)) : m))

  // Только переразбор гарантированно оставляет строку на месте (возвращает её в
  // awaiting) — его итог можно показать прямо в строке. Приёмка, отклонение и
  // разнесение уводят строку из вкладки (в архив) — итог должен пережить именно
  // этот reload(), поэтому уходит на уровень страницы с именем звонка.
  const rowSurvivesAction = (path: string) => path === 'reparse'

  const announceSuccess = (jobId: string, path: string, text: string) => {
    if (rowSurvivesAction(path)) {
      setMsg(jobId, 'notice', text.charAt(0).toUpperCase() + text.slice(1))
      return
    }
    const label = rows.find(r => r.job_id === jobId)?.summary || jobId
    setPageNotice(`${label} — ${text}`)
  }

  const reload = () => {
    // API фильтрует только по одному статусу за раз — запрашиваем каждый статус
    // вкладки отдельно и складываем.
    const statuses = filter === 'queue' ? QUEUE_STATUSES : ARCHIVE_STATUSES
    Promise.all(statuses.map(
      s => ingestApi<{ reviews: ReviewRow[] }>(`/reviews?status=${s}`)))
      .then(parts => setRows(parts.flatMap(p => p.reviews)))
      .catch(e => setError(String(e.message)))
  }
  const reloadAndNotify = () => {
    reload()
    window.dispatchEvent(new Event(REVIEW_QUEUE_EVENT))
  }

  // Смена фильтра — новая выборка, поэтому и счёт страниц начинается заново;
  // без сброса «показать решённые» могло бы открыться на несуществующей
  // странице для новой, обычно куда более длинной выборки.
  useEffect(() => { setPage(1); setSelected(new Set()) }, [filter])
  useEffect(reload, [filter])
  // Выбор держится только за строки, которые всё ещё ждут решения: отклонённая
  // поштучно или ушедшая с сервера строка не должна молча сидеть в счётчике.
  useEffect(() => {
    setSelected(s => {
      if (s.size === 0) return s
      const live = new Set(rows.filter(r => r.status === 'awaiting').map(r => r.job_id))
      const next = new Set([...s].filter(id => live.has(id)))
      return next.size === s.size ? s : next
    })
  }, [rows])
  useEffect(() => {
    // Сбой загрузки проектов нельзя гасить в пустой список: выпадашка тогда
    // выглядит как «проектов нет», и причина (протухший токен Мионики, лежащий
    // сервис) не видна вообще. Так уже было: ровно этот симптом дважды.
    ingestApi<{ projects: { key: string; name: string }[] }>('/projects')
      .then(b => { setProjects(b.projects); setProjectsError(null) })
      .catch(e => setProjectsError(String(e?.message ?? e)))
  }, [])
  useEffect(() => {
    ingestApi<QueueMetrics>('/metrics').then(setMetrics).catch(() => setMetrics(null))
  }, [rows])

  // Сортировка — по дате, новые сверху. Флаги гейта наверх больше НЕ поднимают:
  // они остаются подсветкой строки. Так уже было: : звонок, разобранный
  // в тот же день, оказался 32-м в списке — выше него стояла 31 открытая строка
  // с двумя-тремя флагами, накопленная за месяцы. Очередь разбирают от свежего,
  // а флаг — это «посмотри внимательнее», а не «это важнее сегодняшнего».
  // Пагинация режет именно этот, уже отсортированный порядок — иначе смысл
  // сортировки терялся бы на второй же странице.
  // Архив сортируется так же, по дате звонка. Первая версия архива (10.09) шла по
  // дате решения — но колонка «Дата» показывает дату звонка, и порядок читался
  // как поломанная сортировка: звонок от 03.09, принятый вчера, стоял среди 09.09.
  const sorted = [...rows].sort(compareRows)
  const PAGE_SIZE = 100
  // В очереди есть колонка галок выбора, в архиве её нет.
  const colCount = filter === 'queue' ? 10 : 9
  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE))
  const safePage = Math.min(page, pageCount)
  const pageStart = (safePage - 1) * PAGE_SIZE
  const pageRows = sorted.slice(pageStart, pageStart + PAGE_SIZE)

  // Раскрытая строка живёт по job_id — при переходе на другую страницу той
  // строки может не быть на экране вовсе, оставлять её «открытой» незачем.
  const goToPage = (p: number) => {
    setPage(Math.min(Math.max(p, 1), pageCount))
    setOpen(null)
  }

  const act = async (jobId: string, path: string, body?: object) => {
    setBusy(jobId)
    clearMsg(jobId)
    try {
      const res = await ingestApi<Record<string, unknown>>(
        `/reviews/${encodeURIComponent(jobId)}/${path}`,
        { method: 'POST', body: JSON.stringify(body ?? {}) })

      if (res.needs_confirm) {
        setConfirm({
          jobId,
          items: Array.isArray(res.contradictions) ? (res.contradictions as Contradiction[]) : [],
        })
        return
      }
      // Действие не выполнено, потому что строка уже решена — обновляем строку,
      // не показываем это как сбой.
      if (typeof res.already === 'string') {
        announceSuccess(jobId, path, `строка уже ${ALREADY_RU[res.already] ?? res.already}`)
        setConfirm(null)
        reloadAndNotify()
        return
      }
      if (typeof res.error === 'string') {
        setMsg(jobId, 'error', ERROR_RU[res.error] ?? `действие не выполнено: ${res.error}`)
        return
      }
      const txt = successText(path, res)
      if (txt) announceSuccess(jobId, path, txt)
      setConfirm(null)
      setPrompts(p => (jobId in p
        ? Object.fromEntries(Object.entries(p).filter(([k]) => k !== jobId)) : p))
      reloadAndNotify()
    } catch (e) {
      setMsg(jobId, 'error', e instanceof Error
        ? `Не удалось выполнить действие: ${e.message}`
        : 'Не удалось выполнить действие — сеть или сервер недоступны')
    } finally {
      setBusy(null)
    }
  }

  const toggleSelected = (jobId: string) =>
    setSelected(s => {
      const next = new Set(s)
      if (next.has(jobId)) next.delete(jobId)
      else next.add(jobId)
      return next
    })

  // Галка в шапке — про текущую страницу: выбирать скрытые за пагинацией
  // строки незаметно для глаза нельзя.
  const pageSelectable = pageRows.filter(r => r.status === 'awaiting').map(r => r.job_id)
  const pageAllSelected = pageSelectable.length > 0 && pageSelectable.every(id => selected.has(id))
  const togglePage = () =>
    setSelected(s => {
      const next = new Set(s)
      if (pageAllSelected) pageSelectable.forEach(id => next.delete(id))
      else pageSelectable.forEach(id => next.add(id))
      return next
    })

  const rejectSelected = async () => {
    const ids = sorted.filter(r => selected.has(r.job_id)).map(r => r.job_id)
    if (ids.length === 0) return
    if (!window.confirm(`Отклонить выбранные звонки: ${ids.length}?`)) return
    setBulkBusy(true)
    setPageNotice(null)
    // Последовательно, по одному запросу на строку: эндпойнт поштучный, а
    // параллельная пачка писала бы в одну SQLite разом без всякой пользы.
    let done = 0
    let already = 0
    const failed: string[] = []
    for (const id of ids) {
      try {
        const res = await ingestApi<Record<string, unknown>>(
          `/reviews/${encodeURIComponent(id)}/reject`, { method: 'POST', body: '{}' })
        if (typeof res.already === 'string') already++
        else if (typeof res.error === 'string') failed.push(id)
        else done++
      } catch {
        failed.push(id)
      }
    }
    setBulkBusy(false)
    // Неудачные остаются выбранными — их можно добить повторным нажатием.
    setSelected(new Set(failed))
    const parts = [`Отклонено: ${done}`]
    if (already) parts.push(`уже решены раньше: ${already}`)
    if (failed.length) parts.push(`не удалось: ${failed.length} (остались выбранными)`)
    setPageNotice(parts.join(' · '))
    reloadAndNotify()
  }

  const retarget = async (jobId: string, key: string) => {
    // Смена проекта не зовёт LLM: раскладка по узлам живёт в accept — единственный
    // вызов здесь и есть смена ярлыка. Сервер при этом обнуляет уверенность
    // классификатора, зеркалим сразу, иначе рядом с выпадашкой останется врать
    // старое число.
    const prevProject = rows.find(r => r.job_id === jobId)?.project ?? null
    setRows(rs => rs.map(r => (r.job_id === jobId ? { ...r, project: key, confidence: null } : r)))
    try {
      await ingestApi(`/reviews/${encodeURIComponent(jobId)}/retarget`,
                      { method: 'POST', body: JSON.stringify({ project_key: key }) })
    } catch (e) {
      // Откат оптимистичного изменения — иначе интерфейс показывает проект,
      // которого на сервере не было.
      setRows(rs => rs.map(r => (r.job_id === jobId ? { ...r, project: prevProject } : r)))
      setMsg(jobId, 'error', e instanceof Error
        ? `Не удалось сменить проект: ${e.message}`
        : 'Не удалось сменить проект — сеть или сервер недоступны')
      // Выпадашка живёт в свёрнутой строке — без раскрытия сообщение об ошибке
      // никто не увидит.
      setOpen(jobId)
    }
  }

  const download = async (jobId: string, withSpeakers: boolean) => {
    // Расшифровка одна на весь звонок и лежит под базовым id — у потомков
    // расщеплённого звонка (id вида "j1#demo-bot") решётку нужно срезать до
    // обращения к соседнему сервису, иначе получишь "не найдено".
    const baseId = jobId.split('#')[0]
    clearMsg(jobId)
    try {
      const tr = await api<Transcript>(`/jobs/${encodeURIComponent(baseId)}/transcript`)
      downloadText(`${jobId}${withSpeakers ? '.speakers' : ''}.txt`,
                   withSpeakers ? plainText(tr) : flatText(tr))
    } catch (e) {
      // Без этого нажатие на мёртвую кнопку не делало ровно ничего: промис падал
      // в unhandled rejection, владелец видел молчание. Отказ показываем в строке —
      // так же, как отказы остальных действий.
      setMsg(jobId, 'error', e instanceof Error
        ? `Не удалось скачать расшифровку: ${e.message}`
        : 'Не удалось скачать расшифровку — сеть или сервер недоступны')
    }
  }

  return (
    // page-wide снимает общий лимит main (1000px): у разбора девять колонок,
    // и на широком экране они помещаются целиком — горизонтальная прокрутка
    // внутри таблицы нужна только на узких. Остальные страницы кап сохраняют:
    // там читают текст, и длинная строка вредит.
    <div className="page-wide">
      <div className="toolbar">
        <h2>Разбор</h2>
        <div className="row" style={{ gap: 6 }}>
          <button className={filter === 'queue' ? undefined : 'secondary'}
                  onClick={() => setFilter('queue')}>Очередь</button>
          <button className={filter === 'archive' ? undefined : 'secondary'}
                  onClick={() => setFilter('archive')}>Архив</button>
        </div>
      </div>
      {metrics && <p className="muted">
        ждут: {metrics.awaiting} · переигранных: {(metrics.reworked_share * 100).toFixed(0)}%
        {metrics.median_minutes_to_decision != null &&
          <> · медиана до решения: {Math.round(metrics.median_minutes_to_decision)} мин</>}
      </p>}
      {error && <div className="error">{error}</div>}
      {projectsError && (
        <div className="error">
          Список проектов не загрузился — выбрать проект нельзя, приёмка не сработает.
          Причина: {projectsError}
        </div>
      )}
      {pageNotice && <div className="notice">{pageNotice}</div>}
      {filter === 'queue' && selected.size > 0 && (
        <div className="row" style={{ gap: 8, margin: '0 0 12px' }}>
          <span>выбрано: {selected.size}</span>
          <button className="secondary" disabled={bulkBusy} onClick={rejectSelected}>
            {bulkBusy ? 'отклоняю…' : 'Отклонить выбранные'}</button>
          <button className="secondary" disabled={bulkBusy}
                  onClick={() => setSelected(new Set())}>снять выбор</button>
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {filter === 'queue' && (
                <th>
                  <input type="checkbox" checked={pageAllSelected}
                         disabled={pageSelectable.length === 0 || bulkBusy}
                         title="выбрать все ожидающие на этой странице"
                         onChange={togglePage} />
                </th>
              )}
              <th>Дата</th><th>Источник</th><th>Атрибуция</th><th>Проект</th>
              <th>Саммари</th><th>Атомов</th><th>Флаги</th><th>Статус</th><th></th>
            </tr>
          </thead>
          <tbody>
            {pageRows.map(r => {
              const at = attribution(r)
              const awaiting = r.status === 'awaiting'
              const canSplit = awaiting || r.status === 'split'
              const child = isChildJob(r.job_id)
              const msg = rowMsg[r.job_id]
              const promptVal = prompts[r.job_id] ?? ''
              // Подсветка — только для строк, которые реально требуют решения.
              // У флагов гейта — 59% строк; заливать всю таблицу ими бессмысленно,
              // подсветка обязана указывать именно туда, где решение ещё не принято.
              const needsDecision = r.gate_flags.length > 0 && awaiting
              return (
                <Fragment key={r.job_id}>
                  <tr className={needsDecision ? 'row--flagged' : undefined}>
                    {filter === 'queue' && (
                      <td>
                        {awaiting && (
                          <input type="checkbox" checked={selected.has(r.job_id)}
                                 disabled={bulkBusy}
                                 onChange={() => toggleSelected(r.job_id)} />
                        )}
                      </td>
                    )}
                    <td>{(() => {
                      const d = rowDate(r)
                      if (!d.text) return null
                      return d.exact ? d.text : (
                        <span className="muted" title="дата записи не распознана — показано, когда звонок попал в очередь">
                          {d.text}
                        </span>)
                    })()}</td>
                    <td><span className="badge">{ORIGIN_RU[r.origin ?? 'unknown']}</span></td>
                    <td><span className={at.cls} title={at.title}>{at.icon} {at.label}</span></td>
                    <td>
                      {/* Выпадашка — только там, где смена проекта вообще возможна:
                          редактировать его можно исключительно у строк awaiting.
                          На боевом объёме (421 строка × 77 проектов) рендер полного
                          списка <option> в 420 отключённых выпадашках и был главным
                          источником замирания интерфейса. */}
                      {awaiting ? (
                        <select value={r.project ?? ''}
                                onChange={e => retarget(r.job_id, e.target.value)}>
                          <option value="">— не выбран —</option>
                          {projects.map(p => <option key={p.key} value={p.key}>{p.key}</option>)}
                        </select>
                      ) : (
                        <span>{r.project || '— не выбран —'}</span>
                      )}
                      {r.confidence != null && <span className="muted"> {r.confidence.toFixed(2)}</span>}
                      {(r.confidence == null || r.confidence < CONFIDENCE_GATE) &&
                        (r.alternatives?.length ?? 0) > 0 && (
                        <div className="muted" style={{ fontSize: '0.85em' }}>
                          ещё кандидаты: {r.alternatives.map(a =>
                            a.confidence != null ? `${a.key} ${a.confidence.toFixed(2)}` : a.key
                          ).join(', ')}
                        </div>
                      )}
                    </td>
                    <td>
                      {/* Ветка несёт саммари всего звонка — без пометки её не
                          отличить от самостоятельного звонка и она читается как дубль. */}
                      {child && <div className="muted" style={{ fontSize: '0.85em' }}>
                        ветка разнесения звонка</div>}
                      <span className="summary-clamp">{r.summary}</span>
                    </td>
                    <td>{r.count}</td>
                    <td>{r.gate_flags.map(f => (
                      <span key={f} className="badge badge--warn">{FLAG_RU[f] ?? f}</span>))}</td>
                    <td><span className={STATUS_BADGE[r.status] ?? 'badge'}>{STATUS_RU[r.status] ?? r.status}</span></td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <button className="secondary"
                                onClick={() => setOpen(open === r.job_id ? null : r.job_id)}>
                          {open === r.job_id ? 'свернуть' : 'открыть'}
                        </button>
                        {/* Отклонение — прямо в строке: разбор очереди это в основном
                            отсев, и заставлять раскрывать строку ради самого частого
                            действия значит удваивать число кликов. «Принять» здесь
                            сознательно НЕТ: записывать в базу знаний, не посмотрев,
                            что уедет, — ровно то, от чего уходили. */}
                        {awaiting && (
                          <button className="secondary" disabled={busy === r.job_id}
                                  title="отклонить, не раскрывая строку"
                                  onClick={() => act(r.job_id, 'reject')}>
                            отклонить
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {open === r.job_id && (
                    <tr>
                      <td colSpan={colCount}>
                        {/* Саммари в свёрнутой строке обрезано CSS-клампом (3 строки) —
                            полный текст живёт здесь, при раскрытии. */}
                        {r.summary && <p>{r.summary}</p>}
                        {/* Чем модель была недовольна сама. Доля сегментов без метки
                            говорящего — та самая величина, по которой гейт ставит
                            «непонятно, чей трек»; без числа флаг нечем перепроверить. */}
                        <p className="muted">
                          без метки говорящего: {r.unknown_share != null
                            ? `${Math.round(r.unknown_share * 100)}%` : '—'}
                          {r.speaker_count != null && <> · спикеров: {r.speaker_count}</>}
                        </p>
                        {r.uncertainty && (
                          <p className="muted">Модель не уверена: {r.uncertainty}</p>
                        )}
                        <h3>Уедет в граф</h3>
                        <ul>
                          {r.atoms.map((a, i) => <li key={i}><b>{a.kind}</b> — {a.text}</li>)}
                          {r.tasks.map((t, i) => <li key={`t${i}`}>задача — {t.title}</li>)}
                        </ul>
                        <div className="row" style={{ gap: 8 }}>
                          <button className="secondary" onClick={() => download(r.job_id, true)}>
                            Скачать .txt со спикерами</button>
                          <button className="secondary" onClick={() => download(r.job_id, false)}>
                            Скачать .txt сплошняком</button>
                        </div>

                        {msg && <div className={msg.kind === 'error' ? 'error' : 'notice'}>{msg.text}</div>}

                        {awaiting && (
                          <div className="row" style={{ gap: 8, margin: '10px 0' }}>
                            <button disabled={busy === r.job_id || !r.project}
                                    title={!r.project ? 'сначала выбери проект' : undefined}
                                    onClick={() => act(r.job_id, 'accept')}>Принять</button>
                            <button className="secondary" disabled={busy === r.job_id}
                                    onClick={() => act(r.job_id, 'reject')}>Отклонить</button>
                            {!r.project && <span className="muted">сначала выбери проект</span>}
                          </div>
                        )}
                        {/* Ни разнесения, ни переразбора у ветки разнесения: сервер
                            отказывает обоим (split_on_child / reparse_on_child) —
                            двухуровневое разнесение обходит защиту от двойной записи
                            в граф. Кнопок здесь быть не должно вовсе. */}
                        {canSplit && !child && (
                          <>
                            <textarea value={promptVal}
                                      onChange={e => setPrompts(p => ({ ...p, [r.job_id]: e.target.value }))}
                                      placeholder="например: это два проекта, разнеси на prihoda-partnership и sales-pipeline" />
                            <div className="row" style={{ gap: 8, margin: '10px 0' }}>
                              <button className="secondary" disabled={!promptVal || busy === r.job_id}
                                      onClick={() => act(r.job_id, 'split', { prompt: promptVal })}>
                                Разнести по проектам</button>
                              {awaiting && (
                                <button className="secondary" disabled={!promptVal || busy === r.job_id}
                                        onClick={() => act(r.job_id, 'reparse', { prompt: promptVal })}>
                                  Переразобрать</button>
                              )}
                            </div>
                            <p className="muted">
                              Разнесение работает с готовыми атомами и стоит копейки.
                              Переразбор гонит через модель весь транскрипт — только если кривы сами атомы.
                            </p>
                          </>
                        )}
                        {canSplit && child && (
                          <p className="muted">
                            Это ветка разнесения. Переиграть раскладку можно только повторным
                            разнесением звонка целиком.
                          </p>
                        )}

                        {confirm?.jobId === r.job_id && (
                          <div className="error">
                            <b>Спорит с тем, что уже лежит в проекте:</b>
                            <ul>
                              {confirm.items.map((c, i) => (
                                <li key={i}>{r.atoms[c.index]?.text} — {c.note}
                                  {c.existing && <> (было: {c.existing})</>}</li>
                              ))}
                            </ul>
                            <button onClick={() => act(r.job_id, 'accept', { force: true })}>
                              Всё равно записать</button>
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
            {sorted.length === 0 && <tr><td colSpan={colCount} className="empty">
              {filter === 'queue' ? 'Очередь пуста.' : 'Архив пуст.'}</td></tr>}
          </tbody>
        </table>
      </div>
      {sorted.length > 0 && (
        <div className="row" style={{ justifyContent: 'space-between', margin: '12px 0' }}>
          <span className="muted">
            {pageStart + 1}–{Math.min(pageStart + PAGE_SIZE, sorted.length)} из {sorted.length}
          </span>
          <div className="row" style={{ gap: 8 }}>
            <button className="secondary" disabled={safePage <= 1}
                    onClick={() => goToPage(safePage - 1)}>назад</button>
            <span className="muted">{safePage} / {pageCount}</span>
            <button className="secondary" disabled={safePage >= pageCount}
                    onClick={() => goToPage(safePage + 1)}>вперёд</button>
          </div>
        </div>
      )}
    </div>
  )
}
