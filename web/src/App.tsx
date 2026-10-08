import { createContext, useContext } from 'react'
import { Link, NavLink, Route, Routes, useLocation, useMatch, useNavigate } from 'react-router-dom'
import { api, type Me } from './api'
import { lastRunId, queueHref, RunsContext, useNavMemory, useRuns } from './nav'
import { useAnalyst, useAsync } from './ui'
import RunsPage from './pages/RunsPage'
import RunDashboard from './pages/RunDashboard'
import QueuePage from './pages/QueuePage'
import VendorPage from './pages/VendorPage'
import AuditPage from './pages/AuditPage'
import ExclusionGapsPage from './pages/ExclusionGapsPage'
import IntegrityPage from './pages/IntegrityPage'
import SubjectsPage from './pages/SubjectsPage'
import SubjectScreenPage from './pages/SubjectScreenPage'
import RunRecordPage from './pages/RunRecordPage'
import MyCasesPage from './pages/MyCasesPage'
import VendorsPage from './pages/VendorsPage'
import VendorLookupPage from './pages/VendorLookupPage'
import LinkMapPage from './pages/LinkMapPage'
import PeoplePage from './pages/PeoplePage'
import PoliciesPage from './pages/PoliciesPage'
import PolicyPage from './pages/PolicyPage'
import PolicyEditorPage from './pages/PolicyEditorPage'
import PolicyReviewPage from './pages/PolicyReviewPage'

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

const tab = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-1.5 text-sm font-medium ${isActive ? 'bg-white/15 text-white' : 'text-white/75 hover:text-white'}`

function Nav({ admin }: { admin: boolean }) {
  const onRun = !!useMatch('/runs/:id/*')
  return (
    <nav className="flex flex-wrap items-center gap-1">
      <NavLink to="/" end className={({ isActive }) => tab({ isActive: isActive || onRun })}>
        Imports
      </NavLink>
      <NavLink to="/vendors" className={tab}>
        Vendors
      </NavLink>
      <NavLink to="/subjects" className={tab}>
        Subject screens
      </NavLink>
      <NavLink to="/my-cases" className={tab}>
        My cases
      </NavLink>
      <NavLink to="/policies" className={tab}>
        Policies
      </NavLink>
      <NavLink to="/audit" className={tab}>
        Audit log
      </NavLink>
      {admin && (
        <NavLink to="/people" className={tab}>
          People
        </NavLink>
      )}
    </nav>
  )
}

/** The run you're working in stays one click away from every page, with a switcher for the others. */
function RunBar() {
  const m = useMatch('/runs/:id/*')
  const { runs } = useRuns()
  const nav = useNavigate()
  const loc = useLocation()
  const remembered = lastRunId()
  const id = m?.params.id ?? (runs?.some((r) => r.id === remembered) ? remembered : null)
  if (!id) return null
  const here = !!m
  const sub = ({ isActive }: { isActive: boolean }) =>
    `whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium ${
      isActive && here ? 'border-navy text-navy' : 'border-transparent text-slate-600 hover:text-navy'
    }`
  const onQueue = loc.pathname === `/runs/${id}/queue` || /^\/runs\/[^/]+\/vendors\//.test(loc.pathname)
  return (
    <div className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 px-4 sm:px-6">
        <label className="flex min-w-0 items-center gap-2 py-1.5 text-xs text-slate-500">
          {here ? 'Import' : 'Last import'}
          <select
            value={id}
            onChange={(e) => nav(`/runs/${e.target.value}`)}
            className="max-w-[18rem] truncate rounded border border-slate-300 bg-white px-2 py-1 text-sm text-ink"
          >
            {!runs?.some((r) => r.id === id) && <option value={id}>{id}</option>}
            {runs?.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label} · {r.created_at.slice(0, 10)}
                {r.follows_id ? ' · follow-up' : ''}
                {r.data_class === 'synthetic' ? ' · synthetic' : ''}
              </option>
            ))}
          </select>
        </label>
        <div className="-mb-px flex flex-wrap">
          <NavLink to={`/runs/${id}`} end className={sub}>
            Dashboard
          </NavLink>
          <NavLink to={queueHref(id)} className={() => sub({ isActive: onQueue })}>
            Queue
          </NavLink>
          <NavLink to={`/runs/${id}/integrity`} className={sub}>
            Integrity lane
          </NavLink>
          <NavLink to={`/runs/${id}/exclusion-gaps`} className={sub}>
            Exclusion gaps
          </NavLink>
          <NavLink to={`/runs/${id}/record`} className={sub}>
            Import record
          </NavLink>
        </div>
      </div>
    </div>
  )
}

// Signed in but no role yet: nothing else to show.
function Pending({ me }: { me: Extract<Me, { auth: 'access' }> }) {
  return (
    <div className="mx-auto max-w-xl space-y-3 py-16 text-center">
      <h1 className="text-2xl font-semibold text-navy">You're signed in, but not set up yet</h1>
      <p className="text-slate-600">
        {me.email} can sign in, but an Admin hasn't given it a role in LedgerHawk. Ask an Admin to add you on the People page; then reload this page.
      </p>
    </div>
  )
}

export default function App() {
  const typed = useAnalyst()
  const meQ = useAsync(() => api.me(), [])
  const me = meQ.data
  const signedIn = me?.auth === 'access' ? me : null
  // Signed in, actions are recorded under the person's own name; otherwise analysts type theirs, as before.
  const analyst: [string, (v: string) => void] = signedIn ? [signedIn.name, () => {}] : typed
  const [name, setName] = analyst
  const loc = useLocation()
  const runId = useMatch('/runs/:id/*')?.params.id
  // reload the run list when a new run appears (just created, or opened from a link)
  const runs = useAsync(() => api.runs(), [loc.pathname === '/' ? 'home' : '', runId ?? ''])
  useNavMemory()
  return (
    <RunsContext.Provider value={{ runs: runs.data, reload: runs.reload }}>
      <AnalystContext.Provider value={analyst}>
        <div className="flex min-h-screen flex-col">
          <header className="bg-navy">
            <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
              <Wordmark />
              <Nav admin={signedIn?.role === 'admin'} />
              {signedIn ? (
                <span className="ml-auto flex items-center gap-2 text-sm text-white/85" title={signedIn.email}>
                  {signedIn.name}
                  <span className="rounded bg-white/15 px-1.5 py-0.5 text-xs font-semibold text-white">{signedIn.role_label}</span>
                </span>
              ) : (
                <label className="ml-auto flex items-center gap-2 text-xs text-white/75">
                  Analyst
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Your name"
                    className="w-40 rounded bg-white/10 px-2 py-1 text-sm text-white placeholder:text-white/40 focus:bg-white/20 focus:outline-none"
                  />
                </label>
              )}
            </div>
          </header>
          {signedIn?.role === 'executive' && (
            <div className="border-b border-amber-200 bg-amber-50 text-sm text-amber-900">
              <div className="mx-auto max-w-7xl px-4 py-2 sm:px-6">
                You have read-only access as an Executive: you can see everything, but changes are turned away.
              </div>
            </div>
          )}
          {signedIn?.role !== null && <RunBar />}
          <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">
            {signedIn && signedIn.role === null ? (
              <Pending me={signedIn} />
            ) : (
              <Routes>
                <Route path="/" element={<RunsPage />} />
                <Route path="/runs/:id" element={<RunDashboard />} />
                <Route path="/runs/:id/queue" element={<QueuePage />} />
                <Route path="/runs/:id/vendors/:uei" element={<VendorPage />} />
                <Route path="/runs/:id/vendors/:uei/map" element={<LinkMapPage />} />
                <Route path="/runs/:id/exclusion-gaps" element={<ExclusionGapsPage />} />
                <Route path="/runs/:id/integrity" element={<IntegrityPage />} />
                <Route path="/runs/:id/record" element={<RunRecordPage />} />
                <Route path="/vendors" element={<VendorsPage />} />
                <Route path="/vendors/:uei" element={<VendorLookupPage />} />
                <Route path="/subjects" element={<SubjectsPage />} />
                <Route path="/subjects/:id" element={<SubjectScreenPage />} />
                <Route path="/audit" element={<AuditPage />} />
                <Route path="/my-cases" element={<MyCasesPage />} />
            <Route path="/policies" element={<PoliciesPage />} />
            <Route path="/policies/:id" element={<PolicyPage />} />
            <Route path="/policies/:id/edit" element={<PolicyEditorPage />} />
            <Route path="/policies/:id/review" element={<PolicyReviewPage />} />
                {signedIn?.role === 'admin' && <Route path="/people" element={<PeoplePage myEmail={signedIn.email} />} />}
              </Routes>
            )}
          </main>
          <footer className="border-t border-slate-200 bg-white">
            <div className="mx-auto max-w-7xl px-4 py-3 text-xs text-slate-500 sm:px-6">{FOOTER}</div>
          </footer>
        </div>
      </AnalystContext.Provider>
    </RunsContext.Provider>
  )
}
