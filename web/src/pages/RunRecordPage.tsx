import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Breadcrumbs, usePlace } from '../nav'
import { api, FLAG_LABEL, money, num, type ChangeItem, type ChangeWhat, type ImportJob, type RunRecord } from '../api'
import { ImportProgress, useImportJob } from '../imports'
import { useAnalystName } from '../App'
import { Button, Card, DataClassBadge, ErrorNote, Loading, Stat, useAsync } from '../ui'

const when = (iso: string) => new Date(iso).toLocaleString()
const day = (iso: string) => iso.slice(0, 10)

export function changeWords(w: ChangeWhat): string {
  switch (w.kind) {
    case 'flags':
      return `New exclusion finding: ${w.added.map((f) => FLAG_LABEL[f] ?? f).join(', ')}`
    case 'signals':
      return `New signal: ${w.added.join(', ')}`
    case 'signals_gone':
      return `Signal no longer present: ${w.removed.join(', ')}`
    case 'tier':
      return `Suggested tier moved from ${w.from} to ${w.to}`
    case 'dollars':
      return `Dollars went from ${money(w.from)} to ${money(w.to)}`
  }
}

/** Start a follow-up run: the same vendor file against the newest SAM and exclusions extracts, linked to this run. */
export function FollowUpButton({ runId }: { runId: string }) {
  const [analyst] = useAnalystName()
  const nav = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [job, setJob] = useState<ImportJob | null>(null)
  const watched = useImportJob(job, (j) => {
    if (j.state === 'done') nav(`/runs/${j.run_id}/record`)
    else {
      setError(`The follow-up import didn’t finish: ${j.error}`)
      setBusy(false)
      setJob(null)
    }
  })
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Button
        variant="secondary"
        disabled={busy || !analyst.trim()}
        title={analyst.trim() ? 'Re-screen this file against the newest extracts, linked to this import' : 'Enter your name in the header first'}
        onClick={async () => {
          setBusy(true)
          setError(null)
          const f = new FormData()
          f.append('analyst', analyst)
          try {
            const { id, job } = await api.followUp(runId, f)
            if (id) nav(`/runs/${id}/record`)
            else setJob(job)
          } catch (err) {
            setError((err as Error).message)
            setBusy(false)
          }
        }}
      >
        {busy ? 'Importing…' : 'Start follow-up import'}
      </Button>
      {watched && <ImportProgress job={watched} />}
      <ErrorNote error={error} />
    </span>
  )
}

function ChangeList({ runId, title, items, empty }: { runId: string; title: string; items: ChangeItem[]; empty: string }) {
  const [all, setAll] = useState(false)
  const shown = all ? items : items.slice(0, 8)
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-navy">
        {title} <span className="font-normal text-slate-500">({num(items.length)})</span>
      </h3>
      {items.length === 0 && <p className="text-sm text-slate-500">{empty}</p>}
      <ul className="divide-y divide-slate-100 text-sm">
        {shown.map((c) => (
          <li key={c.uei} className="py-2">
            <div className="flex items-baseline justify-between gap-3">
              <Link to={`/runs/${runId}/vendors/${c.uei}`} className="truncate font-medium text-navy hover:underline">
                {c.name}
              </Link>
              <span className="tabular shrink-0 text-slate-600">{money(c.tot)}</span>
            </div>
            {c.why && <div className="text-xs text-slate-600">{c.why}</div>}
            {c.what?.map((w, i) => (
              <div key={i} className="text-xs text-slate-600">
                {changeWords(w)}
              </div>
            ))}
          </li>
        ))}
      </ul>
      {items.length > shown.length && (
        <button onClick={() => setAll(true)} className="mt-1 text-xs font-medium text-navy hover:underline">
          Show all {num(items.length)}
        </button>
      )}
    </div>
  )
}

function Changes({ runId, rec }: { runId: string; rec: RunRecord }) {
  const { data } = useAsync(() => api.run(runId), [runId])
  const ch = data?.changes
  if (!rec.follows) return null
  return (
    <Card title={`What changed since ${rec.follows.label} (${day(rec.follows.created_at)})`}>
      {!ch && <Loading />}
      {ch && (
        <div className="grid gap-6 md:grid-cols-3">
          <ChangeList runId={runId} title="New to the queue" items={ch.new} empty="No new leads." />
          <ChangeList runId={runId} title="Changed" items={ch.changed} empty="No queued vendor changed." />
          <ChangeList runId={runId} title="Off the queue" items={ch.dropped} empty="Every earlier lead is still queued." />
        </div>
      )}
    </Card>
  )
}

function Carried({ runId, rec, onDone }: { runId: string; rec: RunRecord; onDone: () => void }) {
  const [analyst] = useAnalystName()
  const { data, reload } = useAsync(() => api.vendors(runId, { queue: 'any', disposition: 'carried', limit: '500' }), [runId, rec.queue.carried])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!rec.follows || !data || data.total === 0) return null
  const keep = async (ueis: string[]) => {
    setBusy(true)
    setError(null)
    try {
      await api.confirmCarried(runId, { ueis, analyst })
      reload()
      onDone()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Card
      title={`Decisions carried from ${rec.follows.label} (${num(data.total)})`}
      action={
        <Button variant="secondary" disabled={busy || !analyst.trim()} onClick={() => keep(data.rows.map((r) => r.uei))}>
          Keep all {num(data.rows.length)} in this import
        </Button>
      }
    >
      <p className="mb-3 text-sm text-slate-600">
        These were decided in the earlier import. They show here so nothing is lost, but they don't count as decided in this import until someone keeps them or
        decides again. Check the changes above first.
      </p>
      <ErrorNote error={error} />
      <ul className="divide-y divide-slate-100 text-sm">
        {data.rows.map((r) => (
          <li key={r.uei} className="flex flex-wrap items-center gap-3 py-2">
            <Link to={`/runs/${runId}/vendors/${r.uei}`} className="min-w-0 flex-1 truncate font-medium text-navy hover:underline">
              {r.name}
            </Link>
            <span className="text-slate-700">{r.disposition?.value}</span>
            <span className="text-xs text-slate-500">
              {r.disposition?.analyst}, {day(r.disposition?.at ?? '')}
            </span>
            <Button variant="ghost" disabled={busy || !analyst.trim()} onClick={() => keep([r.uei])}>
              Keep
            </Button>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function Log({ rec, runId }: { rec: RunRecord; runId: string }) {
  const [q, setQ] = useState('')
  const rows = rec.log.filter((h) => !q || `${h.analyst} ${h.action} ${h.detail} ${h.uei ?? ''}`.toLowerCase().includes(q.toLowerCase()))
  return (
    <Card
      title={`Import log (${num(rec.log.length)})`}
      action={<input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter" className="w-48 rounded border border-slate-300 px-2 py-1 text-sm" />}
    >
      <p className="mb-3 text-sm text-slate-600">Every action taken in this import. It also goes out with the Vendors of Interest workbook, on its Import Log sheet.</p>
      <div className="max-h-[32rem] overflow-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-white text-left text-xs text-slate-500">
            <tr>
              <th className="pb-2 font-medium">When</th>
              <th className="pb-2 font-medium">Who</th>
              <th className="pb-2 font-medium">What</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((h, i) => (
              <tr key={i} className="align-top">
                <td className="whitespace-nowrap py-1.5 pr-3 text-xs text-slate-500">{when(h.at)}</td>
                <td className="whitespace-nowrap py-1.5 pr-3">{h.analyst}</td>
                <td className="py-1.5">
                  <span className="mr-1 text-xs font-medium uppercase tracking-wide text-slate-500">{h.action.replace(/_/g, ' ')}</span>
                  {h.uei && (
                    <Link to={`/runs/${runId}/vendors/${h.uei}`} className="mr-1 font-mono text-xs text-navy hover:underline">
                      {h.uei}
                    </Link>
                  )}
                  {h.detail}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

export default function RunRecordPage() {
  const { id = '' } = useParams()
  const { data: rec, error, reload } = useAsync(() => api.record(id), [id])
  usePlace(rec ? `${rec.meta.label} (import record)` : null)
  if (error) return <ErrorNote error={error} />
  if (!rec) return <Loading />
  const m = rec.meta
  const q = rec.queue
  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Imports', to: '/' }, { label: m.label, to: `/runs/${id}` }, { label: 'Import record' }]} />
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="break-all text-2xl font-semibold text-navy">Import record: {m.label}</h1>
            <DataClassBadge dataClass={m.data_class} />
          </div>
          <p className="mt-1 text-sm text-slate-600">
            Import {m.id} · started {when(m.created_at)} by {m.created_by}
          </p>
        </div>
        <div className="flex flex-wrap gap-2 print:hidden">
          <Button variant="ghost" onClick={() => window.print()}>
            Print
          </Button>
          <FollowUpButton runId={id} />
        </div>
      </div>

      <Card title="Where this import stands">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
          <Stat label="Leads in the queue" value={num(q.total)} sub={money(q.dollars)} />
          <Stat label="Decided in this import" value={num(q.decided)} />
          <Stat label="Carried from earlier import" value={num(q.carried)} sub={q.carried ? 'not yet kept here' : undefined} />
          <Stat label="Still open" value={num(q.open)} />
          <Stat label="Actions logged" value={num(rec.log.length)} />
        </div>
        {Object.keys(q.by_value).length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2 text-xs">
            {Object.entries(q.by_value).map(([k, n]) => (
              <Link
                key={k}
                to={`/runs/${id}/queue?queue=any&disposition=${encodeURIComponent(k)}`}
                className="rounded bg-slate-100 px-2 py-1 text-slate-700 hover:bg-slate-200"
              >
                {k}: {num(n)}
              </Link>
            ))}
          </div>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="What was screened (frozen with the run)">
          <table className="w-full text-sm">
            <tbody className="divide-y divide-slate-100">
              {rec.inputs.map((x) => (
                <tr key={x.role} className="align-top">
                  <td className="py-2 pr-3 text-slate-500">{x.role}</td>
                  <td className="py-2">
                    {x.file}
                    {x.as_of && <span className="text-slate-600"> · as of {x.as_of}</span>}
                    {x.sha256 && (
                      <div className="truncate font-mono text-xs text-slate-500" title={x.sha256}>
                        SHA-256 {x.sha256}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
              <tr>
                <td className="py-2 pr-3 text-slate-500">Policy</td>
                <td className="py-2">
                  <Link to={`/policies/${rec.policy.pack_id}`} className="text-navy hover:underline">
                    {rec.policy.pack_name} v{rec.policy.version}
                  </Link>{' '}
                  <span className="text-xs text-slate-500">rule set {rec.rule_set.version}</span>{' '}
                  <span className="font-mono text-xs text-slate-500">{rec.rule_set.fingerprint}</span>
                </td>
              </tr>
              <tr>
                <td className="py-2 pr-3 text-slate-500">Software</td>
                <td className="py-2">
                  Pipeline {rec.pipeline_version}
                  {rec.app_version && <span className="font-mono text-xs text-slate-500"> · build {rec.app_version}</span>}
                </td>
              </tr>
            </tbody>
          </table>
          <p className="mt-3 text-xs text-slate-500">An import's files and results never change. Reopening it later shows what the analyst saw at the time.</p>
        </Card>
        <Card title="Related imports">
          <ul className="space-y-2 text-sm">
            {rec.follows ? (
              <li>
                Follows{' '}
                <Link to={`/runs/${rec.follows.id}/record`} className="font-medium text-navy hover:underline">
                  {rec.follows.label}
                </Link>{' '}
                of {day(rec.follows.created_at)}. Its decisions show here as carried until kept or redone.
              </li>
            ) : (
              <li className="text-slate-600">This is the first import of this list.</li>
            )}
            {rec.restored_from.map((r) => (
              <li key={r.id}>
                Continues{' '}
                <Link to={`/runs/${r.id}`} className="font-medium text-navy hover:underline">
                  {r.label}
                </Link>{' '}
                of {day(r.created_at)}, rerun to restore {num(rec.restored.length)} vendor(s). Its work counts as this run's.
              </li>
            ))}
            {rec.followed_by.map((r) => (
              <li key={r.id}>
                Followed by{' '}
                <Link to={`/runs/${r.id}/record`} className="font-medium text-navy hover:underline">
                  {r.label}
                </Link>{' '}
                of {day(r.created_at)}.
              </li>
            ))}
          </ul>
          {rec.changes && (
            <p className="mt-3 text-sm text-slate-600">
              Since the earlier import: {num(rec.changes.new)} new leads, {num(rec.changes.changed)} changed, {num(rec.changes.dropped)} off the queue.
            </p>
          )}
        </Card>
      </div>

      <Changes runId={id} rec={rec} />
      <Carried runId={id} rec={rec} onDone={reload} />
      <Log rec={rec} runId={id} />
    </div>
  )
}
