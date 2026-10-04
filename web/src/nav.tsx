// Navigation memory: the run you were last in, the queue filters you last used per run, and the last page you were on.
// Kept in localStorage so a reload, a new tab or a redeploy doesn't drop you back on the Runs list.
import { createContext, useContext, useEffect } from 'react'
import { Link, matchPath, useLocation } from 'react-router-dom'
import type { RunMeta } from './api'

const LAST_RUN = 'ledgerhawk.lastRun'
const LAST_PLACE = 'ledgerhawk.lastPlace'
const QUEUE = 'ledgerhawk.queue.'

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    // private window or blocked storage: navigation still works, it just isn't remembered
  }
}

export const lastRunId = () => read(LAST_RUN)

/** Queue URL for a run with the filters last used there, so "back to queue" returns to the same list. */
export const queueHref = (runId: string) => `/runs/${runId}/queue${read(QUEUE + runId) ?? ''}`

export type Place = { path: string; label: string; at: string }

export function lastPlace(): Place | null {
  try {
    const p = JSON.parse(read(LAST_PLACE) ?? 'null')
    return p && typeof p.path === 'string' ? p : null
  } catch {
    return null
  }
}

export function rememberPlace(path: string, label: string) {
  write(LAST_PLACE, JSON.stringify({ path, label, at: new Date().toISOString() }))
}

/** Records the current run and queue filters on every route change. */
export function useNavMemory() {
  const loc = useLocation()
  useEffect(() => {
    const run = matchPath('/runs/:id/*', loc.pathname) ?? matchPath('/runs/:id', loc.pathname)
    if (run?.params.id) write(LAST_RUN, run.params.id)
    const queue = matchPath('/runs/:id/queue', loc.pathname)
    if (queue?.params.id) write(QUEUE + queue.params.id, loc.search)
  }, [loc.pathname, loc.search])
}

export const RunsContext = createContext<{ runs: RunMeta[] | null; reload: () => void }>({ runs: null, reload: () => {} })
export const useRuns = () => useContext(RunsContext)

export function runLabel(runs: RunMeta[] | null, id: string) {
  const r = runs?.find((x) => x.id === id)
  return r ? `${r.label} · ${r.created_at.slice(0, 10)}` : id
}

export type Crumb = { label: string; to?: string }

/** Where you are, with every level above it one click away. The last crumb is the current page. */
export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-1 text-sm text-slate-500">
      {items.map((c, i) => (
        <span key={i} className="flex min-w-0 items-center gap-1">
          {i > 0 && <span className="text-slate-300">›</span>}
          {c.to ? (
            <Link to={c.to} className="truncate text-navy hover:underline">
              {c.label}
            </Link>
          ) : (
            <span className="truncate text-slate-700">{c.label}</span>
          )}
        </span>
      ))}
    </nav>
  )
}

/** Remember this page (with its query string) as the place to pick up from, once its label is known. */
export function usePlace(label: string | null | undefined) {
  const loc = useLocation()
  useEffect(() => {
    if (label) rememberPlace(loc.pathname + loc.search, label)
  }, [label, loc.pathname, loc.search])
}
