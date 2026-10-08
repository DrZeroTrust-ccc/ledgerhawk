import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api, type ImportJob } from './api'

// Imports run in the background on the server. These show that one is being handled: a badge in the top bar while
// any import runs, a card on the Imports page, and a progress line where an import was started.

const KIND: Record<ImportJob['kind'], string> = { new: 'Import', follow_up: 'Follow-up import', restore: 'Restore' }

function elapsed(from: string, to?: string) {
  const s = Math.max(0, Math.round(((to ? Date.parse(to) : Date.now()) - Date.parse(from)) / 1000))
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
}

/** Follow one job until it finishes; calls onDone(job) once when it's done or failed. */
export function useImportJob(start: ImportJob | null, onDone?: (j: ImportJob) => void) {
  const [job, setJob] = useState<ImportJob | null>(start)
  const [, tick] = useState(0)
  useEffect(() => setJob(start), [start])
  const id = job?.id
  const live = job ? job.state === 'queued' || job.state === 'running' : false
  useEffect(() => {
    if (!id || !live) return
    let stop = false
    const t = setInterval(async () => {
      tick((n) => n + 1) // keeps the elapsed time moving
      try {
        const j = await api.importJob(id)
        if (stop) return
        setJob(j)
        if (j.state === 'done' || j.state === 'error') onDone?.(j)
      } catch {
        // a blip; the next tick tries again
      }
    }, 2000)
    return () => {
      stop = true
      clearInterval(t)
    }
    // onDone is read when the job finishes; following it as a dependency would restart the timer every render
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, live])
  return job
}

function Spinner() {
  return <span aria-hidden className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-navy/30 border-t-navy" />
}

/** One job's progress, for where it was started. */
export function ImportProgress({ job }: { job: ImportJob }) {
  if (job.state === 'error') return null
  return (
    <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-navy/20 bg-navy-50 px-3 py-2 text-sm text-ink">
      {job.state !== 'done' && <Spinner />}
      <span className="font-medium">
        {KIND[job.kind]}: {job.label}
      </span>
      <span className="text-slate-600">
        {job.state === 'queued' ? 'Waiting for another import to finish' : job.state === 'done' ? 'Done' : job.step}
      </span>
      <span className="tabular text-xs text-slate-500">{elapsed(job.started_at, job.finished_at || undefined)}</span>
      {job.state !== 'done' && (
        <span className="w-full text-xs text-slate-500">
          This keeps going if you leave the page; the badge at the top shows it’s still running. Large files take a few minutes.
        </span>
      )}
    </div>
  )
}

/** The top-bar badge: shown while any import is queued or running. */
export function ImportsIndicator() {
  const [jobs, setJobs] = useState<ImportJob[]>([])
  const loc = useLocation()
  useEffect(() => {
    let stop = false
    const load = () =>
      api.importJobs(true).then(
        (r) => !stop && setJobs(r.jobs),
        () => {},
      )
    load()
    const t = setInterval(load, jobs.length ? 3000 : 15000)
    return () => {
      stop = true
      clearInterval(t)
    }
  }, [loc.pathname, jobs.length])
  if (!jobs.length) return null
  const j = jobs[jobs.length - 1]
  return (
    <Link
      to="/"
      title={jobs.map((x) => `${KIND[x.kind]}: ${x.label} (${x.step})`).join('\n')}
      className="inline-flex items-center gap-2 rounded-full bg-white/15 px-3 py-1 text-xs font-medium text-white hover:bg-white/25"
    >
      <span aria-hidden className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-white/40 border-t-white" />
      {jobs.length === 1 ? `Importing ${j.label}` : `${jobs.length} imports running`}
    </Link>
  )
}

/** The Imports page card: what's running now, and what finished or failed recently. */
export function RecentImports() {
  const [jobs, setJobs] = useState<ImportJob[] | null>(null)
  const active = (jobs ?? []).some((j) => j.state === 'queued' || j.state === 'running')
  useEffect(() => {
    let stop = false
    const load = () =>
      api.importJobs(false).then(
        (r) => !stop && setJobs(r.jobs),
        () => {},
      )
    load()
    const t = setInterval(load, active ? 2000 : 15000)
    return () => {
      stop = true
      clearInterval(t)
    }
  }, [active])
  const day = Date.now() - 24 * 3600 * 1000
  const shown = (jobs ?? [])
    .filter((j) => j.state === 'queued' || j.state === 'running' || Date.parse(j.finished_at || j.started_at) > day)
    .slice(0, 6)
  if (!shown.length) return null
  return (
    <section aria-label="Imports in progress" className="rounded-lg border border-slate-200 bg-white px-5 py-4">
      <h2 className="text-sm font-semibold text-navy">{active ? 'Imports in progress' : 'Recent imports'}</h2>
      <ul className="mt-2 divide-y divide-slate-100 text-sm">
        {shown.map((j) => (
          <li key={j.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
            {(j.state === 'queued' || j.state === 'running') && <Spinner />}
            <span className="font-medium">
              {KIND[j.kind]}: {j.label}
            </span>
            <span className={j.state === 'error' ? 'text-crimson' : 'text-slate-600'}>
              {j.state === 'queued'
                ? 'Waiting for another import to finish'
                : j.state === 'error'
                  ? `Failed: ${j.error}`
                  : j.state === 'done'
                    ? 'Done'
                    : j.step}
            </span>
            <span className="tabular text-xs text-slate-500">
              {j.by} · {elapsed(j.started_at, j.finished_at || undefined)}
            </span>
            {j.state === 'done' && j.run_id && (
              <Link to={`/runs/${j.run_id}`} className="ml-auto text-navy underline">
                Open
              </Link>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
