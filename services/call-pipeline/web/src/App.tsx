import { useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth'
import { ingestApi, REVIEW_QUEUE_EVENT } from './api'
import Login from './pages/Login'
import Recordings from './pages/Recordings'
import Upload from './pages/Upload'
import Library from './pages/Library'
import JobView from './pages/JobView'
import Review from './pages/Review'
import Admin from './pages/Admin'
import Account from './pages/Account'

function Brand() {
  return (
    <div className="brand">
      <span className="brand-eq" aria-hidden><i /><i /><i /><i /></span>
      call-pipeline
    </div>
  )
}

function ReviewLink() {
  const [n, setN] = useState(0)
  useEffect(() => {
    const load = () => {
      ingestApi<{ reviews: unknown[] }>('/reviews?status=awaiting')
        .then(b => setN(b.reviews.length)).catch(() => setN(0))
    }
    load()
    // Пересчитать после каждого решения на странице «Разбор» — иначе бейдж висит
    // со старым числом, пока владелец не перезагрузит страницу целиком.
    window.addEventListener(REVIEW_QUEUE_EVENT, load)
    return () => window.removeEventListener(REVIEW_QUEUE_EVENT, load)
  }, [])
  return <NavLink to="/review">Разбор{n > 0 && <span className="badge"> {n}</span>}</NavLink>
}

function Shell({ children }: { children: React.ReactNode }) {
  const { user, loading, logout } = useAuth()
  if (loading) return <div className="center muted">Загрузка…</div>
  if (!user) return <Navigate to="/login" replace />
  return (
    <div className="shell">
      <aside className="sidebar">
        <Brand />
        <nav>
          <ReviewLink />
          <NavLink to="/" end>Записи</NavLink>
          <NavLink to="/upload">Загрузка</NavLink>
          <NavLink to="/library">Библиотека</NavLink>
          {user.is_admin && <NavLink to="/admin">Пользователи</NavLink>}
        </nav>
        <div className="sidebar-foot">
          <NavLink to="/account" className="who" style={{ textDecoration: 'none' }}>
            <div className="who-name">{user.name}</div>
            {user.email && <div className="who-mail muted">{user.email}</div>}
          </NavLink>
          <button className="ghost" onClick={logout}>Выйти</button>
        </div>
      </aside>
      <main>{children}</main>
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Shell><Recordings /></Shell>} />
          <Route path="/upload" element={<Shell><Upload /></Shell>} />
          <Route path="/library" element={<Shell><Library /></Shell>} />
          <Route path="/review" element={<Shell><Review /></Shell>} />
          <Route path="/jobs/:jobId" element={<Shell><JobView /></Shell>} />
          <Route path="/admin" element={<Shell><Admin /></Shell>} />
          <Route path="/account" element={<Shell><Account /></Shell>} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
