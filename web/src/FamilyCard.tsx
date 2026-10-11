import { useState } from 'react'
import { Link } from 'react-router-dom'
import { money, type FamilyMember } from './api'
import { Card } from './ui'

// One company, several UEIs: each registration's FY24 and FY25 money side by side on one scale, so money moving
// from one UEI to another, or a certified firm split across several, reads at a glance. Two series: FY24 in a neutral
// gray, FY25 in the validated slot-1 blue; a legend says which is which and the numbers sit beside the bars.
const FY24 = '#a3a19a'
const FY25 = '#2a78d6'

export default function FamilyCard({ runId, family }: { runId: string; family: FamilyMember[] }) {
  const [hover, setHover] = useState<string | null>(null)
  if (family.length < 2) return null
  const max = Math.max(...family.flatMap((m) => [m.fy24, m.fy25]), 1)
  const sum = (k: 'fy24' | 'fy25' | 'tot') => family.reduce((s, m) => s + m[k], 0)
  const certified = family.filter((m) => m.certs.length).length
  const bar = (v: number, color: string, label: string) => (
    <div className="flex items-center gap-2">
      <span
        className="h-2.5 rounded-r"
        style={{ width: `max(${v > 0 ? 2 : 0}px, ${(Math.max(v, 0) / max) * 60}%)`, background: color }}
        aria-hidden
      />
      <span className="tabular whitespace-nowrap text-[11px] text-slate-600">
        {label} {money(v)}
      </span>
    </div>
  )
  return (
    <Card title={`Same company, ${family.length} registrations`}>
      <p className="text-sm text-slate-700">
        {money(sum('tot'))} across the family in FY24–25 ({money(sum('fy24'))} in FY24, {money(sum('fy25'))} in FY25)
        {certified > 0 && `; ${certified} of the registrations hold small-business certifications`}.
      </p>
      <div className="mt-2 flex gap-4 text-xs text-slate-600" aria-hidden>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-3 rounded-sm" style={{ background: FY24 }} /> FY24
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-3 rounded-sm" style={{ background: FY25 }} /> FY25
        </span>
      </div>
      <ul className="mt-2 divide-y divide-slate-100" onMouseLeave={() => setHover(null)}>
        {family.map((m) => (
          <li
            key={m.uei}
            onMouseEnter={() => setHover(m.uei)}
            className={`grid grid-cols-1 gap-x-4 gap-y-1 py-2 text-sm sm:grid-cols-[minmax(0,15rem)_1fr] ${hover === m.uei || m.self ? 'bg-slate-50' : ''}`}
          >
            <div className="min-w-0 px-1">
              {m.self ? (
                <span className="font-medium text-ink">{m.name} (this vendor)</span>
              ) : (
                <Link to={`/runs/${runId}/vendors/${encodeURIComponent(m.uei)}`} className="font-medium text-navy hover:underline">
                  {m.name}
                </Link>
              )}
              <div className="font-mono text-xs text-slate-500">{m.uei}</div>
              <div className="text-xs text-slate-600">
                {[m.start && `SAM since ${m.start}`, m.state, m.certs.join(', '), m.queue && `in the ${m.queue} queue`].filter(Boolean).join(' · ')}
                {m.excluded && <span className="ml-1 font-medium text-crimson">· excluded</span>}
              </div>
            </div>
            <div className="space-y-1 px-1">
              {bar(m.fy24, FY24, 'FY24')}
              {bar(m.fy25, FY25, 'FY25')}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  )
}
