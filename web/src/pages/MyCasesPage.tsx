import { Link } from 'react-router-dom'
import { api, money, num } from '../api'
import { useAnalystName } from '../App'
import { Card, ErrorNote, LeadLine, Loading, TierChip, useAsync } from '../ui'

/** Everything assigned to you, across runs. Each lead shows in the newest run that carries it, tagged with that run. */
export default function MyCasesPage() {
  const [analyst] = useAnalystName()
  const { data, error } = useAsync(() => (analyst.trim() ? api.myCases(analyst) : Promise.resolve({ rows: [] })), [analyst])
  const today = data?.today ?? new Date().toISOString().slice(0, 10)
  // overdue first, then due soonest, then the rest; leads with news since you last looked rise within each
  const rows = [...(data?.rows ?? [])].sort(
    (a, b) => (a.due || '9999').localeCompare(b.due || '9999') || Number(!!b.activity?.updates) - Number(!!a.activity?.updates) || b.tot - a.tot,
  )
  const overdue = rows.filter((r) => r.due && r.due < today && (!r.disposition || r.disposition.carried_from)).length
  const runs = [...new Map(rows.map((r) => [r.run.id, r.run])).values()]
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-navy">My cases</h1>
        <p className="mt-1 text-sm text-slate-600">
          Leads assigned to you in any import, overdue first. Each says what others have done to it since you last opened it.
        </p>
        {overdue > 0 && (
          <p className="mt-2 text-sm font-medium text-crimson">
            {num(overdue)} open {overdue === 1 ? 'lead is' : 'leads are'} past due.
          </p>
        )}
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
                    {r.activity && !r.activity.last_viewed && <div className="text-xs font-medium text-navy">Not opened yet</div>}
                    {r.activity && r.activity.last_viewed && r.activity.updates > 0 && (
                      <div
                        className="text-xs font-medium text-amber-800"
                        title={`You last opened it ${new Date(r.activity.last_viewed).toLocaleString()}`}
                      >
                        {r.activity.updates} {r.activity.updates === 1 ? 'update' : 'updates'} since you last looked · {r.activity.latest}
                      </div>
                    )}
                  </div>
                  {r.due && (
                    <span
                      className={`tabular w-28 whitespace-nowrap text-xs ${r.due < today && (!r.disposition || r.disposition.carried_from) ? 'font-semibold text-crimson' : 'text-slate-600'}`}
                    >
                      due {r.due}
                    </span>
                  )}
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
