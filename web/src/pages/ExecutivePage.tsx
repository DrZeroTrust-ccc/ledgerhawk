import { Link, useParams } from 'react-router-dom'
import { api, money, num } from '../api'
import { usePlace } from '../nav'
import { ErrorNote, Loading, useAsync } from '../ui'

// The one-pager for leadership and for GSA: what was screened, what was found and for how much, the vendors to look at
// first, what changed, the data it rests on, and the effort it saved. Prints to a single page.

function Tile({ label, value, sub, tone = 'ink' }: { label: string; value: string; sub?: string; tone?: 'ink' | 'red' | 'amber' }) {
  const color = { ink: 'text-navy', red: 'text-crimson', amber: 'text-amber-700' }[tone]
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 print:border-slate-300">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`tabular mt-1 text-2xl font-semibold ${color}`}>{value}</div>
      {sub && <div className="tabular mt-0.5 text-xs text-slate-600">{sub}</div>}
    </div>
  )
}

const day = (iso?: string | null) =>
  iso ? new Date(iso.length === 10 ? `${iso}T12:00:00` : iso).toLocaleDateString([], { dateStyle: 'medium' }) : '—'

export default function ExecutivePage() {
  usePlace('Executive summary')
  const { id = '' } = useParams()
  const { data: x, error } = useAsync(() => api.executive(id), [id])
  if (error) return <ErrorNote error={error} />
  if (!x) return <Loading />
  const e = x.effort
  return (
    <div className="mx-auto max-w-5xl space-y-5 print:max-w-none print:space-y-3 print:text-[11px]">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">LedgerHawk · Executive summary</div>
          <h1 className="text-2xl font-semibold text-navy">{x.import.label}</h1>
          <p className="text-sm text-slate-600">
            Screened {day(x.import.created_at)} under {x.import.policy} v{x.import.policy_version}
            {x.import.data_class === 'synthetic' && ' · synthetic demo data'}
          </p>
        </div>
        <button
          type="button"
          onClick={() => window.print()}
          className="rounded-md bg-navy px-3 py-1.5 text-sm font-medium text-white hover:bg-ink print:hidden"
        >
          Print or save as PDF
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="Vendors screened" value={num(x.screened.vendors)} sub={`${money(x.screened.dollars)} in FY24–25 obligations`} />
        <Tile label="Red: look first" value={num(x.red.vendors)} sub={money(x.red.dollars)} tone="red" />
        <Tile label="Yellow: strong leads" value={num(x.yellow.vendors)} sub={money(x.yellow.dollars)} tone="amber" />
        <Tile
          label="Paid after exclusion"
          value={num(x.excluded.paid_after)}
          sub={`of ${num(x.excluded.vendors)} excluded vendors · ${num(x.excluded.checked)} checked in USAspending`}
          tone="red"
        />
      </div>

      <section className="rounded-lg border border-slate-200 bg-white px-5 py-4 print:break-inside-avoid">
        <h2 className="text-sm font-semibold text-navy">Look at these first</h2>
        <p className="text-xs text-slate-500">The largest red vendors, with the main reason each one is red.</p>
        <table className="mt-2 w-full text-sm print:text-[11px]">
          <tbody className="divide-y divide-slate-100">
            {x.top.map((t, i) => (
              <tr key={t.uei} className="align-top">
                <td className="tabular w-6 py-1.5 text-slate-400">{i + 1}</td>
                <td className="py-1.5 pr-3">
                  <Link to={`/runs/${id}/vendors/${t.uei}`} className="font-medium text-navy hover:underline print:no-underline">
                    {t.name}
                  </Link>
                  <div className="text-xs text-slate-600">{t.why}</div>
                </td>
                <td className="tabular whitespace-nowrap py-1.5 text-right">{money(t.tot)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="grid gap-3 md:grid-cols-2 print:grid-cols-2">
        <section className="rounded-lg border border-slate-200 bg-white px-5 py-4 print:break-inside-avoid">
          <h2 className="text-sm font-semibold text-navy">What it would take by hand</h2>
          <div className="tabular mt-1 text-2xl font-semibold text-navy">
            {num(e.low)}–{num(e.high)} analyst hours
          </div>
          <p className="text-xs text-slate-600">
            About {num(e.weeks_one_analyst)} weeks for one analyst.{' '}
            {x.machine_minutes != null
              ? `LedgerHawk took ${x.machine_minutes < 60 ? `${num(Math.round(x.machine_minutes))} minutes` : `${(x.machine_minutes / 60).toFixed(1)} hours`} of processing.`
              : ''}
          </p>
          <table className="mt-2 w-full text-xs text-slate-600">
            <tbody>
              {e.rows.map((r) => (
                <tr key={r.task}>
                  <td className="py-0.5 pr-2">{r.task}</td>
                  <td className="tabular whitespace-nowrap py-0.5 text-right">{num(r.hours)} h</td>
                  <td className="whitespace-nowrap py-0.5 pl-2 text-slate-400">{r.basis}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-1 text-[11px] text-slate-400">{e.note}</p>
        </section>

        <section className="space-y-3 rounded-lg border border-slate-200 bg-white px-5 py-4 text-sm print:break-inside-avoid">
          <div>
            <h2 className="text-sm font-semibold text-navy">Since the last import</h2>
            {x.changes ? (
              <>
                <p className="text-slate-700">
                  {num(x.changes.new)} new in the queue, {num(x.changes.dropped)} left it, {num(x.changes.changed)} changed since{' '}
                  {day(x.changes.since)}.
                </p>
                {x.changes.new_top.length > 0 && (
                  <ul className="mt-1 text-xs text-slate-600">
                    {x.changes.new_top.map((n) => (
                      <li key={n.uei}>
                        New: {n.name} ({money(n.tot)})
                      </li>
                    ))}
                  </ul>
                )}
              </>
            ) : (
              <p className="text-slate-500">This is the first import of this list.</p>
            )}
          </div>
          <div>
            <h2 className="text-sm font-semibold text-navy">Other checks</h2>
            <ul className="text-slate-700">
              <li>
                {num(x.excluded.subcontracts_after)} excluded vendor{x.excluded.subcontracts_after === 1 ? '' : 's'} paid as a subcontractor after
                exclusion
              </li>
              <li>
                {x.watchlists.checked
                  ? `${num(x.watchlists.ofac)} OFAC sanctions and ${num(x.watchlists.leie)} HHS-OIG exclusion name matches to confirm`
                  : 'OFAC and HHS-OIG lists: checked on the next import'}
              </li>
              <li>{money(x.excluded.paid_after_dollars)} paid to excluded vendors since FY24</li>
            </ul>
          </div>
          <div>
            <h2 className="text-sm font-semibold text-navy">Data</h2>
            <p className="text-xs text-slate-600">
              SAM entities {day(x.data.sam)} · SAM exclusions {day(x.data.exclusions)} · USAspending {day(x.data.usaspending)}
              {x.data.watchlists ? ` · OFAC and HHS-OIG ${day(x.data.watchlists)}` : ''}
            </p>
          </div>
        </section>
      </div>

      <p className="text-xs text-slate-500">
        Screening signals and dollars under review, not findings of fraud. Every red and yellow vendor, with its evidence, is in the analysis export.
      </p>
    </div>
  )
}
