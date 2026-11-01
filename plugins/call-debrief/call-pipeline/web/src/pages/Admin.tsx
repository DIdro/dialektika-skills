import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api'

type AdminUser = {
  id: string; name: string; email: string | null
  is_admin: boolean; is_active: boolean
  jobs_count: number; recordings_count: number; created_at: string
}

export default function Admin() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [secret, setSecret] = useState('')   // temp-пароль, показывается один раз
  const [error, setError] = useState('')

  const load = useCallback(() => {
    api<{ users: AdminUser[] }>('/web/admin/users').then(r => setUsers(r.users)).catch(() => {})
  }, [])

  useEffect(load, [load])

  const run = async (fn: () => Promise<void>) => {
    setError('')
    try { await fn(); load() }
    catch (err) { setError(err instanceof ApiError ? err.message : 'Ошибка') }
  }

  const create = (e: React.FormEvent) => {
    e.preventDefault()
    run(async () => {
      const r = await api<{ email: string; temp_password: string }>('/web/admin/users', {
        method: 'POST', body: JSON.stringify({ name, email }),
      })
      setSecret(`${r.email} — временный пароль: ${r.temp_password}`)
      setName(''); setEmail('')
    })
  }

  const resetPassword = (u: AdminUser) =>
    run(async () => {
      const r = await api<{ temp_password: string }>(`/web/admin/users/${u.id}/reset-password`,
        { method: 'POST' })
      setSecret(`${u.email ?? u.name} — временный пароль: ${r.temp_password}`)
    })

  const toggleActive = (u: AdminUser) =>
    run(() => api(`/web/admin/users/${u.id}/set-active`, {
      method: 'POST', body: JSON.stringify({ active: !u.is_active }),
    }).then(() => {}))

  const setUserEmail = (u: AdminUser) => {
    const value = prompt(`Email для ${u.name}:`, u.email ?? '')
    if (!value) return
    run(() => api(`/web/admin/users/${u.id}/set-email`, {
      method: 'POST', body: JSON.stringify({ email: value }),
    }).then(() => {}))
  }

  return (
    <div>
      <h2>Пользователи</h2>

      <form className="card" onSubmit={create}>
        <div className="row">
          <input placeholder="Имя" value={name} onChange={e => setName(e.target.value)} required />
          <input type="email" placeholder="Email" value={email}
                 onChange={e => setEmail(e.target.value)} required style={{ minWidth: 220 }} />
          <button className="btn">Создать</button>
        </div>
      </form>

      {secret && (
        <div className="card" style={{ marginTop: 12, background: '#FBF3DE', borderColor: '#EAD9A8' }}>
          <strong className="mono">{secret}</strong>
          <span className="muted"> — показывается один раз, передай пользователю</span>
          <button className="ghost" style={{ marginLeft: 10 }} onClick={() => setSecret('')}>Скрыть</button>
        </div>
      )}
      {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}

      <div className="table-wrap" style={{ marginTop: 18 }}>
        <table>
          <thead><tr>
            <th>Имя</th><th>Email</th><th>Джобы</th><th>Записи</th><th>Статус</th><th></th>
          </tr></thead>
          <tbody>
            {users.map(u => (
              <tr key={u.id} style={u.is_active ? undefined : { opacity: .5 }}>
                <td>{u.name}{u.is_admin && ' 👑'}</td>
                <td className="mono">{u.email ?? <span className="muted">нет</span>}</td>
                <td className="mono">{u.jobs_count}</td>
                <td className="mono">{u.recordings_count}</td>
                <td><span className={u.is_active ? 'badge badge--done' : 'badge badge--fail'}>
                  {u.is_active ? 'активен' : 'выключен'}</span></td>
                <td className="actions">
                  <div className="row" style={{ gap: 8 }}>
                    <button className="secondary" onClick={() => setUserEmail(u)}>Email</button>
                    <button className="secondary" onClick={() => resetPassword(u)}>Сброс пароля</button>
                    <button className="secondary" onClick={() => toggleActive(u)}>
                      {u.is_active ? 'Выключить' : 'Включить'}</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
