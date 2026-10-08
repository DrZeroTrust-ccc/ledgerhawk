import { Link } from 'react-router-dom'
import { api, money, num } from '../api'
import { useAnalystName } from '../App'
import { Card, ErrorNote, LeadLine, Loading, TierChip, useAsync } from '../ui'

/** Everything assigned to you, across runs. Each lead shows in the newest run that carries it, tagged with that run. */
export default function MyCasesPage() {
  const [analyst] = useAnalystName()
  const { data, error } = useAsync(() => (analyst.trim() ? api.myCases(analyst) : Promise.resolve({ rows: [] })), [analyst])
  const rows = data?.rows ?? []
  const runs = [...new Map(rows.map((r) => [r.run.id, r.run])).values()]
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-navy">My cases</h1>
        <p className="mt-1 text-sm text-slate-600">Leads assigned to you in any import. Imports stay separate; each lead opens in its own import.</p>
      </div>
      {!analyst.trim() && <p className="text-sm text-slate-500">Enter your name in the header to see your cases.</p>}
      <ErrorNote error={error} />
      {analyst.trim() && !data && !error && <Loading />}
      {data && analyst.trim() && rows.length === 0 && <p className="text-sm text-slate-500">Nothing is assigned to {analyst} yet.</p>}
      {runs.map((run) => {
        const mine = rows.filter((r) => r.run.id === run.id)
        const open = mine.filter((r) => !r.disposition || r.disposition.carried_from).length
        return (
          <Card
            key={run.id}
            title={
              <span>
                {run.label} <span className="font-normal text-slate-500">· import of {run.created_at.slice(0, 10)}</span>
              </span>
            }
            action={
              <span className="text-xs text-slate-500">
                {num(open)} open of {num(mine.length)}
              </span>
            }
          >
            <ul className="divide-y divide-slate-100 text-sm">
              {mine.map((r) => (
                <li key={r.uei} className="flex flex-wrap items-start gap-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <Link to={`/runs/${run.id}/vendors/${encodeURIComponent(r.uei)}`} className="font-medium text-navy hover:underline">
                      {r.name}
                    </Link>
                    <LeadLine hawk={r.hawk} headline={r.headline} className="text-xs text-ink" />
                  </div>
                  <TierChip tier={r.tier} changed={!!r.tier_change} />
                  <span className="tabular w-20 text-right">{money(r.tot)}</span>
                  <span className={`w-48 text-xs ${r.disposition?.carried_from ? 'text-amber-800' : 'text-slate-600'}`}>
                    {r.disposition ? `${r.disposition.value}${r.disposition.carried_from ? ' (carried)' : ''}` : 'Not yet decided'}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        )
      })}
    </div>
  )
}
