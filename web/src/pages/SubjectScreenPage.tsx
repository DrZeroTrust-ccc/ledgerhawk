import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  api,
  money,
  SUBJECT_STATUS,
  type JobKind,
  type PersonResult,
  type ScreenAwards,
  type ScreenJob,
  type ScreenJobs,
  type ScreenContext,
  type SubjectChanges,
  type SubjectResult,
  type SubjectScreen,
} from '../api'
import { useAnalystName } from '../App'
import { Breadcrumbs, usePlace } from '../nav'
import { ContextPanel } from '../Context'
import { AwardBlock, type CaseCtx, Notes, Review, screenCtx } from '../Case'
import { Button, Card, DataClassBadge, ErrorNote, FlagChip, Loading, SignalChip, Stat, useAsync } from '../ui'

const STATUS_STYLE: Record<string, string> = {
  excluded: 'bg-crimson-50 text-crimson ring-1 ring-crimson/30',
  tied: 'bg-crimson-50 text-crimson ring-1 ring-crimson/30',
  related_excluded: 'bg-violet-50 text-violet-800 ring-1 ring-violet-200',
  name_only: 'bg-amber-50 text-amber-800 ring-1 ring-amber-200',
  signals: 'bg-amber-50 text-amber-800 ring-1 ring-amber-200',
  registration: 'bg-navy-50 text-navy ring-1 ring-navy-100',
  clear: 'bg-slate-100 text-slate-600',
}

function StatusChip({ status, label }: { status: string; label?: string }) {
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[status] ?? ''}`}>{label ?? SUBJECT_STATUS[status] ?? status}</span>
}

const PERSON_STYLE: Record<string, string> = { listed: 'signals' }

function Person({ p, ctx, context }: { p: PersonResult; ctx: CaseCtx; context: ScreenContext | null }) {
  const outside = context?.entities.find((c) => c.person_ref === p.ref)
  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-slate-400">Person {p.ref}</span>
          <span>{p.input}</span>
          <StatusChip status={PERSON_STYLE[p.status] ?? p.status} label={p.status_label} />
        </span>
      }
    >
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-4">
          <section>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">What we found</h3>
            <ul className="list-disc space-y-1 pl-5 text-sm text-ink">
              {p.findings.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </section>
          <section>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Next steps</h3>
            <ol className="list-decimal space-y-1 pl-5 text-sm text-ink">
              {p.next_steps.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ol>
          </section>
          {outside && <ContextPanel c={outside} />}
          <Notes target={`p:${p.ref}`} ctx={ctx} />
        </div>
        {p.registrations.length > 0 && (
          <section>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
              SAM registrations listing this person ({p.registrations_total}
              {p.registrations_total > p.registrations.length ? `, first ${p.registrations.length} shown` : ''})
            </h3>
            <ul className="divide-y divide-slate-100 text-sm">
              {p.registrations.map((r) => (
                <li key={r.uei} className="py-1.5">
                  <span className="font-medium">{r.name}</span> <span className="font-mono text-xs text-slate-500">{r.uei}</span>{' '}
                  {r.excluded && <FlagChip flag="EXCLUDED" />}
                  <div className="text-xs text-slate-500">
                    {r.roles.join(', ')} · {r.place}
                    {!r.active && ' · registration not active'}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </Card>
  )
}

function Subject({ s, withDollars, ctx, awards, context }: { s: SubjectResult; withDollars: boolean; ctx: CaseCtx; awards: ScreenAwards | null; context: ScreenContext | null }) {
  const outside = context?.entities.filter((c) => c.ref === s.ref) ?? []
  const awardEntities = awards?.entities.filter((e) => e.refs.includes(s.ref)) ?? []
  const noteCount = ctx.review.notes.filter((n) => n.target === `s:${s.ref}`).length
  const [open, setOpen] = useState(s.status !== 'clear')
  const given = [s.input_uei, s.input_name].filter(Boolean).join(' · ')
  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-slate-400">#{s.ref}</span>
          <span>{s.entities.map((e) => e.sam?.legal_name || e.name).join(' / ') || given}</span>
          <StatusChip status={s.status} />
          {s.role && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">{s.role}</span>}
          {noteCount > 0 && <span className="text-xs font-normal text-slate-500">{noteCount === 1 ? '1 note' : `${noteCount} notes`}</span>}
        </span>
      }
      action={
        <button className="text-xs text-navy hover:underline" onClick={() => setOpen(!open)}>
          {open ? 'Hide' : 'Show'}
        </button>
      }
    >
      <div className="text-xs text-slate-500">
        Given as {given}. {s.resolution}.
      </div>
      {open && (
        <div className="mt-4 grid gap-6 lg:grid-cols-2">
          <div className="space-y-4">
            <section>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">What we found</h3>
              <ul className="list-disc space-y-1 pl-5 text-sm text-ink">
                {s.findings.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ul>
            </section>
            <section>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Next steps</h3>
              <ol className="list-decimal space-y-1 pl-5 text-sm text-ink">
                {s.next_steps.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ol>
            </section>
            <Notes target={`s:${s.ref}`} ctx={ctx} />
          </div>
          <div className="space-y-4">
            {s.entities.map((e) => (
              <section key={e.uei || e.name} className="rounded-md border border-slate-100 p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{e.sam?.legal_name || e.name}</span>
                  {e.uei && <span className="font-mono text-xs text-slate-500">{e.uei}</span>}
                  {e.exclusion_flags.map((f) => (
                    <FlagChip key={f} flag={f} />
                  ))}
                  {e.signals.map((sig) => (
                    <SignalChip key={sig.id + sig.detail} s={sig} />
                  ))}
                </div>
                {e.sam ? (
                  <div className="mt-1 text-xs text-slate-600">
                    {e.sam.active ? 'Active registration' : `Registration not active (exp. ${e.sam.exp_date || '?'})`} · {e.sam.address}
                    {e.sam.start_date && ` · business start ${e.sam.start_date}`}
                    {e.sam.certs.length > 0 && ` · ${e.sam.certs.join(', ')}`}
                  </div>
                ) : (
                  <div className="mt-1 text-xs text-slate-500">No SAM registration in the extract.</div>
                )}
                {withDollars && (
                  <div className="tabular mt-1 text-xs text-slate-600">
                    {e.in_dollars_run
                      ? `${e.dollars_from === 'list' ? 'From the list: ' : ''}FY24 ${money(e.fy24)} · FY25 ${money(e.fy25)}`
                      : 'Not in the selected run'}
                  </div>
                )}
              </section>
            ))}
            {s.related.length > 0 && (
              <section>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Related entities ({s.related_total}
                  {s.related_total > s.related.length ? `, first ${s.related.length} shown` : ''})
                </h3>
                <ul className="divide-y divide-slate-100 text-sm">
                  {s.related.map((r) => (
                    <li key={r.uei} className="py-1.5">
                      <span className="font-medium">{r.name}</span> <span className="font-mono text-xs text-slate-500">{r.uei}</span>{' '}
                      {r.excluded && <FlagChip flag="EXCLUDED" />} {r.flags.filter((f) => f !== 'EXCLUDED').map((f) => <FlagChip key={f} flag={f} />)}
                      <div className="text-xs text-slate-500">{r.via.join('; ')}</div>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {awards && awardEntities.length > 0 && <AwardBlock entities={awardEntities} awards={awards} />}
            {outside.map((c) => (
              <ContextPanel key={c.uei || c.name} c={c} title={outside.length > 1 ? `Outside context: ${c.name}` : 'Outside context'} />
            ))}
          </div>
        </div>
      )}
    </Card>
  )
}

function Changes({ ch }: { ch: SubjectChanges }) {
  const ps = ch.parent_sources
  const since = [ps.sam_extract_date && `SAM ${ps.sam_extract_date}`, ps.exclusions_extract_date && `exclusions ${ps.exclusions_extract_date}`]
    .filter(Boolean)
    .join(', ')
  const arrow = { worse: 'text-crimson', better: 'text-emerald-700', same: 'text-slate-600' }
  return (
    <Card title="What changed since the last check">
      <p className="text-sm text-slate-600">
        Compared with the <Link to={`/subjects/${ch.parent_id}`} className="text-navy underline">screen of {ch.parent_created_at.slice(0, 10)}</Link>
        {since && ` (${since})`}. {ch.counts.changed} changed, {ch.counts.worse} got worse, {ch.counts.better} improved, {ch.counts.unchanged} unchanged.
      </p>
      {ch.subjects.length + (ch.people?.length ?? 0) === 0 ? (
        <p className="mt-3 text-sm text-slate-500">Nothing changed for any subject.</p>
      ) : (
        <ul className="mt-3 divide-y divide-slate-100">
          {[...ch.subjects, ...(ch.people ?? [])].map((r) => (
            <li key={`${'status_before' in r ? 's' : 'p'}${r.ref}${r.name}`} className="py-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-slate-400">#{r.ref}</span>
                <span className="font-medium">{r.name}</span>
                <span className={arrow[r.direction]}>
                  {r.direction === 'same' ? r.status_now_label : `${r.status_before_label} → ${r.status_now_label}`}
                </span>
              </div>
              <ul className="mt-1 list-disc pl-5 text-ink">
                {r.added.map((x, i) => (
                  <li key={`a${i}`}>
                    <span className="font-medium">New:</span> {x}
                  </li>
                ))}
                {r.removed.map((x, i) => (
                  <li key={`r${i}`} className="text-slate-500">
                    No longer found: {x}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

const JOB_KINDS: JobKind[] = ['recheck', 'awards', 'context']

function duration(ms: number) {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return m < 60 ? `${m}m ${String(s % 60).padStart(2, '0')}s` : `${Math.floor(m / 60)}h ${m % 60}m`
}

// Long screen jobs run on the server in the background. The page polls their progress every two seconds while any is
// running, and picks a running job back up after a reload or from another analyst's tab.
function useScreenJobs(id: string, initial: ScreenJobs | undefined, onFinish: (kind: JobKind, job: ScreenJob) => void) {
  const [jobs, setJobs] = useState<ScreenJobs>(initial ?? {})
  const [retry, setRetry] = useState(0)
  const running = JOB_KINDS.some((k) => jobs[k]?.state === 'running')
  useEffect(() => {
    if (!running) return
    const t = setTimeout(async () => {
      try {
        const next = await api.screenJobs(id)
        for (const k of JOB_KINDS) {
          const n = next[k]
          if (jobs[k]?.state === 'running' && n && n.state !== 'running') onFinish(k, n)
        }
        setJobs(next)
      } catch {
        setRetry((n) => n + 1) // the server was busy or restarting; ask again next round
      }
    }, 2000)
    return () => clearTimeout(t)
    // onFinish is left out on purpose: it is a new function each render, and listing it would restart the poll timer
    // every time the page re-renders (oxlint warns about this)
  }, [id, jobs, running, retry])
  const started = (j: ScreenJob) => {
    setJobs((prev) => ({ ...prev, [j.kind]: j }))
    if (j.state !== 'running') onFinish(j.kind, j)
  }
  return { jobs, started }
}

function JobProgress({ job }: { job: ScreenJob }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const elapsed = now - Date.parse(job.started_at)
  const pct = job.total ? Math.min(100, (job.done / job.total) * 100) : 0
  const left = job.done > 0 && job.total > job.done ? `about ${duration((elapsed / job.done) * (job.total - job.done))} left` : ''
  const unit = job.kind === 'context' ? 'names' : job.kind === 'awards' ? 'lookups' : 'steps'
  return (
    <div className="rounded-md border border-navy-100 bg-navy-50 px-3 py-2" role="status" aria-live="polite">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 text-xs text-slate-600">
        <span className="font-medium text-ink">{job.label}…</span>
        <span className="tabular">
          {job.total ? `${job.done} of ${job.total} ${unit}` : 'starting'} · {duration(elapsed)}
          {left && ` · ${left}`}
        </span>
      </div>
      <div className="mt-1 h-2 overflow-hidden rounded-full bg-white" role="progressbar" aria-valuemin={0} aria-valuemax={job.total} aria-valuenow={job.done}>
        <div
          className={`h-full rounded-full bg-navy transition-all duration-700 ${job.done === 0 ? 'w-1/12 animate-pulse' : ''}`}
          style={job.done ? { width: `${Math.max(pct, 2)}%` } : undefined}
        />
      </div>
      <div className="mt-1 truncate text-xs text-slate-500">
        {job.step || 'Working'} · started by {job.by}. You can leave this page; it keeps running and shows here when you come back.
      </div>
    </div>
  )
}

function JobButton({
  id,
  kind,
  job,
  started,
  label,
  busyLabel,
  title,
}: {
  id: string
  kind: JobKind
  job: ScreenJob | undefined
  started: (j: ScreenJob) => void
  label: string
  busyLabel: string
  title: string
}) {
  const [analyst] = useAnalystName()
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const running = job?.state === 'running'
  const start = { awards: api.fetchScreenAwards, context: api.fetchScreenContext, recheck: api.recheckSubjectScreen }[kind]
  return (
    <span className="flex flex-col items-end gap-1">
      <Button
        variant="secondary"
        disabled={sending || running || !analyst.trim()}
        title={analyst.trim() ? title : 'Enter your name in the header first'}
        onClick={async () => {
          setSending(true)
          setError(null)
          const f = new FormData()
          f.append('analyst', analyst)
          try {
            started(await start(id, f))
          } catch (err) {
            setError((err as Error).message)
          } finally {
            setSending(false)
          }
        }}
      >
        {running || sending ? busyLabel : label}
      </Button>
      <ErrorNote error={error} />
    </span>
  )
}

function JobOutcome({ job }: { job: ScreenJob }) {
  return (
    <div className="rounded-md bg-crimson-50 px-3 py-2 text-xs text-crimson">
      {job.label} stopped{job.finished_at ? ` at ${job.finished_at.slice(11, 16)} UTC` : ''}: {job.error}
    </div>
  )
}

function Rename({ data, reload }: { data: SubjectScreen; reload: () => void }) {
  const [analyst] = useAnalystName()
  const m = data.meta
  const [open, setOpen] = useState(false)
  const [matter, setMatter] = useState(m.matter)
  const [client, setClient] = useState(m.client)
  const [error, setError] = useState<string | null>(null)
  const locked = data.review.state === 'approved'
  if (!open)
    return (
      <button
        className="text-xs font-normal text-navy hover:underline disabled:text-slate-400 disabled:no-underline"
        disabled={locked || !analyst.trim()}
        title={locked ? 'Approved screens keep their name. Reopen the screen to rename it.' : analyst.trim() ? 'Rename this screen' : 'Enter your name in the header first'}
        onClick={() => {
          setMatter(m.matter)
          setClient(m.client)
          setOpen(true)
        }}
      >
        Rename
      </button>
    )
  const save = async () => {
    setError(null)
    const f = new FormData()
    f.append('analyst', analyst)
    f.append('matter', matter)
    f.append('client', client)
    try {
      await api.renameScreen(m.id, f)
      setOpen(false)
      reload()
    } catch (err) {
      setError((err as Error).message)
    }
  }
  return (
    <form
      className="flex w-full flex-wrap items-center gap-2 text-sm font-normal"
      onSubmit={(e) => {
        e.preventDefault()
        save()
      }}
    >
      <input
        autoFocus
        value={matter}
        maxLength={200}
        onChange={(e) => setMatter(e.target.value)}
        placeholder="Matter name or number"
        aria-label="Matter name or number"
        className="min-w-[16rem] flex-1 rounded-md border border-slate-300 px-2 py-1"
      />
      <input
        value={client}
        maxLength={200}
        onChange={(e) => setClient(e.target.value)}
        placeholder="Client or instructing counsel"
        aria-label="Client or instructing counsel"
        className="min-w-[12rem] rounded-md border border-slate-300 px-2 py-1"
      />
      <Button type="submit">Save name</Button>
      <button type="button" className="text-xs text-slate-600 hover:underline" onClick={() => setOpen(false)}>
        Cancel
      </button>
      <ErrorNote error={error} />
    </form>
  )
}

function Header({ data, reload }: { data: SubjectScreen; reload: () => void }) {
  const m = data.meta
  const src = data.sources
  const nav = useNavigate()
  const { jobs, started } = useScreenJobs(m.id, data.jobs, (kind, job) => {
    if (kind === 'recheck' && job.state === 'done' && job.result?.id) nav(`/subjects/${job.result.id}`)
    else if (job.state === 'done') reload()
  })
  const running = JOB_KINDS.map((k) => jobs[k]).filter((j): j is ScreenJob => j?.state === 'running')
  const failed = JOB_KINDS.map((k) => jobs[k]).filter((j): j is ScreenJob => j?.state === 'error')
  return (
    <div className="space-y-3">
      {m.privileged && (
        <div className="rounded-md bg-crimson-50 px-3 py-2 text-sm font-semibold text-crimson">Privileged and Confidential. Prepared at the direction of counsel.</div>
      )}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-[18rem] flex-1">
          <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold text-ink">
            {m.matter || 'Untitled matter'} <DataClassBadge dataClass={m.data_class} /> <Rename key={m.matter + m.client} data={data} reload={reload} />
          </h1>
          <p className="text-sm text-slate-500">
            {m.client && `${m.client} · `}Screened by {m.created_by} on {m.created_at.slice(0, 10)} ·{' '}
            {[src.sam_extract_date && `SAM entity extract as of ${src.sam_extract_date}`, src.exclusions_extract_date && `exclusions as of ${src.exclusions_extract_date}`]
              .filter(Boolean)
              .join(' · ')}
            {data.awards && ` · awards from USAspending as of ${data.awards.fetched_at.slice(0, 16).replace('T', ' ')} UTC`}
            {data.context && ` · outside context as of ${data.context.fetched_at.slice(0, 16).replace('T', ' ')} UTC`}
          </p>
          {m.renamed_at && (
            <p className="text-xs text-slate-400">
              Renamed by {m.renamed_by} on {m.renamed_at.slice(0, 10)} (screened as &ldquo;{m.original_matter || 'Untitled matter'}&rdquo;)
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-start gap-2">
          <JobButton
            id={m.id}
            kind="context"
            job={jobs.context}
            started={started}
            label={data.context ? 'Refresh outside context' : 'Search news, courts, DOJ, SEC, OFAC'}
            busyLabel="Searching outside sources…"
            title="News, DOJ press releases, federal court records, SEC filings and the OFAC list for every subject and person"
          />
          {data.subjects.length > 0 && (
            <JobButton
              id={m.id}
              kind="awards"
              job={jobs.awards}
              started={started}
              label={data.awards ? 'Refresh awards' : 'Look up awards (USAspending)'}
              busyLabel="Looking up awards…"
              title="Contracts and IDVs reported to USAspending.gov for each subject UEI and excluded related firm"
            />
          )}
          <JobButton
            id={m.id}
            kind="recheck"
            job={jobs.recheck}
            started={started}
            label="Re-check with latest data"
            busyLabel="Re-checking…"
            title="Run the same subjects against the newest SAM and exclusions extracts"
          />
          <a
            href={`/api/subject-screens/${encodeURIComponent(m.id)}/subject-screen.docx`}
            className="rounded-md bg-navy px-3 py-1.5 text-sm font-medium text-white hover:bg-ink"
          >
            Download report (Word)
          </a>
          <a
            href={`/api/subject-screens/${encodeURIComponent(m.id)}/subject-screen.xlsx`}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-navy hover:bg-slate-50"
          >
            Workbook (Excel)
          </a>
          <a
            href={`/api/subject-screens/${encodeURIComponent(m.id)}/link-chart.xlsx`}
            title="Entities and links to import into i2 Analyst's Notebook or Maltego"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-navy hover:bg-slate-50"
          >
            Link chart (i2 / Maltego)
          </a>
        </div>
      </div>
      {running.map((j) => (
        <JobProgress key={j.kind} job={j} />
      ))}
      {failed.map((j) => (
        <JobOutcome key={j.kind} job={j} />
      ))}
    </div>
  )
}

export default function SubjectScreenPage() {
  const { id = '' } = useParams()
  const { data, error, reload } = useAsync(() => api.subjectScreen(id), [id])
  usePlace(data ? `${data.meta.matter || 'Untitled matter'} (subject screen)` : null)
  const [status, setStatus] = useState('')
  if (error) return <ErrorNote error={error} />
  if (!data) return <Loading />
  const c = data.counts
  const shown = data.subjects.filter((s) => !status || s.status === status)
  const ctx: CaseCtx = screenCtx(data.meta.id, data.review, reload)
  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Subject screens', to: '/subjects' }, { label: data.meta.matter || 'Untitled matter' }]} />
      <Header key={data.meta.id} data={data} reload={reload} />
      {data.changes && <Changes ch={data.changes} />}
      <Review ctx={ctx} notesTarget="screen" />
      <Card>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Subjects" value={c.subjects} />
          <Stat label="Excluded or tied to an excluded party" value={(c.excluded ?? 0) + (c.tied ?? 0)} />
          {/* Counted from the cards, not from statuses: a subject that is itself tied to an excluded party takes that
              status, so the "related entity is excluded" status count would miss it */}
          <Stat label="Subjects with an excluded related firm" value={data.subjects.filter((s) => s.related.some((r) => r.excluded)).length} />
          <Stat label="Related entities found" value={c.related} sub={c.people ? `${c.people} people screened` : undefined} />
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <button onClick={() => setStatus('')} className={`rounded px-2 py-1 text-xs ${status === '' ? 'bg-navy text-white' : 'bg-slate-100 text-slate-700'}`}>
            All ({c.subjects})
          </button>
          {Object.entries(SUBJECT_STATUS)
            .filter(([k]) => c[k])
            .map(([k, label]) => (
              <button key={k} onClick={() => setStatus(k)} className={`rounded px-2 py-1 text-xs ${status === k ? 'bg-navy text-white' : 'bg-slate-100 text-slate-700'}`}>
                {label} ({c[k]})
              </button>
            ))}
        </div>
      </Card>
      {shown.map((s) => (
        <Subject key={s.ref} s={s} withDollars={!!data.meta.dollars_run || s.entities.some((e) => e.dollars_from === 'list')} ctx={ctx} awards={data.awards} context={data.context} />
      ))}
      {(data.people?.length ?? 0) > 0 && (
        <>
          <h2 className="pt-2 text-lg font-semibold text-ink">People</h2>
          {data.people!.map((p) => (
            <Person key={p.ref} p={p} ctx={ctx} context={data.context} />
          ))}
        </>
      )}
      <p className="text-xs text-slate-500">
        Public federal data only. Shared contacts, addresses and names are leads to test, not proof of common ownership or control. A subject with no hits is
        not cleared; the sources and dates above are the scope of this check.
      </p>
    </div>
  )
}
