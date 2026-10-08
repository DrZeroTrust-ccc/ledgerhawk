import { Link } from 'react-router-dom'
import { FLAG_LABEL, money, type VendorScreen } from './api'
import { Card } from './ui'

// Parts of the vendor record: the "why it's here" cards, money by fiscal year across a firm's registrations, and what
// subject screens found. Used on a run's vendor page and on a vendor that only appears on screens.

const STATUS_STYLE: Record<string, string> = {
  excluded: 'bg-crimson-50 text-crimson ring-1 ring-crimson/30',
  tied: 'bg-crimson-50 text-crimson ring-1 ring-crimson/30',
  related_excluded: 'bg-violet-50 text-violet-800 ring-1 ring-violet-200',
  name_only: 'bg-amber-50 text-amber-800 ring-1 ring-amber-200',
  signals: 'bg-amber-50 text-amber-800 ring-1 ring-amber-200',
  registration: 'bg-navy-50 text-navy ring-1 ring-navy-100',
  clear: 'bg-slate-100 text-slate-600',
}

// The newest screen that looked up awards for this vendor as a subject.
function screenAwards(screens: VendorScreen[]) {
  return screens.find((s) => s.role === 'subject' && s.awards && s.awards.by_uei.length > 0)?.awards ?? null
}

// The one fact most worth seeing first, from the screens and the run.
function strongest(screens: VendorScreen[], flags: string[]): { text: string; alarm: boolean } {
  const aw = screenAwards(screens)
  if (aw?.shift) return { text: aw.shift, alarm: true }
  if (aw?.actions_summary) return { text: aw.actions_summary, alarm: true }
  if (flags.includes('EXCLUDED')) return { text: 'On the SAM exclusions list', alarm: true }
  const tie = flags.find((f) => f.startsWith('R_EX') || f === 'JV_PARTNER_EXCLUDED')
  if (tie) return { text: FLAG_LABEL[tie] ?? tie, alarm: true }
  if (aw?.growth) return { text: aw.growth, alarm: false }
  if (aw?.anomalies[0]) return { text: aw.anomalies[0], alarm: false }
  const f = screens.find((s) => s.role === 'subject')?.findings?.[0]
  if (f) return { text: f, alarm: false }
  return { text: 'No finding beyond the import’s own signals.', alarm: false }
}

export function WhyHere({ headline, flags, screens }: { headline: string; flags: string[]; screens: VendorScreen[] }) {
  const top = strongest(screens, flags)
  const asSubject = screens.find((s) => s.role === 'subject')
  const asRelated = screens.filter((s) => s.role === 'related').length
  const card = 'flex flex-col gap-1 rounded-lg border p-4'
  return (
    <section aria-label="Why it is here" className="grid gap-3 md:grid-cols-3">
      <div className={`${card} border-slate-200 bg-white`}>
        <span className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Why it is here</span>
        <span className="text-sm leading-snug text-ink">{headline || 'Flagged by the screen; see the summary below.'}</span>
      </div>
      <div className={`${card} border-slate-200 bg-white`}>
        <span className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Subject screens</span>
        {asSubject ? (
          <>
            <span className="text-sm font-medium text-ink">
              {asSubject.status_label} · {asSubject.matter || 'Untitled matter'}
            </span>
            <span className="text-xs text-slate-500">
              {asSubject.related_total ? `${asSubject.related_total} related ${asSubject.related_total === 1 ? 'firm' : 'firms'}` : 'No related firms'}
              {asRelated ? ` · also related to ${asRelated} other ${asRelated === 1 ? 'subject' : 'subjects'}` : ''}
            </span>
          </>
        ) : asRelated ? (
          <span className="text-sm text-ink">Related to {asRelated} screened {asRelated === 1 ? 'subject' : 'subjects'}; not screened itself</span>
        ) : (
          <span className="text-sm text-slate-500">Not on any subject screen yet</span>
        )}
      </div>
      <div className={`${card} ${top.alarm ? 'border-crimson/30 bg-crimson-50' : 'border-slate-200 bg-white'}`}>
        <span className={`text-xs font-semibold tracking-wide uppercase ${top.alarm ? 'text-crimson' : 'text-slate-500'}`}>Strongest finding</span>
        <span className="text-sm leading-snug text-ink">{top.text}</span>
      </div>
    </section>
  )
}

const SHADES = ['#1F3447', '#4F6D8F', '#8EA6C3', '#7C8798', '#B8C0CC', '#5B6B7F', '#A7B4C6']

// Obligations by fiscal year, stacked by registration: one firm's money moving between UEIs shows as colour shifting.
export function MoneyByYear({ uei, screens }: { uei: string; screens: VendorScreen[] }) {
  const aw = screenAwards(screens)
  if (!aw) return null
  const ueis = [...aw.by_uei].sort((a, b) => (a.uei === uei ? -1 : b.uei === uei ? 1 : 0))
  const years = [...new Set(ueis.flatMap((e) => Object.entries(e.by_fy).filter(([, v]) => v > 0).map(([y]) => y)))].sort().slice(-6)
  if (!years.length) return null
  const last = years.at(-1)
  const isNew = (e: (typeof ueis)[number]) =>
    !!last && (e.by_fy[last] ?? 0) > 0 && years.slice(0, -1).every((y) => (e.by_fy[y] ?? 0) <= 0) && years.length > 1
  const colour = (e: (typeof ueis)[number], i: number) => (isNew(e) ? '#B4233C' : SHADES[i % SHADES.length])
  const total = (y: string) => ueis.reduce((a, e) => a + Math.max(0, e.by_fy[y] ?? 0), 0)
  const max = Math.max(...years.map(total), 1)
  const H = 180
  return (
    <Card
      title={ueis.length > 1 ? `Obligations by fiscal year, ${ueis.length} registrations` : 'Obligations by fiscal year'}
      action={<span className="text-xs text-slate-500">USAspending, looked up {aw.fetched_at.slice(0, 10)}</span>}
    >
      <div className="flex items-end gap-4 border-b border-slate-300 px-2" style={{ height: H + 28 }} role="img" aria-label="Obligations by fiscal year">
        {years.map((y) => (
          <div key={y} className="flex flex-1 flex-col items-center gap-1">
            <span className="tabular text-xs text-slate-600">{money(total(y))}</span>
            <div className="flex w-full max-w-16 flex-col-reverse">
              {ueis.map((e, i) =>
                (e.by_fy[y] ?? 0) > 0 ? (
                  <div
                    key={e.uei}
                    title={`${e.uei}: ${money(e.by_fy[y])}`}
                    style={{ height: Math.max(2, Math.round(((e.by_fy[y] ?? 0) / max) * H)), background: colour(e, i) }}
                    className="border-t border-white"
                  />
                ) : null,
              )}
            </div>
          </div>
        ))}
      </div>
      <div className="flex gap-4 px-2 pt-1">
        {years.map((y) => (
          <span key={y} className="flex-1 text-center text-xs text-slate-500">
            FY{y.slice(2)}
          </span>
        ))}
      </div>
      {ueis.length > 1 && (
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-700">
          {ueis.map((e, i) => (
            <span key={e.uei} className="inline-flex items-center gap-1.5">
              <span className="inline-block h-3 w-3 rounded-sm" style={{ background: colour(e, i) }} />
              <span className="font-mono">{e.uei}</span>
              {e.uei === uei ? ' this registration' : isNew(e) ? ' new' : ''}
            </span>
          ))}
        </div>
      )}
      {(aw.shift || aw.growth) && <p className="mt-3 text-sm text-ink">{aw.shift || aw.growth}</p>}
    </Card>
  )
}

export function ScreenEvidence({ screens }: { screens: VendorScreen[] }) {
  if (!screens.length)
    return (
      <Card title="Subject screens">
        <p className="text-sm text-slate-500">No subject screen includes this vendor yet.</p>
      </Card>
    )
  return (
    <div className="space-y-4">
      {screens.map((s) => (
        <Card
          key={`${s.id}-${s.ref}`}
          title={
            <span className="flex flex-wrap items-center gap-2">
              <Link to={`/subjects/${s.id}`} className="text-navy hover:underline">
                {s.matter || 'Untitled matter'}
              </Link>
              <span className="text-xs font-normal text-slate-500">{s.created_at.slice(0, 10)}</span>
              {s.role === 'subject' && s.status && (
                <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[s.status] ?? ''}`}>{s.status_label}</span>
              )}
              {s.role === 'related' && <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">Related to subject #{s.ref}</span>}
            </span>
          }
        >
          {s.role === 'related' ? (
            <p className="text-sm text-ink">
              Linked to subject #{s.ref}, {s.subject}, by {(s.via ?? []).join('; ')}.{s.excluded && ' This vendor is excluded.'}
            </p>
          ) : (
            <div className="grid gap-4 text-sm lg:grid-cols-2">
              <div>
                <h3 className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">What the screen found</h3>
                <ul className="list-disc space-y-1 pl-5 text-ink">
                  {(s.findings ?? []).slice(0, 6).map((f, i) => (
                    <li key={i}>{f}</li>
                  ))}
                </ul>
                {(s.next_steps ?? []).length > 0 && (
                  <>
                    <h3 className="mt-3 mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">Next steps</h3>
                    <ol className="list-decimal space-y-1 pl-5 text-ink">
                      {(s.next_steps ?? []).map((f, i) => (
                        <li key={i}>{f}</li>
                      ))}
                    </ol>
                  </>
                )}
              </div>
              <div>
                <h3 className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">
                  Related firms {s.related_total ? `(${s.related_total})` : ''}
                </h3>
                {(s.related ?? []).length === 0 ? (
                  <p className="text-slate-500">None found.</p>
                ) : (
                  <ul className="divide-y divide-slate-100">
                    {(s.related ?? []).map((r) => (
                      <li key={r.uei} className="py-1.5">
                        <Link to={`/vendors/${encodeURIComponent(r.uei)}`} className="font-medium text-navy hover:underline">
                          {r.name}
                        </Link>{' '}
                        <span className="font-mono text-xs text-slate-500">{r.uei}</span>
                        {r.excluded && <span className="ml-2 rounded bg-crimson-50 px-1.5 py-0.5 text-xs text-crimson">excluded</span>}
                        <div className="text-xs text-slate-500">{r.via.join('; ')}</div>
                      </li>
                    ))}
                  </ul>
                )}
                {s.awards?.anomalies.map((a) => (
                  <p key={a} className="mt-2 rounded bg-amber-50 px-2 py-1 text-xs text-amber-900">
                    {a}
                  </p>
                ))}
              </div>
            </div>
          )}
        </Card>
      ))}
    </div>
  )
}
