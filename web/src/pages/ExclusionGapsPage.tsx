import { Link, useParams } from 'react-router-dom'
import { api, LANE_LABEL, money, num } from '../api'
import { Breadcrumbs, runLabel, usePlace, useRuns } from '../nav'
import { Card, ErrorNote, Loading, useAsync } from '../ui'

export default function ExclusionGapsPage() {
  const { id = '' } = useParams()
  const { data, error } = useAsync(() => api.gaps(id), [id])
  const run = useAsync(() => api.run(id), [id])
  const total = data?.reduce((n, g) => n + g.vendors.length, 0) ?? 0
  const { runs } = useRuns()
  usePlace(run.data ? `Exclusion gaps (${run.data.meta.label})` : null)
  return (
    <div className="space-y-6">
      <div>
        <Breadcrumbs items={[{ label: 'Runs', to: '/' }, { label: runLabel(runs, id), to: `/runs/${id}` }, { label: 'Exclusion gaps' }]} />
        <h1 className="mt-2 text-2xl font-semibold text-navy">Exclusion coverage gaps</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">
          Vendors that are not excluded themselves but are tied to an excluded party by a shared suite, a shared contact, an alias in the
          exclusion comments, or a supported name match. Grouped by the excluding agency, whose suspension and debarment official decides
          the scope. These are questions for that official, not violations.
        </p>
        {run.data && !run.data.meta.sam_source && (
          <p className="mt-2 text-sm text-amber-700">This run has no SAM entity extract, so suite and contact ties are not checked.</p>
        )}
      </div>
      <ErrorNote error={error} />
      {!data && !error && <Loading />}
      {data && total === 0 && (
        <Card>
          <p className="text-sm text-slate-500">No vendors in this run are tied to an excluded party.</p>
        </Card>
      )}
      {data?.map((g) => (
        <Card key={g.agency} title={`${g.agency} · ${num(g.vendors.length)} vendor${g.vendors.length === 1 ? '' : 's'}`}>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-slate-500">
                <tr>
                  <th className="pb-2 font-medium">Vendor</th>
                  <th className="pb-2 font-medium">Tie</th>
                  <th className="pb-2 font-medium">Excluded party</th>
                  <th className="pb-2 text-right font-medium">Dollars</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {g.vendors.map((v, i) => (
                  <tr key={`${v.uei}-${i}`} className="align-top">
                    <td className="py-2 pr-4">
                      <Link to={`/runs/${id}/vendors/${encodeURIComponent(v.uei)}`} className="font-medium text-navy hover:underline">
                        {v.name}
                      </Link>
                      <div className="font-mono text-xs text-slate-500">
                        {v.uei} <span className="font-sans">· {LANE_LABEL[v.lane] ?? v.lane}</span>
                      </div>
                    </td>
                    <td className="py-2 pr-4">
                      {v.tie}
                      {v.evidence && <div className="text-xs text-slate-500">{v.evidence}</div>}
                    </td>
                    <td className="py-2 pr-4">
                      {v.excluded_party}
                      {v.type && (
                        <div className="text-xs text-slate-500">
                          {v.type}
                          {v.active_date && ` · since ${v.active_date}`}
                        </div>
                      )}
                    </td>
                    <td className="tabular py-2 text-right">{money(v.tot)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ))}
    </div>
  )
}
