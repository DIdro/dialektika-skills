export type Seg = { start: number; end: number; text: string; speaker?: string }
export type Transcript = {
  text?: string; segments?: Seg[]
  language?: string; duration_sec?: number; diarized?: boolean
}

export function ts(sec: number) {
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export function speakerLabel(sp?: string) {
  if (!sp || sp === 'UNKNOWN') return ''
  const m = sp.match(/(\d+)$/)
  return m ? `Спикер ${Number(m[1]) + 1}` : sp
}

/** Расшифровка с разметкой по спикерам и таймкодами. */
export function plainText(t: Transcript): string {
  if (t.segments?.length)
    return t.segments.map(s => {
      const spk = speakerLabel(s.speaker)
      return `[${ts(s.start)}] ${spk ? spk + ': ' : ''}${s.text.trim()}`
    }).join('\n')
  return t.text ?? ''
}

/** Сплошной текст без меток — для пересылки человеку, которому спикеры не нужны. */
export function flatText(t: Transcript): string {
  if (t.text) return t.text
  return (t.segments ?? []).map(s => s.text.trim()).join(' ')
}

export function downloadText(name: string, body: string): void {
  const blob = new Blob([body], { type: 'text/plain;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = name
  a.click()
  URL.revokeObjectURL(a.href)
}
