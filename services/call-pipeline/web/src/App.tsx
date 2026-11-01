import { BrowserRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth'
import Login from './pages/Login'
import Recordings from './pages/Recordings'
import Upload from './pages/Upload'
import Library from './pages/Library'
import JobView from './pages/JobView'
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

function Shell({ children }: { children: React.ReactNode }) {
  const { user, loading, logout } = useAuth()
  if (loading) return <div className="center muted">Загрузка…</div>
  if (!user) return <Navigate to="/login" replace />
  return (
    <div className="shell">
      <aside className="sidebar">
        <Brand />
        <nav>
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
          <Route path="/jobs/:jobId" element={<Shell><JobView /></Shell>} />
          <Route path="/admin" element={<Shell><Admin /></Shell>} />
          <Route path="/account" element={<Shell><Account /></Shell>} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
