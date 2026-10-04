import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, LANE_LABEL, money, num, REASON_LABEL } from '../api'
import { Button, Card, DataClassBadge, ErrorNote, FlagChip, Loading, QueueChip, SignalChip, useAsync } from '../ui'

const TABS: [string, string][] = [
  ['any', 'All in queue'],
  ['priority', 'Priority'],
  ['relationship', 'Relationship screen'],
  ['strong', 'Strong single signal'],
  ['exclusion', 'Exclusion-linked'],
  ['watch', 'Watch (deferred)'],
]
const PAGE = 100

export default function QueuePage() {
  const { id = '' } = useParams()
  const [sp, setSp] = useSearchParams()
  const [limit, setLimit] = useState(PAGE)
  const lane = sp.get('lane') ?? ''
  const queue = sp.get('queue') ?? (lane ? '' : 'any')
  const signal = sp.get('signal') ?? ''
  const disposition = sp.get('disposition') ?? ''
  const q = sp.get('q') ?? ''

  const params: Record<string, string> = { limit: String(limit) }
  if (queue === 'watch') params.bucket = 'watch'
  else if (queue) params.queue = queue
  if (lane) params.lane = lane
  if (signal) params.signal = signal
  if (disposition) params.disposition = disposition
  if (q) params.q = q

  const run = useAsync(() => api.run(id), [id])
  const meta = useAsync(() => api.meta(), [])
  const { data, error } = useAsync(() => api.vendors(id, params), [id, JSON.stringify(params)])

  const set = (k: string, v: string) => {
    const next = new URLSearchParams(sp)
    if (v) next.set(k, v)
    else next.delete(k)
    if (k === 'queue') next.delete('lane')
    setSp(next)
    setLimit(PAGE)
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-2xl font-semibold text-navy">Queue</h1>
        {run.data && <DataClassBadge dataClass={run.data.meta.data_class} />}
        {run.data && (
          <Link to={`/runs/${id}`} className="ml-auto text-sm text-navy hover:underline">
            {run.data.meta.label} · run dashboard
          </Link>
        )}
      </div>

      <div className="flex flex-wrap gap-1 border-b border-slate-200">
        {TABS.map(([k, label]) => (
          <button
            key={k}
            onClick={() => set('queue', k)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${queue === k && !lane ? 'border-crimson text-navy' : 'border-transparent text-slate-500 hover:text-navy'}`}
          >
            {label}
            {run.data && k !== 'any' && <span className="tabular ml-1.5 text-xs text-slate-400">{num(run.data.queue_counts[k] ?? 0)}</span>}
          </button>
        ))}
        {lane && <span className="-mb-px border-b-2 border-crimson px-3 py-2 text-sm font-medium text-navy">{LANE_LABEL[lane] ?? lane}</span>}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <input
          defaultValue={q}
          onKeyDown={(e) => e.key === 'Enter' && set('q', (e.target as HTMLInputElement).value)}
          placeholder="Search name or UEI, press Enter"
          className="w-64 rounded-md border border-slate-300 px-2.5 py-1.5 text-sm"
        />
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
          {meta.data?.dispositions.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        {data && (
          <span className="tabular ml-auto text-sm text-slate-600">
            {num(data.total)} vendors · {money(data.dollars)} under review
          </span>
        )}
      </div>

      <Card>
        <ErrorNote error={error} />
        {!data && !error && <Loading />}
        {data && data.total === 0 && <p className="py-6 text-center text-sm text-slate-500">No vendors match these filters.</p>}
        {data && data.total > 0 && (
          <div className="-mx-5 -my-5 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs text-slate-500">
                <tr>
                  <th className="px-5 py-2 font-medium">Vendor</th>
                  <th className="px-3 py-2 font-medium">Queue</th>
                  <th className="px-3 py-2 font-medium">Signals</th>
                  <th className="px-3 py-2 text-right font-medium">FY24 → FY25</th>
                  <th className="px-3 py-2 text-right font-medium">Total</th>
                  <th className="px-5 py-2 font-medium">Disposition</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.rows.map((r) => (
                  <tr key={r.uei} className="align-top hover:bg-slate-50">
                    <td className="px-5 py-2.5">
                      <Link to={`/runs/${id}/vendors/${r.uei}`} className="font-medium text-navy hover:underline">
                        {r.name}
                      </Link>
                      <div className="font-mono text-xs text-slate-500">{r.uei}</div>
                      {r.suppression && <div className="mt-0.5 text-xs text-slate-500">Lawful pattern: {r.suppression}</div>}
                      {r.lane !== 'outlier' && r.reason_code && <div className="mt-0.5 text-xs text-slate-500">{REASON_LABEL[r.reason_code] ?? r.reason_code}</div>}
                    </td>
                    <td className="px-3 py-2.5">
                      <QueueChip queue={r.queue || (r.bucket === 'watch' ? 'watch' : '')} />
                    </td>
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
                    <td className="tabular whitespace-nowrap px-3 py-2.5 text-right text-slate-600">
                      {money(r.fy24)} → {money(r.fy25)}
                    </td>
                    <td className="tabular px-3 py-2.5 text-right font-medium">{money(r.tot)}</td>
                    <td className="px-5 py-2.5 text-xs">
                      {r.disposition ? (
                        <>
                          <div className="font-medium">{r.disposition.value}</div>
                          <div className="text-slate-500">
                            {r.disposition.analyst} · {new Date(r.disposition.at).toLocaleDateString()}
                          </div>
                        </>
                      ) : (
                        <span className="text-slate-400">Not yet reviewed</span>
                      )}
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
    </div>
  )
}
