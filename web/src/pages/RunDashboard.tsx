import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Breadcrumbs, queueHref, usePlace } from '../nav'
import { api, queueTotal, money, num, REASON_LABEL, type FunnelStep, type RunSummary } from '../api'
import { useAnalystName } from '../App'
import { Button, Card, DataClassBadge, ErrorNote, FlagChip, Loading, Stat, useAsync } from '../ui'

const STAGE_FILTER: Record<string, string> = { '1a': '1a', '1b': '1b,1c', '1d': '1d' }

function RestoreButton({ runId, uei }: { runId: string; uei: string }) {
  const [analyst] = useAnalystName()
  const nav = useNavigate()
  const [open, setOpen] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  if (!open)
    return (
      <Button variant="ghost" onClick={() => setOpen(true)}>
        Restore to queue
      </Button>
    )
  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={async (e) => {
        e.preventDefault()
        setBusy(true)
        try {
          const { id } = await api.restore(runId, uei, { note, analyst })
          nav(`/runs/${id}`)
        } catch (err) {
          setError((err as Error).message)
          setBusy(false)
        }
      }}
    >
      <input
        autoFocus
        required
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Why restore?"
        className="w-48 rounded border border-slate-300 px-2 py-1 text-sm"
      />
      <Button type="submit" disabled={busy || !note.trim() || !analyst.trim()}>
        {busy ? 'Rerunning…' : 'Restore'}
      </Button>
      <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
        Cancel
      </Button>
      {!analyst.trim() && <span className="text-xs text-slate-500">Enter your name in the header first.</span>}
      <ErrorNote error={error} />
    </form>
  )
}

function CutTable({ runId, step }: { runId: string; step: FunnelStep }) {
  const [q, setQ] = useState('')
  const { data, error } = useAsync(() => api.vendors(runId, { cut_stage: STAGE_FILTER[step.key], q, limit: '100' }), [runId, step.key, q])
  return (
    <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-semibold">
          Cut at this stage: {num(step.cut)} vendors, {money(step.cut_dollars)}
        </h3>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name or UEI"
          className="ml-auto w-56 rounded border border-slate-300 bg-white px-2 py-1 text-sm"
        />
      </div>
      <ErrorNote error={error} />
      {!data && !error && <Loading />}
      {data && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th className="pb-2 font-medium">Vendor</th>
                <th className="pb-2 font-medium">Why it was cut</th>
                <th className="pb-2 text-right font-medium">FY24–FY25</th>
                <th />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {data.rows.map((r) => (
                <tr key={r.uei} className="align-top">
                  <td className="py-2 pr-3">
                    <Link to={`/runs/${runId}/vendors/${r.uei}`} className="font-medium text-navy hover:underline">
                      {r.name}
                    </Link>
                    <div className="font-mono text-xs text-slate-500">{r.uei}</div>
                    {r.exclusion_flags.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {r.exclusion_flags.map((f) => (
                          <FlagChip key={f} flag={f} />
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="py-2 pr-3">
                    <div className="text-xs font-semibold text-slate-700">
                      {REASON_LABEL[r.reason_code] ?? r.reason_code} <span className="font-mono font-normal text-slate-400">{r.reason_code}</span>
                    </div>
                    <div className="text-xs text-slate-600">{r.reason}</div>
                  </td>
                  <td className="tabular py-2 text-right">{money(r.tot)}</td>
                  <td className="py-2 pl-3 text-right">
                    <RestoreButton runId={runId} uei={r.uei} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.total > data.rows.length && (
            <p className="mt-2 text-xs text-slate-500">
              Showing the {data.rows.length} largest of {num(data.total)}. Search to find a specific vendor.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function Funnel({ run }: { run: RunSummary }) {
  const [open, setOpen] = useState<string | null>(null)
  const max = run.funnel[0].vendors || 1
  return (
    <Card title="Funnel" action={<span className="text-xs text-slate-500">Click a stage to see who was cut and why</span>}>
      <ol className="space-y-3">
        {run.funnel.map((s) => (
          <li key={s.key}>
            <button disabled={!s.cut} onClick={() => setOpen(open === s.key ? null : s.key)} className="group block w-full text-left disabled:cursor-default">
              <div className="flex items-baseline justify-between gap-4 text-sm">
                <span className="font-medium">{s.label}</span>
                <span className="tabular text-slate-600">
                  {num(s.vendors)} vendors · {money(s.dollars)}
                </span>
              </div>
              <div className="mt-1 h-3 w-full rounded bg-slate-100">
                <div className="h-3 rounded bg-navy group-hover:bg-ink" style={{ width: `${Math.max(1, (100 * s.vendors) / max)}%` }} />
              </div>
              {s.cut > 0 && (
                <div className="mt-1 text-xs text-crimson">
                  {num(s.cut)} cut ({money(s.cut_dollars)}) · {s.reason_code} {open === s.key ? '▾' : '▸'}
                </div>
              )}
            </button>
            {open === s.key && <CutTable runId={run.meta.id} step={s} />}
          </li>
        ))}
      </ol>
    </Card>
  )
}

function QueueTiles({ run }: { run: RunSummary }) {
  const q = run.queue_counts
  const id = run.meta.id
  const tiles: [string, number, string, string][] = [
    ['Priority', q.priority, `/runs/${id}/queue?queue=priority`, 'Two or more of S1–S4'],
    ...(run.meta.sam_source
      ? ([['Relationship screen', q.relationship ?? 0, `/runs/${id}/queue?queue=relationship`, 'Two signals, one from SAM links']] as [
          string,
          number,
          string,
          string,
        ][])
      : []),
    ['Strong single signal', q.strong, `/runs/${id}/queue?queue=strong`, 'One signal above the strong bar'],
    [
      'Exclusion-linked',
      q.exclusion,
      `/runs/${id}/queue?queue=exclusion`,
      `${num(q.directly_excluded)} excluded · ${num(q.address_or_contact_ties ?? 0)} tied by address or contact`,
    ],
    ['Watch (deferred)', q.watch, `/runs/${id}/queue?queue=watch`, 'One signal; deferred, not cleared'],
    ['Integrity lane', q.integrity_leads ?? 0, `/runs/${id}/integrity`, `Leads among ${num(q.integrity_lane)} small vendors`],
    ['Data-quality review', q.closeouts, `/runs/${id}/queue?lane=closeout`, 'Net small only from deobligations'],
  ]
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7">
      {tiles.map(([label, n, to, sub]) => (
        <Link key={label} to={to} className="rounded-lg border border-slate-200 bg-white p-4 hover:border-navy">
          <Stat label={label} value={num(n)} sub={sub} />
        </Link>
      ))}
    </div>
  )
}

function RoiPanel({ run }: { run: RunSummary }) {
  const { data } = useAsync(() => api.tierRollup(run.meta.id), [run.meta.id])
  const [rates, setRates] = useState([3, 7, 15])
  const [hrsVendor, setHrsVendor] = useState(1)
  const [hrsLead, setHrsLead] = useState(10)
  if (!data) return null
  const t = Object.fromEntries(data.tiers.map((x) => [x.tier, x]))
  const top = ['1', '2', '3'].map((k) => t[k]).filter(Boolean)
  const lookBack = top.reduce((a, x) => a + x.dollars, 0)
  const forward = top.reduce((a, x) => a + x.fy25, 0)
  const t5 = t['5']?.dollars ?? 0
  const leads = top.reduce((a, x) => a + x.vendors, 0) + (run.queue_counts.integrity_leads ?? 0)
  const fileVendors = run.funnel[0]?.vendors ?? 0
  const fullHours = fileVendors * hrsVendor
  const leadHours = leads * hrsLead
  const cut = fullHours > 0 ? 1 - leadHours / fullHours : 0
  const field = 'w-16 rounded border border-slate-300 px-1.5 py-0.5 text-right text-sm tabular'
  return (
    <Card
      title={
        <span>
          Risk and ROI <span className="ml-1 rounded bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-800">Estimate</span>
        </span>
      }
    >
      <div className="grid gap-6 md:grid-cols-3">
        <div className="space-y-2 text-sm">
          <Stat label="Tiers 1–3, FY24–FY25 (look-back)" value={money(lookBack)} />
          <Stat label="Tiers 1–3, FY25 only (forward run rate)" value={money(forward)} />
          <Stat label="Tier 5, not yet reviewed (separate)" value={money(t5)} />
        </div>
        <div className="text-sm">
          <p className="mb-2 font-medium text-navy">Loss-rate scenarios on tiers 1–3</p>
          <ul className="space-y-1.5">
            {rates.map((r, i) => (
              <li key={i} className="flex items-center gap-2">
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={0.5}
                  value={r}
                  onChange={(e) => setRates(rates.map((x, j) => (j === i ? Number(e.target.value) : x)))}
                  className={field}
                  aria-label={`Loss rate ${i + 1} percent`}
                />
                <span>%</span>
                <span className="tabular ml-auto font-medium">{money((lookBack * r) / 100)}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-slate-500">
            Tier 5 at {rates[0]}–{rates[1]}% would add {money((t5 * rates[0]) / 100)}–{money((t5 * rates[1]) / 100)}. The 3% and 7% defaults come from the range
            in GAO-24-105833, which estimates annual government-wide losses of $233B to $521B; 15% is an illustrative concentrated-risk case. Look-back dollars
            become recoveries only through investigation; forward dollars are protected by acting sooner.
          </p>
        </div>
        <div className="text-sm">
          <p className="mb-2 font-medium text-navy">Analyst time</p>
          <label className="flex items-center gap-2">
            <input type="number" min={0} step={0.25} value={hrsVendor} onChange={(e) => setHrsVendor(Number(e.target.value))} className={field} />
            hours per vendor to review the whole file
          </label>
          <label className="mt-1.5 flex items-center gap-2">
            <input type="number" min={0} step={1} value={hrsLead} onChange={(e) => setHrsLead(Number(e.target.value))} className={field} />
            hours per final lead
          </label>
          <p className="mt-3">
            {num(fileVendors)} vendors × {hrsVendor} h = <span className="tabular font-medium">{num(Math.round(fullHours))} h</span> (
            {(fullHours / 2080).toFixed(1)} staff-years)
          </p>
          <p>
            {num(leads)} leads × {hrsLead} h = <span className="tabular font-medium">{num(Math.round(leadHours))} h</span>
          </p>
          <p className="mt-1 font-medium text-navy">{(cut * 100).toFixed(2)}% less analyst time</p>
          <p className="mt-2 text-xs text-slate-500">Leads are tiers 1–3 plus integrity-lane tiers A–C. A staff-year is 2,080 hours.</p>
        </div>
      </div>
    </Card>
  )
}

function Histogram({ runId }: { runId: string }) {
  const { data } = useAsync(() => api.histogram(runId), [runId])
  if (!data) return null
  const entries = Object.entries(data.combinations).slice(0, 10)
  const max = Math.max(1, ...entries.map(([, n]) => n))
  return (
    <Card title="Signal combinations">
      {entries.length === 0 && <p className="text-sm text-slate-500">No signals in this run.</p>}
      <ul className="space-y-1.5">
        {entries.map(([k, n]) => (
          <li key={k}>
            <Link to={`/runs/${runId}/queue?signal=${k.replace(/\+/g, ',')}`} className="flex items-center gap-3 text-sm hover:text-navy">
              <span className="w-40 shrink-0 break-all font-mono text-xs">{k.replace(/\+/g, ' + ')}</span>
              <span className="h-2 flex-1 rounded bg-slate-100">
                <span className="block h-2 rounded bg-navy/70" style={{ width: `${(100 * n) / max}%` }} />
              </span>
              <span className="tabular w-12 text-right text-xs text-slate-600">{num(n)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function Inputs({ run }: { run: RunSummary }) {
  const v = run.validation
  const m = run.manifest as Record<string, string | null>
  const checks: [string, number][] = [
    ['Duplicate UEIs', v.duplicate_ueis],
    ['Missing UEIs', v.missing_ueis],
    ['Non-numeric amounts', v.non_numeric_amounts],
    ['Negative values', v.negative_values],
  ]
  return (
    <Card title="Inputs and validation">
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        <dt className="text-slate-500">Vendor file</dt>
        <dd>
          {v.file_name} · {num(v.rows)} rows · {money(v.total_dollars)}
        </dd>
        <dt className="text-slate-500">File hash (SHA-256)</dt>
        <dd className="truncate font-mono text-xs" title={v.sha256}>
          {v.sha256}
        </dd>
        <dt className="text-slate-500">Exclusions extract</dt>
        <dd>
          {m.exclusions_file ? (
            `${m.exclusions_file} · as of ${m.exclusions_extract_date} · ${num(Number(m.exclusions_active_records))} active records`
          ) : (
            <span className="text-amber-700">Not provided. The exclusion lane is empty.</span>
          )}
        </dd>
        <dt className="text-slate-500">SAM entity extract</dt>
        <dd>
          {m.sam_file ? (
            `${m.sam_file} · as of ${m.sam_extract_date} · ${num(Number(m.sam_records))} entities · ${num(run.queue_counts.sam_matched ?? 0)} vendors matched`
          ) : (
            <span className="text-amber-700">Not provided. SAM cards, the relationship screen and address or contact ties are off.</span>
          )}
        </dd>
        <dt className="text-slate-500">Rule set</dt>
        <dd>
          {m.rule_set_version} <span className="font-mono text-xs text-slate-500">{m.rule_set_fingerprint}</span>
        </dd>
      </dl>
      <div className="mt-4 flex flex-wrap gap-2">
        {checks.map(([label, n]) => (
          <span key={label} className={`rounded px-2 py-1 text-xs ${n ? 'bg-amber-50 text-amber-800' : 'bg-emerald-50 text-emerald-800'}`}>
            {label}: {num(n)}
          </span>
        ))}
      </div>
      {v.warnings.map((w) => (
        <p key={w} className="mt-2 text-xs text-amber-800">
          {w}
        </p>
      ))}
      <details className="mt-4 text-sm">
        <summary className="cursor-pointer text-slate-600">Column mapping and thresholds</summary>
        <div className="mt-2 grid gap-4 md:grid-cols-2">
          <table className="text-xs">
            <tbody>
              {Object.entries(v.column_mapping).map(([src, dst]) => (
                <tr key={src}>
                  <td className="pr-3 text-slate-500">{src}</td>
                  <td className="font-mono">{dst}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <table className="text-xs">
            <tbody>
              {Object.entries(run.manifest.thresholds).map(([k, val]) => (
                <tr key={k}>
                  <td className="pr-3 font-mono text-slate-500">{k}</td>
                  <td className="tabular">{typeof val === 'number' && val >= 1000 ? money(val) : String(val)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </Card>
  )
}

export default function RunDashboard() {
  const { id = '' } = useParams()
  const { data: run, error } = useAsync(() => api.run(id), [id])
  usePlace(run ? `${run.meta.label} (run dashboard)` : null)
  if (error) return <ErrorNote error={error} />
  if (!run) return <Loading />
  const q = run.queue_counts
  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Runs', to: '/' }, { label: run.meta.label }]} />
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="break-all text-2xl font-semibold text-navy">{run.meta.label}</h1>
            <DataClassBadge dataClass={run.meta.data_class} />
          </div>
          <p className="mt-1 text-sm text-slate-600">
            Run {run.meta.id} · started {new Date(run.meta.created_at).toLocaleString()} by {run.meta.created_by}
            {run.meta.parent_id && (
              <>
                {' '}
                · rerun of{' '}
                <Link className="text-navy underline" to={`/runs/${run.meta.parent_id}`}>
                  {run.meta.parent_id}
                </Link>{' '}
                with {run.meta.restore.length} restored
              </>
            )}
          </p>
        </div>
        <Link to={queueHref(id)}>
          <Button>Open queue ({num(queueTotal(q))})</Button>
        </Link>
      </div>
      <QueueTiles run={run} />
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Funnel run={run} />
        </div>
        <Histogram runId={id} />
      </div>
      <RoiPanel run={run} />
      <Inputs run={run} />
    </div>
  )
}
