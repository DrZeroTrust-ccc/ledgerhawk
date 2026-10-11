import { useState } from 'react'
import { money, type AwardEntity } from './api'

// Money over time around the exclusion date: each paid contract action and each subcontract as a mark, the exclusion
// as a labelled rule. "Paid after exclusion" in one look. Validated palette (light, all-pairs): slot 1 blue for
// contract actions, slot 2 orange for subcontracts (also a different shape); actions that moved no new money sit on
// the baseline in gray.
const C = { action: '#2a78d6', sub: '#eb6834', none: '#9a9890', grid: '#e6e5e0', ink: '#141B22', muted: '#6b6a65', surface: '#ffffff' }
const W = 640
const H = 190
const M = { top: 14, right: 16, bottom: 26, left: 56 }

type Pt = { t: number; amount: number; kind: 'action' | 'sub' | 'none'; label: string; date: string; id: string }

const day = (s: string) => Date.parse(`${s}T12:00:00Z`)
const fmtDate = (t: number) => new Date(t).toLocaleDateString([], { month: 'short', year: 'numeric', timeZone: 'UTC' })

function niceMax(v: number) {
  if (v <= 0) return 1
  const p = 10 ** Math.floor(Math.log10(v))
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((x) => x >= v) ?? v
}

export default function ExclusionTimeline({ e }: { e: AwardEntity }) {
  const [hover, setHover] = useState<Pt | null>(null)
  const since = e.excluded_since
  if (!since) return null
  const pts: Pt[] = []
  for (const a of e.actions ?? []) {
    if (!a.flagged) continue
    const paid = a.amount > 0 && a.date > since
    pts.push({
      t: day(a.date),
      amount: paid ? a.amount : 0,
      kind: paid ? 'action' : 'none',
      label: `${a.label} · ${a.award_id}${a.mod ? ` ${a.mod}` : ''}${paid ? '' : a.date === since ? ' · on the exclusion date' : ' · no new money'}`,
      date: a.date,
      id: `a${a.award_id}${a.mod}${a.date}`,
    })
  }
  for (const s of e.subawards ?? []) {
    if (!s.date) continue
    pts.push({
      t: day(s.date),
      amount: Math.max(s.amount, 0),
      kind: 'sub',
      label: `Subcontract under ${s.prime || 'a prime'} · ${s.prime_award_id}`,
      date: s.date,
      id: `s${s.id}${s.date}`,
    })
  }
  if (!pts.length) return null
  const ex = day(since)
  const today = Date.now()
  const t0 = Math.min(ex - 365 * 864e5, ...pts.map((p) => p.t))
  const t1 = Math.max(today, ...pts.map((p) => p.t))
  const ymax = niceMax(Math.max(...pts.map((p) => p.amount)))
  const x = (t: number) => M.left + ((t - t0) / (t1 - t0)) * (W - M.left - M.right)
  const y = (v: number) => H - M.bottom - (v / ymax) * (H - M.top - M.bottom)
  const yTicks = [0, ymax / 2, ymax]
  const years: number[] = []
  for (let yr = new Date(t0).getUTCFullYear() + 1; Date.UTC(yr, 0, 1) <= t1; yr++) years.push(Date.UTC(yr, 0, 1))
  const kinds = new Set(pts.map((p) => p.kind))
  const after = pts.filter((p) => p.t > ex && p.amount > 0)
  return (
    <figure className="mt-2">
      <figcaption className="text-xs text-slate-600">
        <span className="font-medium text-ink">Money around the exclusion of {since}</span>
        {after.length
          ? ` · ${after.length} payment${after.length === 1 ? '' : 's'} after it (${money(after.reduce((s, p) => s + p.amount, 0))})`
          : ' · nothing paid after it'}
      </figcaption>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600" aria-hidden>
        {kinds.has('action') && (
          <span className="flex items-center gap-1.5">
            <svg width="10" height="10">
              <circle cx="5" cy="5" r="4" fill={C.action} />
            </svg>
            Contract action with new money
          </span>
        )}
        {kinds.has('sub') && (
          <span className="flex items-center gap-1.5">
            <svg width="10" height="10">
              <rect x="1.5" y="1.5" width="7" height="7" transform="rotate(45 5 5)" fill={C.sub} />
            </svg>
            Subcontract under another firm
          </span>
        )}
        {kinds.has('none') && (
          <span className="flex items-center gap-1.5">
            <svg width="10" height="10">
              <circle cx="5" cy="5" r="4" fill={C.none} />
            </svg>
            Same day or no new money
          </span>
        )}
      </div>
      <div className="relative">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="mt-1 w-full max-w-[640px]"
          role="img"
          aria-label={`Payments to ${e.name} around its exclusion on ${since}: ${after.length} after it`}
          onMouseLeave={() => setHover(null)}
        >
          {yTicks.map((v) => (
            <g key={v}>
              <line x1={M.left} x2={W - M.right} y1={y(v)} y2={y(v)} stroke={C.grid} strokeWidth={1} />
              <text x={M.left - 6} y={y(v) + 3} textAnchor="end" fontSize="10" fill={C.muted}>
                {money(v)}
              </text>
            </g>
          ))}
          {years.map((t) => (
            <text key={t} x={x(t)} y={H - 8} textAnchor="middle" fontSize="10" fill={C.muted}>
              {new Date(t).getUTCFullYear()}
            </text>
          ))}
          <rect x={x(ex)} y={M.top} width={Math.max(0, W - M.right - x(ex))} height={H - M.top - M.bottom} fill="#B0263A" opacity={0.05} />
          <line x1={x(ex)} x2={x(ex)} y1={M.top - 4} y2={H - M.bottom} stroke={C.ink} strokeWidth={1.5} />
          <text x={x(ex) + 4} y={M.top + 6} fontSize="10" fill={C.ink}>
            Excluded {fmtDate(ex)}
          </text>
          {pts.map((p) => {
            const cx = x(p.t)
            const cy = y(p.amount)
            const fill = C[p.kind]
            return (
              <g key={p.id} onMouseEnter={() => setHover(p)} onFocus={() => setHover(p)} tabIndex={0} className="cursor-default outline-none">
                <circle cx={cx} cy={cy} r={10} fill="transparent" />
                {p.kind === 'sub' ? (
                  <rect
                    x={cx - 4.5}
                    y={cy - 4.5}
                    width={9}
                    height={9}
                    transform={`rotate(45 ${cx} ${cy})`}
                    fill={fill}
                    stroke={C.surface}
                    strokeWidth={2}
                  />
                ) : (
                  <circle cx={cx} cy={cy} r={hover?.id === p.id ? 6 : 4.5} fill={fill} stroke={C.surface} strokeWidth={2} />
                )}
              </g>
            )
          })}
        </svg>
        {hover && (
          <div
            className="pointer-events-none absolute z-10 w-64 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs shadow-md"
            style={{ left: `min(calc(${(x(hover.t) / W) * 100}% + 8px), calc(100% - 16rem))`, top: 0 }}
          >
            <div className="tabular font-medium text-ink">
              {hover.date} · {hover.kind === 'none' ? 'no new money' : money(hover.amount)}
            </div>
            <div className="text-slate-600">{hover.label}</div>
          </div>
        )}
      </div>
    </figure>
  )
}
