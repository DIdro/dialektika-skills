import { useState } from 'react'
import { useAuth } from '../auth'
import { api, ApiError } from '../api'

export default function Account() {
  const { user } = useAuth()
  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newPassword2, setNewPassword2] = useState('')
  const [error, setError] = useState('')
  const [ok, setOk] = useState(false)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(''); setOk(false)
    if (newPassword !== newPassword2) {
      setError('Новые пароли не совпадают')
      return
    }
    setBusy(true)
    try {
      await api('/web/change-password', {
        method: 'POST',
        body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
      })
      setOk(true)
      setOldPassword(''); setNewPassword(''); setNewPassword2('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Ошибка')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <h2>Профиль</h2>

      <div className="card">
        <div><strong>{user?.name}</strong></div>
        {user?.email && <div className="muted">{user.email}</div>}
      </div>

      <form className="card" onSubmit={submit} style={{ marginTop: 18, maxWidth: 360 }}>
        <h3 style={{ marginTop: 0 }}>Смена пароля</h3>
        <div className="row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
          <input type="password" placeholder="Текущий пароль" value={oldPassword}
                 onChange={e => setOldPassword(e.target.value)} required autoComplete="current-password" />
          <input type="password" placeholder="Новый пароль (мин. 8 символов)" value={newPassword}
                 onChange={e => setNewPassword(e.target.value)} required minLength={8}
                 autoComplete="new-password" />
          <input type="password" placeholder="Повтори новый пароль" value={newPassword2}
                 onChange={e => setNewPassword2(e.target.value)} required minLength={8}
                 autoComplete="new-password" />
          {error && <div className="error">{error}</div>}
          {ok && <div className="muted">Пароль изменён.</div>}
          <button className="btn" disabled={busy} style={{ alignSelf: 'flex-start' }}>
            {busy ? 'Сохранение…' : 'Сохранить'}
          </button>
        </div>
      </form>
    </div>
  )
}
