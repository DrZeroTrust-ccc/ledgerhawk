import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type ProgramEdge, type ProgramLead, type ProgramNode } from '../api'
import { Card, DownloadMenu, ErrorNote, Loading, Stat, useAsync } from '../ui'
import { ProgramClassBadge } from './ProgramsPage'

const NODE_COLOR: Record<string, string> = {
  sponsor: '#1F3447',
  site: '#64748b',
  retailer: '#0f766e',
  vendor: '#7c3aed',
  address: '#d97706',
  person: '#B0263A',
  exclusion: '#7f1d1d',
  court: '#4338ca',
  osint: '#0891b2',
}
const CHIP: Record<string, string> = { P_osint: 'bg-cyan-50 text-cyan-800', P_court: 'bg-indigo-50 text-indigo-700' }
const RECORD_KINDS = new Set(['exclusion', 'court', 'osint'])
const NODE_LABEL: Record<string, string> = {
  sponsor: 'Sponsor',
  site: 'Meal site',
  retailer: 'SNAP store',
  vendor: 'WIC vendor',
  address: 'Shared address',
  person: 'Officer or owner',
  exclusion: 'Exclusion or disqualification',
  court: 'Court case',
  osint: 'Open-source finding',
}

const fmt = (n: number) => n.toLocaleString()

// A small deterministic force layout: enough for one lead's one-hop network (tens of nodes).
function layout(nodes: ProgramNode[], edges: ProgramEdge[], w: number, h: number) {
  const n = nodes.length
  const idx = new Map(nodes.map((x, i) => [x.id, i]))
  const pos = nodes.map((x, i) => {
    if (x.center) return { x: w / 2, y: h / 2 }
    const a = (2 * Math.PI * i) / Math.max(n, 1)
    const r = Math.min(w, h) * (x.kind === 'site' ? 0.42 : 0.25)
    return { x: w / 2 + r * Math.cos(a), y: h / 2 + r * Math.sin(a) }
  })
  const k = Math.sqrt((w * h) / Math.max(n, 1)) * 0.55
  for (let it = 0; it < 300; it++) {
    const disp = pos.map(() => ({ x: 0, y: 0 }))
    for (let i = 0; i < n; i++)
      for (let j = i + 1; j < n; j++) {
        const dx = pos[i].x - pos[j].x
        const dy = pos[i].y - pos[j].y
        const d = Math.max(Math.hypot(dx, dy), 0.01)
        const f = (k * k) / d
        disp[i].x += (dx / d) * f
        disp[i].y += (dy / d) * f
        disp[j].x -= (dx / d) * f
        disp[j].y -= (dy / d) * f
      }
    for (const e of edges) {
      const a = idx.get(e.source)
      const b = idx.get(e.target)
      if (a === undefined || b === undefined) continue
      const dx = pos[a].x - pos[b].x
      const dy = pos[a].y - pos[b].y
      const d = Math.max(Math.hypot(dx, dy), 0.01)
      const f = (d * d) / k
      disp[a].x -= (dx / d) * f
      disp[a].y -= (dy / d) * f
      disp[b].x += (dx / d) * f
      disp[b].y += (dy / d) * f
    }
    const t = 18 * (1 - it / 300) + 1
    nodes.forEach((x, i) => {
      if (x.center) return
      const d = Math.max(Math.hypot(disp[i].x, disp[i].y), 0.01)
      pos[i].x = Math.min(w - 30, Math.max(30, pos[i].x + (disp[i].x / d) * Math.min(d, t)))
      pos[i].y = Math.min(h - 20, Math.max(20, pos[i].y + (disp[i].y / d) * Math.min(d, t)))
    })
  }
  return pos
}

function Network({ lead }: { lead: ProgramLead }) {
  const { nodes, edges } = lead.graph
  const W = 820
  const H = nodes.length > 20 ? 560 : 380
  const pos = useMemo(() => layout(nodes, edges, W, H), [nodes, edges, H])
  const idx = new Map(nodes.map((x, i) => [x.id, i]))
  const [hover, setHover] = useState<string | null>(null)
  const showLabel = (x: ProgramNode) => x.center || x.kind === 'person' || x.kind === 'address' || RECORD_KINDS.has(x.kind) || x.bad || nodes.length <= 14 || hover === x.id
  const kinds = Array.from(new Set(nodes.map((x) => x.kind)))
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-md border border-slate-100 bg-slate-50" role="img" aria-label="Network of this lead">
        {edges.map((e, i) => {
          const a = idx.get(e.source)
          const b = idx.get(e.target)
          if (a === undefined || b === undefined) return null
          return (
            <line
              key={i}
              x1={pos[a].x}
              y1={pos[a].y}
              x2={pos[b].x}
              y2={pos[b].y}
              stroke={e.kind === 'officer' ? '#B0263A' : e.kind === 'at' ? '#d97706' : e.kind === 'record' ? '#4338ca' : '#94a3b8'}
              strokeOpacity={0.45}
              strokeDasharray={e.kind === 'linked' || e.kind === 'record' ? '4 3' : undefined}
            />
          )
        })}
        {nodes.map((x, i) => {
          const r = x.center ? 13 : x.kind === 'site' ? 5 : 8
          return (
            <g key={x.id} onMouseEnter={() => setHover(x.id)} onMouseLeave={() => setHover(null)}>
              <title>{`${NODE_LABEL[x.kind] ?? x.kind}: ${x.label}${x.shared && x.shared > 1 ? ` (shared by ${x.shared})` : ''}${x.bad && !RECORD_KINDS.has(x.kind) ? ' (terminated or disqualified)' : ''}`}</title>
              {x.kind === 'address' ? (
                <rect x={pos[i].x - r} y={pos[i].y - r} width={2 * r} height={2 * r} fill={NODE_COLOR.address} />
              ) : RECORD_KINDS.has(x.kind) ? (
                <polygon
                  points={`${pos[i].x},${pos[i].y - r - 1} ${pos[i].x + r + 1},${pos[i].y} ${pos[i].x},${pos[i].y + r + 1} ${pos[i].x - r - 1},${pos[i].y}`}
                  fill={NODE_COLOR[x.kind]}
                  stroke="#fff"
                  strokeWidth={1.5}
                />
              ) : (
                <circle cx={pos[i].x} cy={pos[i].y} r={r} fill={NODE_COLOR[x.kind] ?? '#64748b'} stroke={x.bad ? '#B0263A' : x.flagged ? '#f59e0b' : '#fff'} strokeWidth={x.bad || x.flagged ? 3 : 1.5} />
              )}
              {showLabel(x) && (
                <text x={Math.min(W - 90, Math.max(90, pos[i].x))} y={pos[i].y - r - 4} textAnchor="middle" fontSize={11} fill="#141B22" stroke="#f8fafc" strokeWidth={3} paintOrder="stroke">
                  {x.label.length > 34 ? x.label.slice(0, 32) + '…' : x.label}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
        {kinds.map((k) => (
          <span key={k} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: NODE_COLOR[k] }} />
            {NODE_LABEL[k] ?? k}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full border-2 border-crimson" />
          Terminated or disqualified
        </span>
        {nodes.some((x) => x.flagged) && (
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-full border-2 border-amber-500" />
            Site named in a finding
          </span>
        )}
      </div>
    </div>
  )
}

function Rows({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return null
  const cols = Array.from(new Set(rows.flatMap((r) => Object.keys(r))))
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-left text-slate-500">
          <tr>
            {cols.map((c) => (
              <th key={c} className="whitespace-nowrap py-1 pr-3 font-medium">
                {c.replace(/_/g, ' ')}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.slice(0, 12).map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c} className="tabular whitespace-nowrap py-1 pr-3 text-ink">
                  {typeof r[c] === 'number' ? fmt(r[c] as number) : String(r[c] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 12 && <div className="mt-1 text-xs text-slate-500">and {rows.length - 12} more rows in the export</div>}
    </div>
  )
}

function LeadDetail({ pid, rank }: { pid: string; rank: number }) {
  const { data, error } = useAsync(() => api.programLead(pid, rank), [pid, rank])
  if (error) return <ErrorNote error={error} />
  if (!data) return <Loading />
  return (
    <div className="space-y-5">
      <div>
        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
          {data.kind_label} {data.id} · {data.status}
        </div>
        <h2 className="mt-0.5 text-lg font-bold text-navy">{data.name}</h2>
        <div className="text-sm text-slate-600">{data.address}</div>
        {data.people.length > 0 && <div className="mt-1 text-sm text-slate-600">People: {data.people.join(', ')}</div>}
        {data.sites > 0 && (
          <div className="mt-1 text-sm text-slate-600">
            {data.sites} sites · {fmt(data.meals)} meals claimed
          </div>
        )}
      </div>
      <div className="space-y-4">
        {data.hits.map((h, i) => (
          <div key={i} className="rounded-md border border-slate-200 p-3">
            <div className={`text-xs font-semibold uppercase tracking-wide ${h.code === 'P_osint' ? 'text-cyan-700' : h.code === 'P_court' ? 'text-indigo-700' : 'text-crimson'}`}>{h.label}</div>
            <p className="mt-1 text-sm text-ink">{h.text}</p>
            <Rows rows={h.rows} />
          </div>
        ))}
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold text-navy">Network</h3>
        <Network lead={data} />
      </div>
    </div>
  )
}

export default function ProgramPage() {
  const { id = '' } = useParams()
  return <ProgramView key={id} id={id} />
}

function ProgramView({ id }: { id: string }) {
  const { data, error } = useAsync(() => api.program(id), [id])
  const [rank, setRank] = useState(1)
  if (error) return <ErrorNote error={error} />
  if (!data) return <Loading />
  const { meta } = data
  const simulated = meta.data_class === 'simulated'
  const stores = (data.counts.retailer ?? 0) + (data.counts.vendor ?? 0)
  return (
    <div className="space-y-6">
      {simulated && (
        <div className="rounded-md bg-violet-100 px-4 py-2 text-sm font-semibold text-violet-900">
          SIMULATED DATA. Every state, person and business on this page is fictional. The patterns follow public fraud cases.
        </div>
      )}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/programs" className="text-xs text-slate-500 hover:underline">
            Program integrity
          </Link>
          <h1 className="text-xl font-bold text-navy">{meta.label}</h1>
          <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
            <ProgramClassBadge dataClass={meta.data_class} label={meta.data_class_label} />
            <span>
              Screened {new Date(meta.created).toLocaleString()} by {meta.analyst}
            </span>
          </div>
        </div>
        <DownloadMenu items={[{ label: 'Leads (CSV)', href: `/api/programs/${id}/leads.csv`, hint: 'Every lead with its signals and the reasons' }]} />
      </div>

      <Card>
        <div className="grid grid-cols-2 gap-6 sm:grid-cols-5">
          <Stat label="Sponsors" value={fmt(data.counts.sponsor ?? 0)} />
          <Stat label="Meal sites" value={fmt(data.counts.site ?? 0)} />
          <Stat label="Meals claimed" value={fmt(data.meals)} />
          <Stat label="SNAP stores and WIC vendors" value={fmt(stores)} />
          <Stat
            label="Leads"
            value={data.leads.length}
            sub={
              data.outside && data.outside.exclusions + data.outside.court_records + data.outside.osint > 0
                ? `Checked against ${data.outside.exclusions} exclusions, ${data.outside.court_records} court cases, ${data.outside.osint} open-source records`
                : data.checks.exclusions
                  ? 'SAM exclusions checked'
                  : 'No exclusions list loaded'
            }
          />
        </div>
      </Card>

      <div className="flex flex-wrap gap-2">
        {Object.entries(data.by_signal)
          .filter(([, n]) => n > 0)
          .map(([code, n]) => (
            <span key={code} className="rounded-full bg-slate-100 px-3 py-1 text-xs text-ink">
              {data.signal_labels[code]} <span className="font-semibold">{n}</span>
            </span>
          ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Card title="Leads, highest score first">
          {data.leads.length === 0 ? (
            <p className="text-sm text-slate-500">Nothing in these files matched a check.</p>
          ) : (
            <ul className="-mx-5 -my-5 divide-y divide-slate-100">
              {data.leads.map((l) => (
                <li key={l.rank}>
                  <button
                    onClick={() => setRank(l.rank)}
                    className={`block w-full px-5 py-3 text-left hover:bg-slate-50 ${rank === l.rank ? 'bg-navy-50' : ''}`}
                  >
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="font-medium text-navy">
                        {l.rank}. {l.name}
                      </span>
                      <span className="tabular shrink-0 text-sm font-semibold text-crimson">{l.score}</span>
                    </div>
                    <div className="text-xs text-slate-500">
                      {l.kind_label} · {l.status}
                      {l.sites > 0 ? ` · ${l.sites} sites` : ''}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {l.signals.map((s) => (
                        <span key={s} className={`rounded px-1.5 py-0.5 text-[11px] ${CHIP[s] ?? 'bg-crimson-50 text-crimson'}`}>
                          {data.signal_labels[s]}
                        </span>
                      ))}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card>{data.leads.length > 0 && <LeadDetail pid={id} rank={rank} />}</Card>
      </div>
      <p className="text-xs text-slate-500">Leads are screening signals for review, not findings of fraud. Each one names the rows behind it.</p>
    </div>
  )
}
