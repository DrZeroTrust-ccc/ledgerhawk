import { Link, useParams } from 'react-router-dom'
import { useState } from 'react'
import { api, money, num, type Executive } from '../api'
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

// Red and yellow dollars by reason: one series, so one hue (validated slot 1), bars capped at 16px with a rounded end,
// value at the tip in ink, details on hover. A vendor counts under each of its reasons, so the bars overlap.
function WhereTheMoneyIs({ rows }: { rows: Executive['by_reason'] }) {
  const [hover, setHover] = useState<string | null>(null)
  const max = Math.max(...rows.map((r) => r.dollars), 1)
  return (
    <section className="rounded-lg border border-slate-200 bg-white px-5 py-4 print:break-inside-avoid">
      <h2 className="text-sm font-semibold text-navy">Where the flagged money is</h2>
      <p className="text-xs text-slate-500">
        FY24–25 obligations of red and yellow vendors, by reason. A vendor with several reasons counts under each, so the bars overlap.
      </p>
      <ul className="mt-3 space-y-2" onMouseLeave={() => setHover(null)}>
        {rows.map((r) => (
          <li
            key={r.key}
            className={`grid grid-cols-[12rem_1fr] items-center gap-3 rounded text-sm sm:grid-cols-[16rem_1fr] ${hover === r.key ? 'bg-slate-50' : ''}`}
            onMouseEnter={() => setHover(r.key)}
            title={`${r.label}: ${money(r.dollars)} across ${num(r.vendors)} vendors (${num(r.red)} red, ${num(r.yellow)} yellow)`}
          >
            <span className="truncate text-slate-700">{r.label}</span>
            <span className="flex min-w-0 items-center gap-2">
              <span className="h-4 shrink-0 rounded-r" style={{ width: `max(2px, ${(r.dollars / max) * 70}%)`, background: '#2a78d6' }} aria-hidden />
              <span className="tabular whitespace-nowrap text-xs text-slate-700">
                {money(r.dollars)} · {num(r.vendors)} vendor{r.vendors === 1 ? '' : 's'}
                {hover === r.key && (
                  <span className="text-slate-500">
                    {' '}
                    ({num(r.red)} red, {num(r.yellow)} yellow)
                  </span>
                )}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </section>
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

      {x.by_reason.length > 0 && <WhereTheMoneyIs rows={x.by_reason} />}

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
              <li>
                {money(x.excluded.paid_after_dollars)} in contract actions and subcontracts dated after the exclusions (USAspending); those vendors
                received {money(x.excluded.paid_after_total)} in FY24–25 in all
              </li>
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

      <p className="text-xs text-slate-500">Every red and yellow vendor, with its evidence, is in the analysis export.</p>
    </div>
  )
}
