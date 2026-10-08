import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Breadcrumbs, usePlace } from '../nav'
import { api, LANE_LABEL, money, num, REASON_LABEL, type DecisionImport, type VendorRow } from '../api'
import { useAnalystName } from '../App'
import { KEYS, Progress, TriagePane } from '../Triage'
import { Button, Card, DataClassBadge, DownloadMenu, ErrorNote, FlagChip, LeadLine, Loading, QueueChip, SignalChip, TierChip, TIER_SHORT, useAsync } from '../ui'

const TABS: [string, string][] = [
  ['any', 'All in queue'],
  ['priority', 'Priority'],
  ['relationship', 'Relationship screen'],
  ['strong', 'Strong single signal'],
  ['exclusion', 'Exclusion-linked'],
  ['integrity', 'Integrity lane'],
  ['watch', 'Watch (deferred)'],
]
const PAGE = 100
const NOT_YET = 'Not yet dispositioned'
// The pipeline never assigns these tiers; they fill in as analysts review tier 5 and tier 3 vendors.
const REVIEW_ONLY = new Set(['1', '2', '4', 'explained'])

function TierStrip({ runId, active, onPick, version }: { runId: string; active: string; onPick: (t: string) => void; version: number }) {
  const { data } = useAsync(() => api.tierRollup(runId), [runId, version])
  if (!data) return null
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
      {data.tiers.map((t) => (
        <button
          key={t.tier}
          onClick={() => onPick(active === t.tier ? '' : t.tier)}
          title={t.meaning}
          className={`rounded-lg border bg-white p-3 text-left hover:border-navy ${active === t.tier ? 'border-navy ring-1 ring-navy' : 'border-slate-200'}`}
        >
          <div className="text-xs font-medium text-slate-500">{TIER_SHORT[t.tier]}</div>
          <div className="tabular mt-0.5 text-lg font-semibold">{num(t.vendors)}</div>
          <div className="tabular text-xs text-slate-500">{t.vendors === 0 && REVIEW_ONLY.has(t.tier) ? 'Set by analysts at review' : money(t.dollars)}</div>
        </button>
      ))}
    </div>
  )
}

// The selection bar: one decision (tier and/or disposition, one note) for every selected lead; assigning is separate.
function BulkAssign({
  runId,
  selected,
  onDone,
  dispositions,
  tiers,
}: {
  runId: string
  selected: string[]
  onDone: (notice: string) => void
  dispositions: string[]
  tiers: Record<string, string>
}) {
  const [analyst] = useAnalystName()
  const [who, setWho] = useState('')
  const [tier, setTier] = useState('')
  const [value, setValue] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const n = num(selected.length)
  // The bar closes when the selection clears, so the confirmation is shown by the page, not here.
  const run = async (work: () => Promise<string>) => {
    setBusy(true)
    setError(null)
    try {
      onDone(await work())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const decide = () =>
    run(async () => {
      if (tier) await api.bulkTier(runId, { ueis: selected, tier, reason: note, analyst })
      if (value) await api.bulkDisposition(runId, { ueis: selected, value, note, analyst })
      const what = [tier && tiers[tier], value].filter(Boolean).join(' and ')
      setTier('')
      setValue('')
      setNote('')
      return `Recorded ${what} for ${n} ${selected.length === 1 ? 'lead' : 'leads'}.`
    })
  const assign = (assignee: string) =>
    run(async () => {
      await api.assign(runId, { ueis: selected, assignee, analyst })
      setWho('')
      return assignee ? `Assigned ${n} to ${assignee}.` : `Unassigned ${n}.`
    })
  return (
    <div className="space-y-2 rounded-md bg-navy-50 px-3 py-3 text-sm" aria-label="Selected leads">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-navy">Decide {n} selected:</span>
        <select value={tier} onChange={(e) => setTier(e.target.value)} aria-label="Tier" className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm">
          <option value="">Tier unchanged</option>
          {Object.entries(tiers).map(([k, label]) => (
            <option key={k} value={k}>
              {label}
            </option>
          ))}
        </select>
        <select value={value} onChange={(e) => setValue(e.target.value)} aria-label="Disposition" className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm">
          <option value="">Disposition unchanged</option>
          {dispositions.map((d) => (
            <option key={d}>{d}</option>
          ))}
        </select>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="One note for all (required)"
          aria-label="Note"
          className="min-w-[16rem] flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
        />
        <Button disabled={(!tier && !value) || !note.trim() || busy || !analyst.trim()} onClick={decide}>
          Apply to {n}
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-slate-600">Or assign them:</span>
        <input
          value={who}
          onChange={(e) => setWho(e.target.value)}
          placeholder="Analyst name"
          aria-label="Assign to"
          className="w-48 rounded-md border border-slate-300 px-2 py-1 text-sm"
        />
        <Button variant="secondary" disabled={!who.trim() || busy || !analyst.trim()} onClick={() => assign(who)}>
          Assign
        </Button>
        <Button variant="ghost" disabled={busy || !analyst.trim()} onClick={() => assign('')}>
          Unassign
        </Button>
        {!analyst.trim() && <span className="text-xs text-slate-500">Enter your name in the header first.</span>}
      </div>
      <ErrorNote error={error} />
    </div>
  )
}

// Tiers and dispositions from a Vendors of Interest workbook: preview what would change, then apply.
function ImportDecisions({ runId, onClose, onApplied }: { runId: string; onClose: () => void; onApplied: () => void }) {
  const [analyst] = useAnalystName()
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<DecisionImport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const send = async (apply: boolean) => {
    if (!file) return
    setBusy(true)
    setError(null)
    const f = new FormData()
    f.append('file', file)
    f.append('analyst', analyst)
    f.append('apply', String(apply))
    try {
      const r = await api.importDecisions(runId, f)
      setPreview(r)
      if (r.applied) onApplied()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const tiers = preview?.changes.filter((c) => c.tier_to).length ?? 0
  const disps = preview?.changes.filter((c) => c.disposition_to).length ?? 0
  return (
    <Card title="Import decisions from a workbook" action={<button onClick={onClose} className="text-xs text-navy hover:underline">Close</button>}>
      <div className="space-y-3 text-sm">
        <p className="text-slate-600">
          Reads the Tier and Analyst Disposition columns of a Vendors of Interest workbook, matched by UEI. Category, Routes To and Recommended Next Step become the
          note. You see what would change before anything is recorded.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="file"
            accept=".xlsx,.xls,.csv"
            aria-label="Workbook"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null)
              setPreview(null)
            }}
            className="text-sm"
          />
          <Button variant="secondary" disabled={!file || busy || !analyst.trim()} onClick={() => send(false)}>
            {busy && !preview ? 'Reading…' : 'Preview'}
          </Button>
          {!analyst.trim() && <span className="text-xs text-slate-500">Enter your name in the header first.</span>}
        </div>
        <ErrorNote error={error} />
        {preview && (
          <div className="space-y-2">
            <p role="status" className={preview.applied ? 'font-medium text-emerald-800' : 'text-ink'}>
              {preview.applied ? 'Recorded: ' : 'Would change: '}
              {num(preview.changes.length)} {preview.changes.length === 1 ? 'vendor' : 'vendors'} ({num(tiers)} tiers, {num(disps)} dispositions) ·{' '}
              {num(preview.unchanged)} already match · {num(preview.unmatched.length)} not in this import
            </p>
            {preview.changes.length > 0 && (
              <div className="max-h-64 overflow-auto rounded-md ring-1 ring-slate-200">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-slate-50 text-left text-slate-500">
                    <tr>
                      <th className="px-2 py-1.5 font-medium">Vendor</th>
                      <th className="px-2 py-1.5 font-medium">Tier</th>
                      <th className="px-2 py-1.5 font-medium">Disposition</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.changes.map((c) => (
                      <tr key={c.uei} className="border-t border-slate-100">
                        <td className="px-2 py-1.5">
                          {c.name} <span className="font-mono text-slate-400">{c.uei}</span>
                        </td>
                        <td className="px-2 py-1.5">{c.tier_to ? `${TIER_SHORT[c.tier_from] ?? 'none'} → ${TIER_SHORT[c.tier_to] ?? c.tier_to}` : 'unchanged'}</td>
                        <td className="px-2 py-1.5">{c.disposition_to ? `${c.disposition_from || 'none'} → ${c.disposition_to}` : 'unchanged'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {preview.problems.length > 0 && (
              <details className="text-xs text-slate-600">
                <summary className="cursor-pointer">{num(preview.problems.length)} rows skipped or merged</summary>
                <ul className="mt-1 list-disc pl-5">
                  {preview.problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              </details>
            )}
            {!preview.applied && preview.changes.length > 0 && (
              <Button disabled={busy} onClick={() => send(true)}>
                {busy ? 'Recording…' : `Apply ${num(preview.changes.length)} changes`}
              </Button>
            )}
          </div>
        )}
      </div>
    </Card>
  )
}

function timeLeft(secs: number) {
  if (secs < 45) return 'less than a minute left'
  const m = Math.round(secs / 60)
  return m < 60 ? `about ${m} minute${m === 1 ? '' : 's'} left` : `about ${Math.floor(m / 60)} h ${m % 60} min left`
}

function HawkProgress({ done, total, elapsed }: { done: number; total: number; elapsed: number }) {
  const pct = total ? Math.min(100, (done / total) * 100) : 0
  // estimate from the pace so far; the first batches take a few seconds to come back
  const eta = done > 0 ? timeLeft((elapsed / done) * (total - done)) : 'working out how long this will take'
  return (
    <div className="w-full max-w-xl" role="status" aria-live="polite">
      <div className="flex items-baseline justify-between text-xs text-slate-600">
        <span className="font-medium text-ink">The Hawk is writing reasons</span>
        <span className="tabular">
          {num(done)} of {num(total)} leads · {eta}
        </span>
      </div>
      <div
        className="mt-1 h-2 overflow-hidden rounded-full bg-slate-200"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
      >
        <div className={`h-full rounded-full bg-violet-600 transition-all duration-700 ${done === 0 ? 'animate-pulse w-1/12' : ''}`} style={done ? { width: `${Math.max(pct, 2)}%` } : undefined} />
      </div>
      <div className="mt-1 text-xs text-slate-500">You can keep working; reasons appear on the rows as each batch finishes.</div>
    </div>
  )
}

function HawkReasons({ runId, onWritten }: { runId: string; onWritten: () => void }) {
  const [analyst] = useAnalystName()
  const [version, setVersion] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const { data } = useAsync(() => api.hawkReasons(runId), [runId, version])
  const running = data?.state === 'running'
  useEffect(() => {
    if (!running) return
    const t = setTimeout(() => {
      setVersion((n) => n + 1)
      onWritten()
    }, 2500)
    return () => clearTimeout(t)
  }, [running, data])
  const prev = useRef(data?.state)
  useEffect(() => {
    if (prev.current === 'running' && data && data.state !== 'running') onWritten()
    prev.current = data?.state
  }, [data?.state])
  if (!data || !data.enabled) return null
  const start = async () => {
    setError(null)
    const f = new FormData()
    f.set('analyst', analyst)
    try {
      await api.startHawkReasons(runId, f)
      setVersion((n) => n + 1)
    } catch (e) {
      setError((e as Error).message)
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      {running ? (
        <HawkProgress done={data.done ?? 0} total={data.total ?? 0} elapsed={data.elapsed_s} />
      ) : (
        <>
          <Button variant="secondary" disabled={!analyst.trim()} onClick={start} title={analyst.trim() ? undefined : 'Enter your name in the header first'}>
            {data.written ? 'Have the Hawk fill in missing reasons' : 'Have the Hawk write a reason for each lead'}
          </Button>
          {data.written > 0 && <span className="text-xs text-slate-500">{num(data.written)} reasons written by the Hawk (AI). Check them against the case before relying on them.</span>}
        </>
      )}
      {data.state === 'error' && data.error && <span className="text-xs text-crimson">{data.error}</span>}
      <ErrorNote error={error} />
    </div>
  )
}

function Board({ runId, rows, dispositions }: { runId: string; rows: VendorRow[]; dispositions: string[] }) {
  const cols = [NOT_YET, ...dispositions]
  return (
    <div className="grid gap-3 overflow-x-auto pb-2 md:grid-cols-3 xl:grid-cols-6">
      {cols.map((c) => {
        const items = rows.filter((r) => (r.disposition?.value ?? NOT_YET) === c)
        return (
          <div key={c} className="min-w-[14rem] rounded-lg bg-slate-100 p-2">
            <div className="flex items-baseline justify-between px-1 pb-2">
              <h3 className="text-xs font-semibold text-slate-600">{c}</h3>
              <span className="tabular text-xs text-slate-500">{num(items.length)}</span>
            </div>
            <div className="space-y-2">
              {items.map((r) => (
                <Link
                  key={r.uei}
                  to={`/runs/${runId}/vendors/${encodeURIComponent(r.uei)}`}
                  className="block rounded-md bg-white p-2.5 text-sm shadow-sm hover:ring-1 hover:ring-navy"
                >
                  <div className="font-medium text-navy">{r.name}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    <TierChip tier={r.tier} changed={!!r.tier_change} />
                    <span className="tabular text-xs text-slate-600">{money(r.tot)}</span>
                  </div>
                  {r.assignee && <div className="mt-1 text-xs text-slate-500">Assigned to {r.assignee}</div>}
                </Link>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

const FILTER_KEYS = ['tier', 'signal', 'disposition', 'owner', 'assignee', 'q'] as const

// An empty list under a tab that says it has vendors: say which filters hid them, and clear them in one click.
function HiddenByFilters({
  runId,
  base,
  active,
  onClear,
}: {
  runId: string
  base: Record<string, string>
  active: string[]
  onClear: () => void
}) {
  const { data } = useAsync(() => api.vendors(runId, { ...base, limit: '1' }), [runId, JSON.stringify(base)])
  if (!data) return <p className="py-6 text-center text-sm text-slate-500">No vendors match these filters.</p>
  return (
    <div className="py-6 text-center text-sm text-slate-600">
      {data.total > 0 ? (
        <>
          <p>
            {num(data.total)} {data.total === 1 ? 'vendor in this tab is' : 'vendors in this tab are'} hidden by your{' '}
            {active.length === 1 ? 'filter' : 'filters'}: <span className="font-medium text-ink">{active.join(' · ')}</span>
          </p>
          <button onClick={onClear} className="mt-2 rounded-md bg-navy px-3 py-1.5 text-sm font-medium text-white hover:bg-ink">
            Clear {active.length === 1 ? 'the filter' : 'filters'} and show {data.total === 1 ? 'it' : `all ${num(data.total)}`}
          </button>
        </>
      ) : (
        <p>No vendors in this tab, even without the filters.</p>
      )}
    </div>
  )
}

export default function QueuePage() {
  const { id = '' } = useParams()
  const [sp, setSp] = useSearchParams()
  const [limit, setLimit] = useState(PAGE)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [version, setVersion] = useState(0)
  const [focus, setFocus] = useState<string | null>(null)
  const [pick, setPick] = useState<{ n: number; at: number } | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const nav = useNavigate()
  const [analystName] = useAnalystName()
  const lane = sp.get('lane') ?? ''
  const queue = sp.get('queue') ?? (lane ? '' : 'any')
  const tierView = queue === '' && !lane && !!sp.get('tier')
  const signal = sp.get('signal') ?? ''
  const disposition = sp.get('disposition') ?? ''
  const tier = sp.get('tier') ?? ''
  const owner = sp.get('owner') ?? ''
  const assignee = sp.get('assignee') ?? ''
  const view = sp.get('view') ?? 'list'
  const q = sp.get('q') ?? ''

  const params: Record<string, string> = { limit: String(view === 'board' ? 500 : limit) }
  if (queue === 'watch') params.bucket = 'watch'
  else if (queue) params.queue = queue
  if (lane) params.lane = lane
  if (signal) params.signal = signal
  if (disposition) params.disposition = disposition
  if (tier) params.tier = tier
  if (owner) params.owner = owner
  if (assignee) params.assignee = assignee
  if (q) params.q = q

  const tabOnly: Record<string, string> = {}
  for (const k of ['bucket', 'queue', 'lane']) if (params[k]) tabOnly[k] = params[k]
  const run = useAsync(() => api.run(id), [id])
  usePlace(run.data ? `Queue (${run.data.meta.label})` : null)
  const meta = useAsync(() => api.meta(), [])
  const { data, error } = useAsync(() => api.vendors(id, params), [id, JSON.stringify(params), version])
  const progress = useAsync(() => api.progress(id, analystName), [id, version, analystName])
  const rows = data?.rows ?? []
  const at = focus ? rows.findIndex((r) => r.uei === focus) : -1
  const move = (d: number) => {
    if (!rows.length) return
    const i = at < 0 ? 0 : Math.min(rows.length - 1, Math.max(0, at + d))
    setFocus(rows[i].uei)
    document.getElementById(`row-${rows[i].uei}`)?.scrollIntoView({ block: 'nearest' })
  }
  const latest = useRef({ move, rows, at, focus })
  latest.current = { move, rows, at, focus }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t.closest('input, textarea, select, [contenteditable]') || e.ctrlKey || e.metaKey || e.altKey) return
      const { move, focus } = latest.current
      if (e.key === 'j' || e.key === 'ArrowDown') move(1)
      else if (e.key === 'k' || e.key === 'ArrowUp') move(-1)
      else if (e.key === 'x' && focus) toggle(focus)
      else if (e.key === 'Enter' && focus) nav(`/runs/${id}/vendors/${encodeURIComponent(focus)}`)
      else if (e.key === 'Escape') setFocus(null)
      else if (e.key === '/') searchRef.current?.focus()
      else if (/^[1-9]$/.test(e.key) && focus) setPick({ n: Number(e.key), at: Date.now() })
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [id, nav])

  // After a decision, refresh the list and move to the next lead that is still in it.
  const decided = () => {
    const next = at >= 0 ? rows[at + 1]?.uei ?? null : null
    setVersion((v) => v + 1)
    setFocus(next)
    if (next) document.getElementById(`row-${next}`)?.scrollIntoView({ block: 'nearest' })
  }

  const set = (k: string, v: string) => {
    const next = new URLSearchParams(sp)
    if (v) next.set(k, v)
    else next.delete(k)
    if (k === 'queue') {
      next.delete('lane')
      if (tierView) next.delete('tier')
    }
    if (k === 'tier' && !v && tierView) next.delete('queue') // clearing the tier closes the tier view
    setSp(next)
    setLimit(PAGE)
    setSelected(new Set())
    setFocus(null)
  }
  // A tier tile shows exactly the vendors it counts: every vendor at that tier, in or out of a queue tab.
  const openTier = (t: string) => {
    const next = new URLSearchParams(sp)
    next.delete('lane')
    if (t) {
      next.set('tier', t)
      next.set('queue', '')
    } else {
      next.delete('tier')
      next.delete('queue')
    }
    setSp(next)
    setLimit(PAGE)
    setSelected(new Set())
    setFocus(null)
  }
  const toggle = (uei: string) =>
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(uei)) n.delete(uei)
      else n.add(uei)
      return n
    })
  const filterLabel = (k: string, v: string) => {
    if (k === 'tier') return v === 'any' ? 'Any tier set' : v === 'none' ? 'No tier' : meta.data?.tiers[v] ?? `Tier ${v}`
    if (k === 'signal') return `Signal ${meta.data?.signals[v] ? `${v} · ${meta.data.signals[v]}` : v}`
    if (k === 'disposition') return v === 'none' ? 'Not yet dispositioned' : v === 'carried' ? 'Carried from an earlier import' : v
    if (k === 'owner') return `Owner: ${v}`
    if (k === 'assignee') return `Assigned to ${v}`
    return `Search "${v}"`
  }
  const activeFilters = FILTER_KEYS.filter((k) => sp.get(k)).map((k) => filterLabel(k, sp.get(k) ?? ''))
  // Chips for the filters on top of the tab; in a tier view the tier is the view itself, shown as its tab.
  const chips = FILTER_KEYS.filter((k) => sp.get(k) && !(tierView && k === 'tier')).map((k) => ({ key: k, label: filterLabel(k, sp.get(k) ?? '') }))
  const [importing, setImporting] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const clearFilters = () => {
    const next = new URLSearchParams(sp)
    FILTER_KEYS.forEach((k) => next.delete(k))
    setSp(next)
    setLimit(PAGE)
    setSelected(new Set())
    setFocus(null)
  }
  const allOnPage = data ? data.rows.every((r) => selected.has(r.uei)) && data.rows.length > 0 : false

  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: 'Imports', to: '/' }, { label: run.data?.meta.label ?? id, to: `/runs/${id}` }, { label: 'Queue' }]} />
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-2xl font-semibold text-navy">Queue</h1>
        {run.data && <DataClassBadge dataClass={run.data.meta.data_class} />}
        <span className="ml-auto flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={() => setImporting((o) => !o)} aria-expanded={importing}>
            Import decisions from workbook
          </Button>
          <DownloadMenu
            items={[
              { label: 'Vendors of Interest (Excel)', href: `/api/runs/${id}/exports/vendors-of-interest.xlsx`, hint: 'Tiered vendors in the hand-built list layout' },
              { label: 'Small-vendor screen (Excel)', href: `/api/runs/${id}/exports/small-vendor-screen.xlsx`, hint: 'Integrity lane: vendors under $250K' },
            ]}
          />
        </span>
        {run.data && (
          <Link to={`/runs/${id}`} className="text-sm text-navy hover:underline">
            {run.data.meta.label} · import dashboard
          </Link>
        )}
      </div>

      {importing && (
        <ImportDecisions
          runId={id}
          onClose={() => setImporting(false)}
          onApplied={() => setVersion((v) => v + 1)}
        />
      )}
      {progress.data && <Progress p={progress.data} />}
      <HawkReasons runId={id} onWritten={() => setVersion((n) => n + 1)} />
      <TierStrip runId={id} active={tier} onPick={openTier} version={version} />

      <div className="flex flex-wrap gap-1 border-b border-slate-200">
        {TABS.map(([k, label]) => (
          <button
            key={k}
            onClick={() => set('queue', k)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${queue === k && !lane ? 'border-crimson text-navy' : 'border-transparent text-slate-500 hover:text-navy'}`}
          >
            {label}
            {run.data && k !== 'any' && (
              <span className="tabular ml-1.5 text-xs text-slate-400">{num(run.data.queue_counts[k === 'integrity' ? 'integrity_leads' : k] ?? 0)}</span>
            )}
          </button>
        ))}
        {lane && <span className="-mb-px border-b-2 border-crimson px-3 py-2 text-sm font-medium text-navy">{LANE_LABEL[lane] ?? lane}</span>}
        {tierView && (
          <span className="-mb-px border-b-2 border-crimson px-3 py-2 text-sm font-medium text-navy">
            {TIER_SHORT[tier] ?? `Tier ${tier}`}
            <button onClick={() => openTier('')} className="ml-2 text-xs font-normal text-slate-500 hover:text-navy" title="Back to all in queue">
              Close
            </button>
          </span>
        )}
        <span className="ml-auto flex items-center gap-1 pb-1">
          {(['list', 'board'] as const).map((v) => (
            <button
              key={v}
              onClick={() => set('view', v === 'list' ? '' : v)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium ${view === v ? 'bg-navy text-white' : 'text-slate-600 hover:bg-slate-100'}`}
            >
              {v === 'list' ? 'List' : 'Board by disposition'}
            </button>
          ))}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={searchRef}
          defaultValue={q}
          onKeyDown={(e) => e.key === 'Enter' && set('q', (e.target as HTMLInputElement).value)}
          placeholder="Search name or UEI ( / ), press Enter"
          className="w-64 rounded-md border border-slate-300 px-2.5 py-1.5 text-sm"
        />
        <select value={tier} onChange={(e) => set('tier', e.target.value)} className="rounded-md border border-slate-300 px-2 py-1.5 text-sm">
          <option value="">Any tier</option>
          <option value="any">Any tier set</option>
          <option value="none">No tier</option>
          {meta.data &&
            Object.entries(meta.data.tiers).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
        </select>
        <select value={signal} onChange={(e) => set('signal', e.target.value)} className="rounded-md border border-slate-300 px-2 py-1.5 text-sm">
          <option value="">Any signal</option>
          {meta.data &&
            Object.entries(meta.data.signals).map(([k, label]) => (
              <option key={k} value={k}>
                {k} · {label}
              </option>
            ))}
          {signal && !meta.data?.signals[signal] && <option value={signal}>{signal}</option>}
        </select>
        <select value={disposition} onChange={(e) => set('disposition', e.target.value)} className="rounded-md border border-slate-300 px-2 py-1.5 text-sm">
          <option value="">Any disposition</option>
          <option value="none">Not yet dispositioned</option>
          <option value="carried">Carried from an earlier import</option>
          {meta.data?.dispositions.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        <select value={owner} onChange={(e) => set('owner', e.target.value)} className="max-w-[16rem] rounded-md border border-slate-300 px-2 py-1.5 text-sm">
          <option value="">Any owner</option>
          {meta.data?.owners.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
          {owner && !meta.data?.owners.includes(owner) && <option value={owner}>{owner}</option>}
        </select>
        <input
          defaultValue={assignee}
          onKeyDown={(e) => e.key === 'Enter' && set('assignee', (e.target as HTMLInputElement).value)}
          placeholder="Assigned to, press Enter"
          className="w-44 rounded-md border border-slate-300 px-2.5 py-1.5 text-sm"
        />
        {data && (
          <span className="tabular ml-auto text-sm text-slate-600">
            {num(data.total)} vendors · {money(data.dollars)} under review
          </span>
        )}
      </div>

      {chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-2" aria-label="Active filters">
          <span className="text-xs font-medium text-slate-500">Filtered by</span>
          {chips.map((c) => (
            <button
              key={c.key}
              onClick={() => set(c.key, '')}
              aria-label={`Remove filter ${c.label}`}
              className="inline-flex items-center gap-1.5 rounded-full bg-navy-50 py-1 pl-3 pr-1.5 text-xs font-medium text-navy ring-1 ring-navy/30 hover:bg-navy-100"
            >
              {c.label}
              <span aria-hidden className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-navy text-[10px] text-white">
                ×
              </span>
            </button>
          ))}
          {chips.length > 1 && (
            <button onClick={clearFilters} className="text-xs text-navy underline hover:text-ink">
              Clear all
            </button>
          )}
        </div>
      )}

      {selected.size > 0 && (
        <BulkAssign
          runId={id}
          dispositions={meta.data?.dispositions ?? []}
          tiers={meta.data?.tiers ?? {}}
          selected={[...selected]}
          onDone={(msg) => {
            setSelected(new Set())
            setVersion((v) => v + 1)
            setNotice(msg)
          }}
        />
      )}
      {notice && selected.size === 0 && (
        <p role="status" className="flex items-center justify-between gap-3 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          {notice}
          <button onClick={() => setNotice(null)} className="text-xs underline">
            Dismiss
          </button>
        </p>
      )}

      {view === 'board' && data && meta.data ? (
        <>
          {data.total > data.rows.length && (
            <p className="text-xs text-slate-500">Showing the top {num(data.rows.length)} by dollars. Narrow the filters to see the rest.</p>
          )}
          <Board runId={id} rows={data.rows} dispositions={meta.data.dispositions} />
        </>
      ) : (
        <div className={focus ? 'grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_26rem]' : ''}>
        <Card>
          <ErrorNote error={error} />
          {!data && !error && <Loading />}
          {data && data.total === 0 &&
            (activeFilters.length ? (
              <HiddenByFilters key={sp.toString()} runId={id} base={tabOnly} active={activeFilters} onClear={clearFilters} />
            ) : (
              <p className="py-6 text-center text-sm text-slate-500">No vendors in this tab.</p>
            ))}
          {data && data.total > 0 && (
            <div className="-mx-5 -my-5 overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-left text-xs text-slate-500">
                  <tr>
                    <th className="w-8 py-2 pl-5">
                      <input
                        type="checkbox"
                        aria-label="Select all on this page"
                        checked={allOnPage}
                        onChange={() => setSelected(allOnPage ? new Set() : new Set(data.rows.map((r) => r.uei)))}
                      />
                    </th>
                    <th className="px-3 py-2 font-medium">Vendor</th>
                    <th className="px-3 py-2 font-medium">Queue and tier</th>
                    {!focus && <th className="px-3 py-2 font-medium">Signals</th>}
                    {!focus && <th className="px-3 py-2 text-right font-medium">FY24 → FY25</th>}
                    <th className="px-3 py-2 text-right font-medium">Total</th>
                    {!focus && <th className="px-3 py-2 font-medium">Owner</th>}
                    <th className="px-5 py-2 font-medium">Disposition</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.rows.map((r) => (
                    <tr
                      key={r.uei}
                      id={`row-${r.uei}`}
                      onClick={(e) => {
                        if (!(e.target as HTMLElement).closest('a, input, button')) setFocus(r.uei)
                      }}
                      className={`cursor-pointer align-top hover:bg-slate-50 ${
                        focus === r.uei ? 'bg-navy-50 outline outline-2 -outline-offset-2 outline-navy' : selected.has(r.uei) ? 'bg-navy-50/50' : ''
                      }`}
                    >
                      <td className="py-2.5 pl-5">
                        <input type="checkbox" aria-label={`Select ${r.name}`} checked={selected.has(r.uei)} onChange={() => toggle(r.uei)} />
                      </td>
                      <td className="px-3 py-2.5">
                        <Link to={`/runs/${id}/vendors/${r.uei}`} className="font-medium text-navy hover:underline">
                          {r.name}
                        </Link>
                        <LeadLine hawk={r.hawk} headline={r.headline} className="mt-0.5 max-w-md text-xs text-ink" />
                        <div className="font-mono text-xs text-slate-400">{r.uei}</div>
                        {r.suppression && <div className="mt-0.5 text-xs text-slate-500">Lawful pattern: {r.suppression}</div>}
                        {r.lane !== 'outlier' && r.reason_code && (
                          <div className="mt-0.5 text-xs text-slate-500">{REASON_LABEL[r.reason_code] ?? r.reason_code}</div>
                        )}
                      </td>
                      <td className="space-y-1 px-3 py-2.5">
                        <QueueChip queue={r.queue || (r.bucket === 'watch' ? 'watch' : '')} />
                        <div>
                          <TierChip tier={r.tier} changed={!!r.tier_change} />
                        </div>
                      </td>
                      {!focus && (
                        <td className="px-3 py-2.5">
                          <div className="flex max-w-xs flex-wrap gap-1">
                            {r.signals.map((s, i) => (
                              <SignalChip key={i} s={s} />
                            ))}
                            {r.exclusion_flags.map((f) => (
                              <FlagChip key={f} flag={f} />
                            ))}
                          </div>
                        </td>
                      )}
                      {!focus && (
                        <td className="tabular whitespace-nowrap px-3 py-2.5 text-right text-slate-600">
                          {money(r.fy24)} → {money(r.fy25)}
                        </td>
                      )}
                      <td className="tabular px-3 py-2.5 text-right font-medium">{money(r.tot)}</td>
                      {!focus && (
                        <td className="max-w-[14rem] px-3 py-2.5 text-xs text-slate-600">
                          {r.owner}
                          {r.assignee && <div className="mt-0.5 text-slate-500">Assigned to {r.assignee}</div>}
                        </td>
                      )}
                      <td className="px-5 py-2.5 text-xs">
                        {r.disposition ? (
                          <>
                            <div className={`font-medium ${r.disposition.carried_from ? 'text-amber-800' : ''}`}>{r.disposition.value}</div>
                            <div className="text-slate-500">
                              {r.disposition.carried_from ? `Carried from ${r.disposition.carried_from.created_at.slice(0, 10)} import · ` : ''}
                              {r.disposition.analyst} · {new Date(r.disposition.at).toLocaleDateString()}
                            </div>
                          </>
                        ) : (
                          <span className="text-slate-400">Not yet reviewed</span>
                        )}
                        {r.last_touched && <div className="mt-0.5 text-slate-400">Touched {new Date(r.last_touched).toLocaleDateString()}</div>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.total > data.rows.length && (
                <div className="border-t border-slate-100 p-3 text-center">
                  <Button variant="secondary" onClick={() => setLimit(limit + PAGE)}>
                    Show more ({num(data.total - data.rows.length)} left)
                  </Button>
                </div>
              )}
            </div>
          )}
        </Card>
        {focus && (
          <TriagePane
            runId={id}
            uei={focus}
            dispositions={meta.data?.dispositions ?? []}
            pick={pick}
            onDecided={decided}
            onClose={() => setFocus(null)}
            position={at >= 0 ? `Lead ${num(at + 1)} of ${num(data?.total ?? 0)}` : ''}
          />
        )}
        </div>
      )}
      {view !== 'board' && (
        <p className="text-xs text-slate-500">
          Click a row to triage it here. Keys:{' '}
          {KEYS.map(([k, what]) => (
            <span key={k} className="mr-3 whitespace-nowrap">
              <kbd className="rounded border border-slate-300 bg-white px-1 font-mono">{k}</kbd> {what}
            </span>
          ))}
        </p>
      )}
    </div>
  )
}
