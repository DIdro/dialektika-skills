import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'
import { ApiError } from '../api'

export default function Login() {
  const { login } = useAuth()
  const nav = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      await login(email, password)
      nav('/', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError && err.status === 429
        ? 'Слишком много попыток — подожди минуту'
        : 'Неверный email или пароль')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="login" onSubmit={submit}>
        <div className="brand">
          <span className="brand-eq" aria-hidden><i /><i /><i /><i /></span>
          call-pipeline
        </div>
        <h1>Вход в кабинет</h1>
        <input type="email" placeholder="Email" value={email}
               onChange={e => setEmail(e.target.value)} required autoFocus />
        <input type="password" placeholder="Пароль" value={password}
               onChange={e => setPassword(e.target.value)} required />
        {error && <div className="error">{error}</div>}
        <button className="btn" disabled={busy}>{busy ? 'Вход…' : 'Войти'}</button>
      </form>
    </div>
  )
}
