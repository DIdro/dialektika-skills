import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, type Me } from './api'

type AuthCtx = {
  user: Me | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const Ctx = createContext<AuthCtx>(null!)
export const useAuth = () => useContext(Ctx)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api<Me>('/web/me')
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  const login = async (email: string, password: string) => {
    const me = await api<Me>('/web/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
    setUser(me)
  }

  const logout = async () => {
    await api('/web/logout', { method: 'POST' })
    setUser(null)
  }

  return <Ctx.Provider value={{ user, loading, login, logout }}>{children}</Ctx.Provider>
}
