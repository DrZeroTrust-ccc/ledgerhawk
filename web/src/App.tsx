import { createContext, useContext } from 'react'
import { Link, NavLink, Route, Routes, useMatch } from 'react-router-dom'
import { useAnalyst } from './ui'
import RunsPage from './pages/RunsPage'
import RunDashboard from './pages/RunDashboard'
import QueuePage from './pages/QueuePage'
import VendorPage from './pages/VendorPage'
import AuditPage from './pages/AuditPage'
import ExclusionGapsPage from './pages/ExclusionGapsPage'
import IntegrityPage from './pages/IntegrityPage'
import SubjectsPage from './pages/SubjectsPage'
import SubjectScreenPage from './pages/SubjectScreenPage'

const FOOTER = 'Screening signals and dollars under review, not findings of fraud.'

export const AnalystContext = createContext<[string, (v: string) => void]>(['', () => {}])
export const useAnalystName = () => useContext(AnalystContext)

function Wordmark() {
  return (
    <Link to="/" className="text-lg font-extrabold tracking-tight">
      <span className="text-white">LEDGER</span>
      <span className="text-[#e04a5f]">HAWK</span>
    </Link>
  )
}

function Nav() {
  const m = useMatch('/runs/:id/*')
  const id = m?.params.id
  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded-md px-3 py-1.5 text-sm font-medium ${isActive ? 'bg-white/15 text-white' : 'text-white/75 hover:text-white'}`
  return (
    <nav className="flex flex-wrap items-center gap-1">
      <NavLink to="/" end className={link}>
        Runs
      </NavLink>
      {id && (
        <>
          <NavLink to={`/runs/${id}`} end className={link}>
            Run dashboard
          </NavLink>
          <NavLink to={`/runs/${id}/queue`} className={link}>
            Queue
          </NavLink>
          <NavLink to={`/runs/${id}/integrity`} className={link}>
            Integrity lane
          </NavLink>
          <NavLink to={`/runs/${id}/exclusion-gaps`} className={link}>
            Exclusion gaps
          </NavLink>
        </>
      )}
      <NavLink to="/subjects" className={link}>
        Subject screen
      </NavLink>
      <NavLink to="/audit" className={link}>
        Audit log
      </NavLink>
    </nav>
  )
}

export default function App() {
  const analyst = useAnalyst()
  const [name, setName] = analyst
  return (
    <AnalystContext.Provider value={analyst}>
      <div className="flex min-h-screen flex-col">
        <header className="bg-navy">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
            <Wordmark />
            <Nav />
            <label className="ml-auto flex items-center gap-2 text-xs text-white/75">
              Analyst
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Your name"
                className="w-40 rounded bg-white/10 px-2 py-1 text-sm text-white placeholder:text-white/40 focus:bg-white/20 focus:outline-none"
              />
            </label>
          </div>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">
          <Routes>
            <Route path="/" element={<RunsPage />} />
            <Route path="/runs/:id" element={<RunDashboard />} />
            <Route path="/runs/:id/queue" element={<QueuePage />} />
            <Route path="/runs/:id/vendors/:uei" element={<VendorPage />} />
            <Route path="/runs/:id/exclusion-gaps" element={<ExclusionGapsPage />} />
            <Route path="/runs/:id/integrity" element={<IntegrityPage />} />
            <Route path="/subjects" element={<SubjectsPage />} />
            <Route path="/subjects/:id" element={<SubjectScreenPage />} />
            <Route path="/audit" element={<AuditPage />} />
          </Routes>
        </main>
        <footer className="border-t border-slate-200 bg-white">
          <div className="mx-auto max-w-7xl px-4 py-3 text-xs text-slate-500 sm:px-6">{FOOTER}</div>
        </footer>
      </div>
    </AnalystContext.Provider>
  )
}
