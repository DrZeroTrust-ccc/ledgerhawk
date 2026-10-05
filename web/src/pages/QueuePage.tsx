import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Breadcrumbs, usePlace } from '../nav'
import { api, LANE_LABEL, money, num, REASON_LABEL, type VendorRow } from '../api'
import { useAnalystName } from '../App'
import { KEYS, Progress, TriagePane } from '../Triage'
import { Button, Card, DataClassBadge, ErrorNote, FlagChip, LeadLine, Loading, QueueChip, SignalChip, TierChip, TIER_SHORT, useAsync } from '../ui'

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

function BulkAssign({ runId, selected, onDone, dispositions }: { runId: string; selected: string[]; onDone: () => void; dispositions: string[] }) {
  const [analyst] = useAnalystName()
  const [who, setWho] = useState('')
  const [value, setValue] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const go = async (assignee: string) => {
    setBusy(true)
    setError(null)
    try {
      await api.assign(runId, { ueis: selected, assignee, analyst })
      setWho('')
      onDone()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md bg-navy-50 px-3 py-2 text-sm">
      <span className="font-medium text-navy">{num(selected.length)} selected</span>
      <input
        value={who}
        onChange={(e) => setWho(e.target.value)}
        placeholder="Assign to (analyst name)"
        className="w-52 rounded-md border border-slate-300 px-2 py-1 text-sm"
      />
      <Button disabled={!who.trim() || busy || !analyst.trim()} onClick={() => go(who)}>
        Assign
      </Button>
      <Button variant="ghost" disabled={busy || !analyst.trim()} onClick={() => go('')}>
        Unassign
      </Button>
      <span className="mx-1 h-5 w-px bg-slate-300" aria-hidden />
      <select value={value} onChange={(e) => setValue(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1 text-sm">
        <option value="">Decide all as…</option>
        {dispositions.map((d) => (
          <option key={d}>{d}</option>
        ))}
      </select>
      <input
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="One note for all (required)"
        className="w-64 rounded-md border border-slate-300 px-2 py-1 text-sm"
      />
      <Button
        disabled={!value || !note.trim() || busy || !analyst.trim()}
        onClick={async () => {
          setBusy(true)
          setError(null)
          try {
            await api.bulkDisposition(runId, { ueis: selected, value, note, analyst })
            setValue('')
            setNote('')
            onDone()
          } catch (e) {
            setError((e as Error).message)
          } finally {
            setBusy(false)
          }
        }}
      >
        Decide {num(selected.length)}
      </Button>
      {!analyst.trim() && <span className="text-xs text-slate-500">Enter your name in the header first.</span>}
      <ErrorNote error={error} />
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
    }, 4000)
    return () => clearTimeout(t)
  }, [running, data?.done])
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
        <span className="text-slate-600">
          The Hawk is writing reasons… {num(data.done ?? 0)} of {num(data.total ?? 0)} leads
        </span>
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
    if (k === 'queue') next.delete('lane')
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
  const allOnPage = data ? data.rows.every((r) => selected.has(r.uei)) && data.rows.length > 0 : false

  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: 'Runs', to: '/' }, { label: run.data?.meta.label ?? id, to: `/runs/${id}` }, { label: 'Queue' }]} />
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-2xl font-semibold text-navy">Queue</h1>
        {run.data && <DataClassBadge dataClass={run.data.meta.data_class} />}
        <a
          href={`/api/runs/${id}/exports/vendors-of-interest.xlsx`}
          className="ml-auto rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-navy hover:bg-slate-50"
          title="Tiered vendors in the layout of the hand-built Vendors of Interest list"
        >
          Download Vendors of Interest (XLSX)
        </a>
        {run.data && (
          <Link to={`/runs/${id}`} className="text-sm text-navy hover:underline">
            {run.data.meta.label} · run dashboard
          </Link>
        )}
      </div>

      {progress.data && <Progress p={progress.data} />}
      <HawkReasons runId={id} onWritten={() => setVersion((n) => n + 1)} />
      <TierStrip runId={id} active={tier} onPick={(t) => set('tier', t)} version={version} />

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
          <option value="carried">Carried from an earlier run</option>
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

      {selected.size > 0 && (
        <BulkAssign
          runId={id}
          dispositions={meta.data?.dispositions ?? []}
          selected={[...selected]}
          onDone={() => {
            setSelected(new Set())
            setVersion((v) => v + 1)
          }}
        />
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
          {data && data.total === 0 && <p className="py-6 text-center text-sm text-slate-500">No vendors match these filters.</p>}
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
                              {r.disposition.carried_from ? `Carried from ${r.disposition.carried_from.created_at.slice(0, 10)} run · ` : ''}
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
