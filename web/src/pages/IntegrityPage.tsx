import { useState } from 'react'
import { Breadcrumbs, runLabel, usePlace, useRuns } from '../nav'
import { Link, useParams } from 'react-router-dom'
import { api, money, num } from '../api'
import { Card, DataClassBadge, ErrorNote, Loading, useAsync } from '../ui'

const TIER_STYLE: Record<string, string> = {
  A: 'bg-crimson text-white',
  B: 'bg-crimson-50 text-crimson ring-1 ring-crimson/30',
  C: 'bg-amber-50 text-amber-800 ring-1 ring-amber-300',
  D: 'bg-navy-50 text-navy ring-1 ring-navy/30',
  '': 'bg-slate-100 text-slate-600',
}

function LaneChip({ tier }: { tier: string }) {
  return (
    <span className={`whitespace-nowrap rounded px-2 py-0.5 text-xs font-medium ${TIER_STYLE[tier]}`}>{tier ? `Tier ${tier}` : 'Excluded, not tiered'}</span>
  )
}

export default function IntegrityPage() {
  const { id = '' } = useParams()
  const { data, error } = useAsync(() => api.integrity(id), [id])
  const run = useAsync(() => api.run(id), [id])
  const [pick, setPick] = useState('')
  const { runs } = useRuns()
  usePlace(run.data ? `Integrity lane (${run.data.meta.label})` : null)
  const rows = data?.rows.filter((r) => (pick === 'excluded' ? r.integrity?.excluded : !pick || r.integrity?.tier === pick)) ?? []
  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Runs', to: '/' }, { label: runLabel(runs, id), to: `/runs/${id}` }, { label: 'Integrity lane' }]} />
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold text-navy">Small-vendor integrity lane</h1>
            {run.data && <DataClassBadge dataClass={run.data.meta.data_class} />}
          </div>
          <p className="mt-1 max-w-3xl text-sm text-slate-600">
            Vendors under the materiality line are screened for integrity signals only: excluded vendors still being paid, and vendors tied to an excluded
            party. The value here is in control gaps, not dollars. Timing is by fiscal year until award dates are checked.
          </p>
          {run.data && !run.data.meta.exclusions_file && (
            <p className="mt-2 text-sm text-amber-700">This run has no exclusions extract, so the lane is empty.</p>
          )}
        </div>
        <a
          href={`/api/runs/${id}/exports/small-vendor-screen.xlsx`}
          className="ml-auto rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-navy hover:bg-slate-50"
        >
          Download Small-Vendor Screen (XLSX)
        </a>
      </div>
      <ErrorNote error={error} />
      {!data && !error && <Loading />}
      {data && (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            {data.funnel.map((f) => (
              <div key={f.key} className="rounded-lg border border-slate-200 bg-white p-4">
                <div className="text-xs text-slate-500">{f.label}</div>
                <div className="tabular mt-1 text-xl font-semibold text-navy">{num(f.vendors)}</div>
                <div className="tabular text-xs text-slate-500">{money(f.dollars)}</div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {data.tiers.map((t) => (
              <button
                key={t.tier}
                onClick={() => setPick(pick === t.tier ? '' : t.tier)}
                className={`rounded-lg border bg-white p-4 text-left hover:border-navy ${pick === t.tier ? 'border-navy ring-1 ring-navy' : 'border-slate-200'}`}
              >
                <div className="text-xs text-slate-500">{t.label}</div>
                <div className="tabular mt-1 text-xl font-semibold text-navy">{num(t.vendors)}</div>
                <div className="tabular text-xs text-slate-500">
                  {money(t.dollars)}
                  {t.after_exclusion > 0 && ` · ${money(t.after_exclusion)} after exclusion`}
                </div>
                <div className="mt-1 text-xs text-slate-500">{t.meaning}</div>
              </button>
            ))}
          </div>

          <Card title="Control gaps by excluding agency">
            {data.gaps.length === 0 && <p className="text-sm text-slate-500">No excluded or exclusion-linked small vendors in this run.</p>}
            {data.gaps.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-slate-500">
                    <tr>
                      <th className="pb-2 font-medium">Agency</th>
                      <th className="px-3 pb-2 text-right font-medium">A</th>
                      <th className="px-3 pb-2 text-right font-medium">B</th>
                      <th className="px-3 pb-2 text-right font-medium">C</th>
                      <th className="px-3 pb-2 text-right font-medium">Excluded</th>
                      <th className="pb-2 pl-4 font-medium">What it suggests</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {data.gaps.map((g) => (
                      <tr key={g.agency} className="align-top">
                        <td className="py-2 pr-4 font-medium text-navy">{g.agency}</td>
                        <td className="tabular px-3 py-2 text-right">{g.A}</td>
                        <td className="tabular px-3 py-2 text-right">{g.B}</td>
                        <td className="tabular px-3 py-2 text-right">{g.C}</td>
                        <td className="tabular px-3 py-2 text-right">{g.excluded}</td>
                        <td className="py-2 pl-4 text-slate-700">{g.summary}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card
            title={pick ? (pick === 'excluded' ? 'Excluded small vendors' : `Tier ${pick}`) : 'All small vendors with an integrity signal'}
            action={
              <span className="flex gap-2 text-xs">
                <button onClick={() => setPick(pick === 'excluded' ? '' : 'excluded')} className="text-navy hover:underline">
                  {pick === 'excluded' ? 'Show all' : 'Excluded only'}
                </button>
              </span>
            }
          >
            {rows.length === 0 && <p className="text-sm text-slate-500">Nothing here.</p>}
            <ul className="divide-y divide-slate-100">
              {rows.map((r) => (
                <li key={r.uei} className="py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <LaneChip tier={r.integrity?.tier ?? ''} />
                    <Link to={`/runs/${id}/vendors/${encodeURIComponent(r.uei)}`} className="font-medium text-navy hover:underline">
                      {r.name}
                    </Link>
                    <span className="font-mono text-xs text-slate-500">{r.uei}</span>
                    <span className="tabular ml-auto text-sm">
                      {money(r.tot)}
                      {(r.integrity?.after_exclusion ?? 0) > 0 && (
                        <span className="text-crimson"> · {money(r.integrity!.after_exclusion)} after exclusion</span>
                      )}
                    </span>
                  </div>
                  <ul className="mt-1 list-disc pl-5 text-sm text-slate-700">
                    {r.integrity?.reasons.map((x) => (
                      <li key={x}>{x}</li>
                    ))}
                  </ul>
                  {!!r.integrity?.second.length && <p className="mt-1 text-xs text-slate-500">Second signals: {r.integrity.second.join('; ')}</p>}
                  <p className="mt-1 text-xs text-slate-500">
                    Routes to {r.owner || 'not set'}
                    {r.disposition ? ` · ${r.disposition.value}` : ' · Not yet dispositioned'}
                  </p>
                </li>
              ))}
            </ul>
          </Card>
        </>
      )}
    </div>
  )
}
