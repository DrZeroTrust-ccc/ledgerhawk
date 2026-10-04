import { useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { money, type Graph, type GraphNode } from './api'
import { Button } from './ui'

const W = 760
const H = 520
const CX = W / 2
const CY = H / 2
const R1 = 140
const R2 = 235

type Placed = GraphNode & { x: number; y: number }

const KIND_LABEL: Record<string, string> = {
  vendor: 'Vendor',
  person: 'Contact',
  suite: 'Suite',
  building: 'Building',
  excluded: 'Excluded party',
}

function layout(g: Graph): Placed[] {
  const center = g.nodes.find((n) => n.center)
  if (!center) return []
  const adj = new Map<string, Set<string>>()
  for (const e of g.edges) {
    if (!adj.has(e.source)) adj.set(e.source, new Set())
    if (!adj.has(e.target)) adj.set(e.target, new Set())
    adj.get(e.source)!.add(e.target)
    adj.get(e.target)!.add(e.source)
  }
  const ring1 = [...(adj.get(center.id) ?? [])].map((id) => g.nodes.find((n) => n.id === id)!).filter(Boolean)
  // Suites hang off buildings in the data; keep the building on ring 1 too.
  for (const n of g.nodes) if (n.kind === 'building' && !ring1.includes(n)) ring1.push(n)
  const order = { person: 0, suite: 1, building: 2, excluded: 3, vendor: 4 }
  ring1.sort((a, b) => order[a.kind] - order[b.kind] || a.id.localeCompare(b.id))
  const placed = new Map<string, Placed>()
  placed.set(center.id, { ...center, x: CX, y: CY })
  const angle = new Map<string, number>()
  ring1.forEach((n, i) => {
    const a = (2 * Math.PI * i) / Math.max(ring1.length, 1) - Math.PI / 2
    angle.set(n.id, a)
    placed.set(n.id, { ...n, x: CX + R1 * Math.cos(a), y: CY + R1 * Math.sin(a) })
  })
  const children = new Map<string, GraphNode[]>()
  for (const n of g.nodes) {
    if (placed.has(n.id)) continue
    const parent = [...(adj.get(n.id) ?? [])].find((p) => angle.has(p))
    const key = parent ?? center.id
    if (!children.has(key)) children.set(key, [])
    children.get(key)!.push(n)
  }
  for (const [parent, kids] of children) {
    const base = angle.get(parent) ?? 0
    const spread = Math.min(Math.PI / 2.5, (Math.PI / 9) * (kids.length - 1))
    kids.forEach((n, i) => {
      const a = kids.length === 1 ? base : base - spread / 2 + (spread * i) / (kids.length - 1)
      placed.set(n.id, { ...n, x: CX + R2 * Math.cos(a), y: CY + R2 * Math.sin(a) })
    })
  }
  return [...placed.values()]
}

function NodeShape({ n }: { n: Placed }) {
  const grey = n.hub
  const stroke = grey ? '#94a3b8' : n.kind === 'excluded' || n.excluded ? '#B0263A' : '#1F3447'
  const fill = grey ? '#f1f5f9' : n.center ? '#1F3447' : n.kind === 'excluded' || n.excluded ? '#fbe9ec' : '#ffffff'
  const dash = n.kind === 'vendor' && !n.center && n.lane !== 'outlier' ? '3 2' : undefined
  switch (n.kind) {
    case 'vendor':
      return <circle r={n.center ? 15 : 11} fill={fill} stroke={stroke} strokeWidth={n.kept_pair ? 3 : 1.5} strokeDasharray={dash} />
    case 'person':
      return <circle r={8} fill={fill} stroke={stroke} strokeWidth={1.5} />
    case 'suite':
      return <rect x={-8} y={-8} width={16} height={16} rx={2} fill={fill} stroke={stroke} strokeWidth={1.5} />
    case 'building':
      return <rect x={-9} y={-9} width={18} height={18} fill={fill} stroke={stroke} strokeWidth={1.5} transform="rotate(45)" />
    case 'excluded':
      return <polygon points="0,-11 10,-4 6,9 -6,9 -10,-4" fill={fill} stroke={stroke} strokeWidth={2} />
  }
}

const trunc = (s: string, n = 26) => (s.length > n ? s.slice(0, n - 1) + '…' : s)

function download(name: string, blob: Blob) {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}

const csvCell = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`

export default function LinkGraph({ graph, runId, uei }: { graph: Graph; runId: string; uei: string }) {
  const nav = useNavigate()
  const svgRef = useRef<SVGSVGElement>(null)
  const nodes = useMemo(() => layout(graph), [graph])
  const pos = new Map(nodes.map((n) => [n.id, n]))
  const alone = nodes.length <= 1 + nodes.filter((n) => n.kind !== 'vendor' && n.kind !== 'excluded').length && graph.paths_to_excluded.length === 0
  const hubs = nodes.filter((n) => n.hub)

  const exportCsv = () => {
    const rows = [
      ['type', 'id', 'kind', 'label', 'source', 'target', 'note'],
      ...graph.nodes.map((n) => ['node', n.id, n.kind, n.label, '', '', n.note ?? '']),
      ...graph.edges.map((e) => ['edge', '', e.kind, e.label, e.source, e.target, '']),
    ]
    download(`ledgerhawk-links-${uei}.csv`, new Blob([rows.map((r) => r.map(csvCell).join(',')).join('\n')], { type: 'text/csv' }))
  }
  const exportPng = () => {
    const svg = svgRef.current
    if (!svg) return
    const xml = new XMLSerializer().serializeToString(svg)
    const img = new Image()
    img.onload = () => {
      const c = document.createElement('canvas')
      c.width = W * 2
      c.height = H * 2
      const ctx = c.getContext('2d')!
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, c.width, c.height)
      ctx.drawImage(img, 0, 0, c.width, c.height)
      c.toBlob((b) => b && download(`ledgerhawk-links-${uei}.png`, b), 'image/png')
    }
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(xml)
  }

  return (
    <div className="space-y-4">
      {alone ? (
        <p className="text-sm text-slate-500">
          No other vendor or excluded party shares a contact, suite or building with this vendor
          {hubs.length > 0 ? ' outside of suppressed hubs (shown grey)' : ''}.
        </p>
      ) : null}
      <div className="overflow-x-auto rounded-md border border-slate-100">
        <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="min-w-[640px]" role="img" aria-label="Link graph" xmlns="http://www.w3.org/2000/svg" fontFamily="Inter, system-ui, sans-serif">
          <rect width={W} height={H} fill="#ffffff" />
          {graph.edges.map((e, i) => {
            const a = pos.get(e.source)
            const b = pos.get(e.target)
            if (!a || !b) return null
            const ex = e.kind === 'excluded_as' || a.kind === 'excluded' || b.kind === 'excluded'
            const hub = a.hub || b.hub
            return (
              <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={hub ? '#cbd5e1' : ex ? '#B0263A' : '#64748b'} strokeWidth={ex ? 1.75 : 1.2} strokeDasharray={e.kind === 'same_name_as' ? '4 3' : undefined} opacity={0.8}>
                <title>{`${e.kind.replace(/_/g, ' ')}${e.label ? `: ${e.label}` : ''}`}</title>
              </line>
            )
          })}
          {nodes.map((n) => (
            <g
              key={n.id}
              transform={`translate(${n.x},${n.y})`}
              className={n.kind === 'vendor' && !n.center ? 'cursor-pointer' : undefined}
              onClick={() => n.kind === 'vendor' && !n.center && n.uei && nav(`/runs/${runId}/vendors/${encodeURIComponent(n.uei)}`)}
            >
              <NodeShape n={n} />
              <title>
                {[`${KIND_LABEL[n.kind]}: ${n.label}`, n.uei, n.tot ? money(n.tot) : '', n.agency ? `${n.agency} · ${n.type} · since ${n.active_date}` : '', n.note]
                  .filter(Boolean)
                  .join('\n')}
              </title>
              <text y={n.center ? 30 : 24} textAnchor="middle" fontSize={11} fill={n.hub ? '#94a3b8' : '#141B22'} fontWeight={n.center ? 600 : 400}>
                {trunc(n.label)}
              </text>
              {n.hub && (
                <text y={37} textAnchor="middle" fontSize={10} fill="#94a3b8">
                  {n.universe} entities, suppressed
                </text>
              )}
            </g>
          ))}
        </svg>
      </div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-slate-600">
        <span className="flex items-center gap-1.5">
          <svg width="14" height="14"><circle cx="7" cy="7" r="5.5" fill="#fff" stroke="#1F3447" strokeWidth="1.5" /></svg>Vendor
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="14" height="14"><circle cx="7" cy="7" r="5.5" fill="#fff" stroke="#1F3447" strokeWidth="1.5" strokeDasharray="3 2" /></svg>Outside the outlier pool
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="14" height="14"><circle cx="7" cy="7" r="4" fill="#fff" stroke="#1F3447" strokeWidth="1.5" /></svg>Contact
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="14" height="14"><rect x="2" y="2" width="10" height="10" fill="#fff" stroke="#1F3447" strokeWidth="1.5" /></svg>Suite
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="14" height="14"><rect x="3" y="3" width="8" height="8" fill="#fff" stroke="#1F3447" strokeWidth="1.5" transform="rotate(45 7 7)" /></svg>Building
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="14" height="14"><polygon points="7,1 13,5 11,13 3,13 1,5" fill="#fbe9ec" stroke="#B0263A" strokeWidth="1.5" /></svg>Excluded party
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="14" height="14"><circle cx="7" cy="7" r="5.5" fill="#f1f5f9" stroke="#94a3b8" strokeWidth="1.5" /></svg>Hub, suppressed
        </span>
        <span className="ml-auto flex gap-2">
          <Button variant="secondary" onClick={exportPng}>
            Export PNG
          </Button>
          <Button variant="secondary" onClick={exportCsv}>
            Export CSV
          </Button>
        </span>
      </div>
      {graph.paths_to_excluded.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Paths to excluded parties</h3>
          <ul className="mt-2 space-y-1 text-sm">
            {graph.paths_to_excluded.map((p, i) => (
              <li key={i}>
                {p.to} <span className="text-slate-500">({p.agency})</span> ·{' '}
                {p.hops === null ? 'not connected' : p.hops === 1 ? 'direct' : `${p.hops} hops`}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
