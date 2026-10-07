import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money, type Graph, type GraphEdge, type GraphNode } from './api'
import { useAnalystName } from './App'
import { Button, ErrorNote, Loading } from './ui'

// The link map on a vendor record: vendors, contacts, addresses and excluded parties, laid out by a small force
// simulation. Bubble size is the vendor's obligations in the year picked. Analysts pan, zoom, drag, switch layers off,
// expand a linked vendor's own links, and ask for the shortest path to an excluded party.


type Layer = 'name' | 'people' | 'address' | 'exclusion' | 'screens' | 'hubs'
const LAYERS: { key: Layer; label: string; colour: string; dash?: string }[] = [
  { key: 'name', label: 'Same legal name', colour: '#64748b', dash: '5 4' },
  { key: 'people', label: 'Shared contacts', colour: '#475569' },
  { key: 'address', label: 'Shared suite or building', colour: '#0F766E' },
  { key: 'exclusion', label: 'Exclusion records', colour: '#B0263A' },
  { key: 'screens', label: 'Subject screen links', colour: '#6D28D9', dash: '2 3' },
  { key: 'hubs', label: 'Suppressed hubs', colour: '#cbd5e1' },
]
const LAYER_OF: Record<string, Layer> = {
  same_name_as: 'name',
  has_poc: 'people',
  located_at: 'address',
  in_building: 'address',
  excluded_as: 'exclusion',
  alias_of: 'exclusion',
  jv_partner_of: 'exclusion',
  screen_related: 'screens',
}
const KIND_LABEL: Record<GraphNode['kind'], string> = {
  vendor: 'Vendor',
  person: 'Contact',
  suite: 'Suite',
  building: 'Building',
  excluded: 'Excluded party',
}
const EDGE_LABEL: Record<string, string> = {
  same_name_as: 'same name',
  has_poc: 'contact',
  located_at: 'address',
  in_building: 'building',
  excluded_as: 'excluded as',
  alias_of: 'alias in exclusion record',
  jv_partner_of: 'joint venture partner',
  screen_related: 'subject screen',
}

const layerOf = (e: GraphEdge, nodes: Map<string, GraphNode>): Layer => {
  const l = LAYER_OF[e.kind] ?? 'people'
  // An edge into an excluded party belongs with the exclusions, whatever the tie.
  if (nodes.get(e.source)?.kind === 'excluded' || nodes.get(e.target)?.kind === 'excluded') return 'exclusion'
  return l
}
const isBad = (n: GraphNode) => n.kind === 'excluded' || !!n.excluded

type P = { x: number; y: number; vx: number; vy: number; pinned?: boolean }

function merge(into: Graph | null, add: Graph): Graph {
  if (!into) return { ...add, nodes: add.nodes.map((n) => ({ ...n })), edges: [...add.edges], years: add.years ?? [] }
  const nodes = new Map(into.nodes.map((n) => [n.id, n]))
  for (const n of add.nodes) {
    const had = nodes.get(n.id)
    if (!had) nodes.set(n.id, { ...n, center: false })
    else if (n.money && Object.keys(n.money).length > Object.keys(had.money ?? {}).length) nodes.set(n.id, { ...had, money: n.money })
  }
  const key = (e: GraphEdge) => [e.kind, ...[e.source, e.target].sort()].join('|')
  const seen = new Set(into.edges.map(key))
  const edges = [...into.edges, ...add.edges.filter((e) => !seen.has(key(e)) && seen.add(key(e)))]
  const years = [...new Set([...(into.years ?? []), ...(add.years ?? [])])].sort()
  return { nodes: [...nodes.values()], edges, paths_to_excluded: into.paths_to_excluded, years }
}

function amount(n: GraphNode, year: string) {
  if (!n.money) return 0
  if (year === 'all') return Object.values(n.money).reduce((a, v) => a + Math.max(0, v), 0)
  return Math.max(0, n.money[year] ?? 0)
}

function radius(n: GraphNode, year: string) {
  if (n.kind !== 'vendor') return n.kind === 'person' ? 7 : 9
  const m = amount(n, year)
  return Math.min(44, (n.center ? 13 : 9) + Math.sqrt(m / 1e6) * 4.5)
}

// New this year: money in the year picked, none in any earlier year the map knows about.
function isNew(n: GraphNode, year: string, years: string[]) {
  if (n.kind !== 'vendor' || year === 'all' || !n.money) return false
  const earlier = years.filter((y) => y < year)
  return earlier.length > 0 && (n.money[year] ?? 0) > 0 && earlier.every((y) => (n.money?.[y] ?? 0) <= 0)
}

function shortestPathToExcluded(g: Graph, from: string): string[] | null {
  const adj = new Map<string, string[]>()
  for (const e of g.edges) {
    adj.set(e.source, [...(adj.get(e.source) ?? []), e.target])
    adj.set(e.target, [...(adj.get(e.target) ?? []), e.source])
  }
  const nodes = new Map(g.nodes.map((n) => [n.id, n]))
  const prev = new Map<string, string>([[from, '']])
  const queue = [from]
  while (queue.length) {
    const id = queue.shift()!
    const n = nodes.get(id)
    if (id !== from && n && isBad(n)) {
      const path = [id]
      while (prev.get(path[0])) path.unshift(prev.get(path[0])!)
      return path
    }
    // A suppressed hub is too common to count as a tie, so the search doesn't pass through one.
    if (id !== from && n?.hub) continue
    for (const m of adj.get(id) ?? [])
      if (!prev.has(m)) {
        prev.set(m, id)
        queue.push(m)
      }
  }
  return null
}

function download(name: string, blob: Blob) {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}
const csvCell = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
const trunc = (s: string, n = 28) => (s.length > n ? s.slice(0, n - 1) + '…' : s)
const fy = (y: string) => (y === 'all' ? 'all years' : `FY${y.slice(2)}`)

export default function LinkMap({ runId, uei, full = false, onNoted }: { runId: string; uei: string; full?: boolean; onNoted?: () => void }) {
  const [analyst] = useAnalystName()
  const [graph, setGraph] = useState<Graph | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<string | null>(null)
  const [year, setYear] = useState('all')
  const [layers, setLayers] = useState<Record<Layer, boolean>>({ name: true, people: true, address: true, exclusion: true, screens: true, hubs: true })
  const [sel, setSel] = useState<string>(`v:${uei}`)
  const [path, setPath] = useState<{ ids: string[] | null; searched: number } | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [view, setView] = useState({ x: 0, y: 0, k: 1 })
  const [size, setSize] = useState({ w: 900, h: 560 })
  const autoFit = useRef(true)
  const [, setFrame] = useState(0)
  const pos = useRef(new Map<string, P>())
  const alpha = useRef(1)
  const raf = useRef(0)
  const svgRef = useRef<SVGSVGElement>(null)
  const drag = useRef<{ kind: 'pan' | 'node'; id?: string; x: number; y: number; moved: boolean } | null>(null)

  useEffect(() => {
    let live = true
    setGraph(null)
    setError(null)
    setExpanded(new Set([`v:${uei}`]))
    setSel(`v:${uei}`)
    setPath(null)
    setView({ x: 0, y: 0, k: 1 })
    autoFit.current = true
    pos.current = new Map()
    api.graph(runId, uei).then(
      (g) => {
        if (!live) return
        setGraph(merge(null, g))
        const ys = (g.years ?? []).filter((y) => g.nodes.some((n) => (n.money?.[y] ?? 0) > 0))
        setYear(ys.at(-1) ?? 'all')
      },
      (e) => live && setError(String(e.message ?? e)),
    )
    return () => {
      live = false
    }
  }, [runId, uei])

  const byId = useMemo(() => new Map((graph?.nodes ?? []).map((n) => [n.id, n])), [graph])

  // What's on screen: edges in a layer that's on, and the nodes still reachable from this vendor through them.
  const { nodes, edges } = useMemo(() => {
    if (!graph) return { nodes: [] as GraphNode[], edges: [] as GraphEdge[] }
    const live = graph.edges.filter((e) => {
      const a = byId.get(e.source)
      const b = byId.get(e.target)
      if (!a || !b || !layers[layerOf(e, byId)]) return false
      return layers.hubs || (!a.hub && !b.hub)
    })
    const adj = new Map<string, string[]>()
    for (const e of live) {
      adj.set(e.source, [...(adj.get(e.source) ?? []), e.target])
      adj.set(e.target, [...(adj.get(e.target) ?? []), e.source])
    }
    const seen = new Set([`v:${uei}`])
    const queue = [`v:${uei}`]
    while (queue.length)
      for (const m of adj.get(queue.shift()!) ?? [])
        if (!seen.has(m)) {
          seen.add(m)
          queue.push(m)
        }
    return { nodes: graph.nodes.filter((n) => seen.has(n.id)), edges: live.filter((e) => seen.has(e.source) && seen.has(e.target)) }
  }, [graph, byId, layers, uei])

  // Frame every node on screen, labels included; done while the layout settles until the analyst pans or zooms.
  const fit = useCallback(() => {
    const ps = nodes.map((n) => pos.current.get(n.id)).filter((q): q is P => !!q)
    if (!ps.length) return
    const pad = 60
    const x0 = Math.min(...ps.map((q) => q.x)) - pad
    const x1 = Math.max(...ps.map((q) => q.x)) + pad
    const y0 = Math.min(...ps.map((q) => q.y)) - pad
    const y1 = Math.max(...ps.map((q) => q.y)) + pad
    const k = Math.min(1.2, size.w / (x1 - x0), size.h / (y1 - y0))
    setView({ k, x: (-(x0 + x1) / 2) * k, y: (-(y0 + y1) / 2) * k })
  }, [nodes, size])

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.max(200, e.contentRect.width), h: Math.max(200, e.contentRect.height) }))
    ro.observe(svg)
    return () => ro.disconnect()
  }, [graph])

  // Place newcomers next to a placed neighbour (or on a ring), then let the simulation settle.
  useEffect(() => {
    const p = pos.current
    const fresh = nodes.filter((n) => !p.has(n.id))
    fresh.forEach((n, i) => {
      if (n.id === `v:${uei}`) return p.set(n.id, { x: 0, y: 0, vx: 0, vy: 0, pinned: true })
      const nb = edges.map((e) => (e.source === n.id ? e.target : e.target === n.id ? e.source : '')).find((id) => id && p.has(id))
      const base = nb ? p.get(nb)! : { x: 0, y: 0 }
      const a = (2 * Math.PI * i) / Math.max(fresh.length, 1)
      const r = nb ? 60 : 150
      p.set(n.id, { x: base.x + r * Math.cos(a) + Math.random() * 8, y: base.y + r * Math.sin(a) + Math.random() * 8, vx: 0, vy: 0 })
    })
    if (fresh.length) alpha.current = Math.max(alpha.current, 1)
    else alpha.current = Math.max(alpha.current, 0.3)
    const tick = () => {
      const list = nodes.map((n) => [n, p.get(n.id)!] as const).filter(([, q]) => q)
      const a = alpha.current
      for (let i = 0; i < list.length; i++)
        for (let j = i + 1; j < list.length; j++) {
          const [na, pa] = list[i]
          const [nb, pb] = list[j]
          let dx = pb.x - pa.x
          let dy = pb.y - pa.y
          let d2 = dx * dx + dy * dy
          if (d2 < 1) {
            dx = Math.random() - 0.5
            dy = Math.random() - 0.5
            d2 = 1
          }
          const min = radius(na, year) + radius(nb, year) + 18
          const f = ((0.25 + a) * 16000) / d2 + (d2 < min * min ? 1.5 : 0)
          const d = Math.sqrt(d2)
          pa.vx -= (dx / d) * f
          pa.vy -= (dy / d) * f
          pb.vx += (dx / d) * f
          pb.vy += (dy / d) * f
        }
      for (const e of edges) {
        const pa = p.get(e.source)
        const pb = p.get(e.target)
        if (!pa || !pb) continue
        const dx = pb.x - pa.x
        const dy = pb.y - pa.y
        const d = Math.sqrt(dx * dx + dy * dy) || 1
        const want = 95 + radius(byId.get(e.source)!, year) + radius(byId.get(e.target)!, year)
        const f = ((d - want) / d) * 0.05 * a
        pa.vx += dx * f
        pa.vy += dy * f
        pb.vx -= dx * f
        pb.vy -= dy * f
      }
      for (const [, q] of list) {
        if (q.pinned) {
          q.vx = q.vy = 0
          continue
        }
        q.vx = (q.vx - q.x * 0.004 * a) * 0.6
        q.vy = (q.vy - q.y * 0.004 * a) * 0.6
        q.x += Math.max(-30, Math.min(30, q.vx))
        q.y += Math.max(-30, Math.min(30, q.vy))
      }
      alpha.current *= 0.97
      if (autoFit.current) fit()
      setFrame((f) => f + 1)
      if (alpha.current > 0.02) raf.current = requestAnimationFrame(tick)
    }
    cancelAnimationFrame(raf.current)
    raf.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf.current)
  }, [nodes, edges, byId, year, uei, fit])

  // Wheel zoom needs a non-passive listener so the page doesn't scroll under the map.
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault()
      autoFit.current = false
      setView((v) => ({ ...v, k: Math.min(4, Math.max(0.25, v.k * (ev.deltaY < 0 ? 1.12 : 1 / 1.12))) }))
    }
    svg.addEventListener('wheel', onWheel, { passive: false })
    return () => svg.removeEventListener('wheel', onWheel)
  }, [graph])

  const unit = () => {
    const svg = svgRef.current
    return svg ? size.w / svg.getBoundingClientRect().width : 1
  }
  const onDown = (ev: React.PointerEvent, id?: string) => {
    ev.stopPropagation()
    ;(ev.currentTarget as Element).setPointerCapture?.(ev.pointerId)
    drag.current = { kind: id ? 'node' : 'pan', id, x: ev.clientX, y: ev.clientY, moved: false }
  }
  const onMove = (ev: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    const dx = (ev.clientX - d.x) * unit()
    const dy = (ev.clientY - d.y) * unit()
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true
    if (!d.moved) return
    autoFit.current = false
    d.x = ev.clientX
    d.y = ev.clientY
    if (d.kind === 'pan') setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }))
    else if (d.id) {
      const q = pos.current.get(d.id)
      if (q) {
        q.x += dx / view.k
        q.y += dy / view.k
        q.pinned = true
        setFrame((f) => f + 1)
      }
    }
  }
  const onUp = () => {
    const d = drag.current
    drag.current = null
    if (d && !d.moved && d.kind === 'node' && d.id) setSel(d.id)
    if (d && !d.moved && d.kind === 'pan') setSel('')
  }

  const expand = useCallback(
    async (ids: string[]) => {
      const todo = ids.filter((id) => !expanded.has(id) && byId.get(id)?.in_run && byId.get(id)?.uei).slice(0, 12)
      if (!todo.length) return
      setBusy(todo.length === 1 ? `Loading ${byId.get(todo[0])?.label}…` : `Loading ${todo.length} vendors' links…`)
      try {
        const got = await Promise.allSettled(todo.map((id) => api.graph(runId, byId.get(id)!.uei!)))
        setGraph((g) => got.reduce((acc, r) => (r.status === 'fulfilled' ? merge(acc, r.value) : acc), g))
        setExpanded((s) => new Set([...s, ...todo]))
        setPath(null)
      } finally {
        setBusy(null)
      }
    },
    [expanded, byId, runId],
  )

  if (error) return <ErrorNote error={error} />
  if (!graph) return <Loading />

  const years = graph.years ?? []
  const shownYears = years.filter((y) => graph.nodes.some((n) => (n.money?.[y] ?? 0) > 0)).slice(-6)
  const selected = byId.get(sel)
  const onPath = new Set(path?.ids ?? [])
  const pathEdge = (e: GraphEdge) => {
    const ids = path?.ids
    if (!ids) return false
    const i = ids.indexOf(e.source)
    const j = ids.indexOf(e.target)
    return i >= 0 && j >= 0 && Math.abs(i - j) === 1
  }
  const frontier = nodes.filter((n) => n.in_run && !expanded.has(n.id)).map((n) => n.id)
  const counts = Object.fromEntries(LAYERS.map((l) => [l.key, 0])) as Record<Layer, number>
  for (const e of graph.edges) counts[layerOf(e, byId)]++
  counts.hubs = graph.nodes.filter((n) => n.hub).length

  const findPath = () => {
    const ids = shortestPathToExcluded(graph, `v:${uei}`)
    setPath({ ids, searched: graph.nodes.length })
    if (ids) {
      // Make sure every step of the path is on screen.
      const need = new Set<Layer>()
      for (let i = 1; i < ids.length; i++) {
        const e = graph.edges.find((x) => (x.source === ids[i - 1] && x.target === ids[i]) || (x.source === ids[i] && x.target === ids[i - 1]))
        if (e) need.add(layerOf(e, byId))
      }
      setLayers((l) => ({ ...l, ...Object.fromEntries([...need].map((k) => [k, true])) }))
      setSel(ids.at(-1)!)
    }
  }
  const pathText = (ids: string[]) =>
    ids
      .map((id, i) => {
        const n = byId.get(id)!
        const e = i ? graph.edges.find((x) => (x.source === ids[i - 1] && x.target === id) || (x.source === id && x.target === ids[i - 1])) : null
        return `${e ? ` → (${EDGE_LABEL[e.kind] ?? e.kind}${e.label ? `: ${e.label}` : ''}) → ` : ''}${n.label}${n.uei ? ` [${n.uei}]` : ''}`
      })
      .join('')

  const describe = (n: GraphNode) => {
    const ties = graph.edges
      .filter((e) => e.source === n.id || e.target === n.id)
      .map((e) => {
        const o = byId.get(e.source === n.id ? e.target : e.source)
        return o ? `${o.label} (${EDGE_LABEL[e.kind] ?? e.kind}${e.label ? `: ${e.label}` : ''})` : ''
      })
      .filter(Boolean)
    return `${KIND_LABEL[n.kind]}: ${n.label}${n.uei ? ` [${n.uei}]` : ''}. Linked to ${ties.join('; ') || 'nothing else on the map'}.`
  }
  const addNote = async () => {
    if (!analyst.trim()) return setNote('Enter your name in the Analyst box at the top first, so the note is attributed.')
    const text = path?.ids ? `Link map, shortest path to an excluded party: ${pathText(path.ids)}.` : selected ? `Link map: ${describe(selected)}` : ''
    if (!text) return
    const f = new FormData()
    f.append('analyst', analyst)
    f.append('text', text)
    f.append('source', 'LedgerHawk link map (SAM entity and exclusions extracts, subject screens)')
    try {
      await api.caseNote(runId, uei, f)
      setNote('Added to this case’s notes.')
      onNoted?.()
    } catch (e) {
      setNote(String((e as Error).message ?? e))
    }
  }
  const exportCsv = () => {
    const rows = [
      ['type', 'id', 'kind', 'label', 'uei', 'source', 'target', 'detail', ...years.map((y) => `FY${y.slice(2)} obligations`)],
      ...graph.nodes.map((n) => ['node', n.id, n.kind, n.label, n.uei ?? '', '', '', n.note ?? n.source ?? '', ...years.map((y) => n.money?.[y] ?? '')]),
      ...graph.edges.map((e) => ['edge', '', e.kind, e.label, '', e.source, e.target, '', ...years.map(() => '')]),
    ]
    download(`ledgerhawk-link-map-${uei}.csv`, new Blob([rows.map((r) => r.map(csvCell).join(',')).join('\n')], { type: 'text/csv' }))
  }
  const exportPng = () => {
    const svg = svgRef.current
    if (!svg) return
    const img = new Image()
    img.onload = () => {
      const c = document.createElement('canvas')
      c.width = size.w * 2
      c.height = size.h * 2
      const ctx = c.getContext('2d')!
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, c.width, c.height)
      ctx.drawImage(img, 0, 0, c.width, c.height)
      c.toBlob((b) => b && download(`ledgerhawk-link-map-${uei}.png`, b), 'image/png')
    }
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(new XMLSerializer().serializeToString(svg))
  }

  const P = (id: string) => pos.current.get(id)
  const labelled = (n: GraphNode) => nodes.length <= 30 || n.center || n.id === sel || onPath.has(n.id) || isBad(n) || radius(n, year) > 16

  const panel = (
    <aside className="space-y-3 rounded-lg border border-slate-200 bg-white p-4 text-sm" aria-label="Selected node">
      {!selected ? (
        <p className="text-slate-500">Click a node to see what it is, where it comes from and what it links to.</p>
      ) : (
        <>
          <div>
            <span className="text-xs font-semibold tracking-wide text-slate-500 uppercase">{KIND_LABEL[selected.kind]}</span>
            <h3 className="font-semibold text-ink">{selected.label}</h3>
            {selected.uei && <p className="font-mono text-xs text-slate-500">UEI {selected.uei}</p>}
            <div className="mt-1 flex flex-wrap gap-1.5">
              {isBad(selected) && <span className="rounded bg-crimson-50 px-1.5 py-0.5 text-xs text-crimson">excluded</span>}
              {selected.hub && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">hub, not expanded</span>}
              {isNew(selected, year, years) && <span className="rounded bg-crimson-50 px-1.5 py-0.5 text-xs text-crimson">new money in {fy(year)}</span>}
              {selected.kind === 'vendor' && !selected.in_run && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">not in this run</span>}
            </div>
          </div>
          {selected.kind === 'vendor' && selected.money && Object.keys(selected.money).length > 0 && (
            <table className="w-full text-xs">
              <tbody>
                {Object.entries(selected.money)
                  .sort()
                  .slice(-6)
                  .map(([y, m]) => (
                    <tr key={y} className={y === year ? 'font-semibold text-ink' : 'text-slate-600'}>
                      <td className="py-0.5 pr-2">FY{y.slice(2)}</td>
                      <td className="tabular py-0.5 text-right">{money(m)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}
          {selected.kind === 'excluded' && (
            <p className="text-ink">
              {selected.agency} · {selected.type} · active since {selected.active_date}
              {selected.scope ? ` · ${selected.scope}` : ''}
            </p>
          )}
          {selected.note && <p className="text-slate-600">{selected.note}</p>}
          <div>
            <h4 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Connections</h4>
            <ul className="mt-1 max-h-48 space-y-1 overflow-y-auto">
              {graph.edges
                .filter((e) => e.source === selected.id || e.target === selected.id)
                .map((e, i) => {
                  const o = byId.get(e.source === selected.id ? e.target : e.source)
                  return o ? (
                    <li key={i}>
                      <button type="button" onClick={() => setSel(o.id)} className="text-left text-navy hover:underline">
                        {trunc(o.label, 36)}
                      </button>{' '}
                      <span className="text-xs text-slate-500">
                        {EDGE_LABEL[e.kind] ?? e.kind}
                        {e.label ? `: ${e.label}` : ''}
                      </span>
                    </li>
                  ) : null
                })}
            </ul>
          </div>
          {selected.source && <p className="text-xs text-slate-500">Source: {selected.source}</p>}
          <div className="flex flex-wrap gap-2">
            {selected.kind === 'vendor' && selected.uei && !selected.center && (
              <Link
                to={selected.in_run ? `/runs/${runId}/vendors/${encodeURIComponent(selected.uei)}` : `/vendors/${encodeURIComponent(selected.uei)}`}
                className="rounded-md bg-white px-3 py-1.5 text-sm font-medium text-navy ring-1 ring-slate-300 hover:bg-slate-50"
              >
                Open record
              </Link>
            )}
            {selected.in_run && !expanded.has(selected.id) && (
              <Button variant="secondary" disabled={!!busy} onClick={() => expand([selected.id])}>
                Expand its links
              </Button>
            )}
          </div>
        </>
      )}
    </aside>
  )

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Bubble size by fiscal year">
          <span className="mr-1 text-xs text-slate-500">Bubble size</span>
          {[...shownYears, 'all'].map((y) => (
            <button
              key={y}
              type="button"
              aria-pressed={year === y}
              onClick={() => setYear(y)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium ${year === y ? 'bg-navy text-white' : 'bg-white text-ink ring-1 ring-slate-300 hover:bg-slate-50'}`}
            >
              {y === 'all' ? 'All years' : `FY${y.slice(2)}`}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-1">
          <Button variant="secondary" aria-label="Zoom in" onClick={() => (autoFit.current = false) || setView((v) => ({ ...v, k: Math.min(4, v.k * 1.25) }))}>
            +
          </Button>
          <Button variant="secondary" aria-label="Zoom out" onClick={() => (autoFit.current = false) || setView((v) => ({ ...v, k: Math.max(0.25, v.k / 1.25) }))}>
            −
          </Button>
          <Button variant="secondary" onClick={fit}>
            Fit to screen
          </Button>
          {!full && (
            <Link
              to={`/runs/${runId}/vendors/${encodeURIComponent(uei)}/map`}
              className="rounded-md bg-white px-3 py-1.5 text-sm font-medium text-navy ring-1 ring-slate-300 hover:bg-slate-50"
            >
              Full screen
            </Link>
          )}
        </div>
      </div>
      <fieldset className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-700">
        <legend className="sr-only">Layers</legend>
        {LAYERS.map((l) => (
          <label key={l.key} className={`inline-flex items-center gap-1.5 ${counts[l.key] ? '' : 'text-slate-400'}`}>
            <input type="checkbox" checked={layers[l.key]} onChange={(e) => setLayers((s) => ({ ...s, [l.key]: e.target.checked }))} />
            <svg width="18" height="8" aria-hidden>
              <line x1="0" y1="4" x2="18" y2="4" stroke={l.colour} strokeWidth="2" strokeDasharray={l.dash} />
            </svg>
            {l.label} <span className="text-slate-400">{counts[l.key]}</span>
          </label>
        ))}
      </fieldset>
      <div className={full ? 'grid gap-3 lg:grid-cols-[1fr_300px]' : 'space-y-3'}>
        <div className="relative overflow-hidden rounded-md border border-slate-200 bg-white">
          {busy && <div className="absolute top-2 left-2 rounded bg-navy px-2 py-1 text-xs text-white">{busy}</div>}
          <svg
            ref={svgRef}
            viewBox={`0 0 ${size.w} ${size.h}`}
            className="block w-full touch-none select-none"
            style={{ height: full ? 'calc(100vh - 300px)' : 460, minHeight: 380, cursor: drag.current?.kind === 'pan' ? 'grabbing' : 'grab' }}
            role="img"
            aria-label={`Link map: ${nodes.length} nodes, ${edges.length} links`}
            xmlns="http://www.w3.org/2000/svg"
            fontFamily="IBM Plex Sans, Inter, system-ui, sans-serif"
            onPointerDown={(e) => onDown(e)}
            onPointerMove={onMove}
            onPointerUp={onUp}
          >
            <rect width={size.w} height={size.h} fill="#ffffff" />
            <g transform={`translate(${size.w / 2 + view.x},${size.h / 2 + view.y}) scale(${view.k})`}>
              {edges.map((e, i) => {
                const a = P(e.source)
                const b = P(e.target)
                if (!a || !b) return null
                const l = LAYERS.find((x) => x.key === layerOf(e, byId))!
                const hub = byId.get(e.source)?.hub || byId.get(e.target)?.hub
                const hot = pathEdge(e)
                const near = sel && (e.source === sel || e.target === sel)
                return (
                  <line
                    key={i}
                    x1={a.x}
                    y1={a.y}
                    x2={b.x}
                    y2={b.y}
                    stroke={hot ? '#F2A900' : hub ? '#cbd5e1' : l.colour}
                    strokeWidth={hot ? 5 : near ? 2.4 : 1.4}
                    strokeDasharray={hot ? undefined : l.dash}
                    opacity={path?.ids && !hot ? 0.35 : 0.85}
                  >
                    <title>{`${EDGE_LABEL[e.kind] ?? e.kind}${e.label ? `: ${e.label}` : ''}`}</title>
                  </line>
                )
              })}
              {nodes.map((n) => {
                const q = P(n.id)
                if (!q) return null
                const r = radius(n, year)
                const m = amount(n, year)
                const bad = isBad(n)
                const fresh = !n.center && isNew(n, year, years)
                const zero = n.kind === 'vendor' && m <= 0
                const fill = n.hub
                  ? '#f1f5f9'
                  : n.kind === 'vendor'
                    ? n.center
                      ? '#1F3447'
                      : zero
                        ? '#E5E8EE'
                        : fresh
                          ? '#B4233C'
                          : bad
                            ? '#fbe9ec'
                            : '#5E7FB8'
                    : n.kind === 'excluded'
                      ? '#fbe9ec'
                      : n.kind === 'person'
                        ? '#ffffff'
                        : '#0F766E'
                const stroke = n.id === sel ? '#F2A900' : onPath.has(n.id) ? '#F2A900' : bad ? '#B0263A' : n.hub ? '#94a3b8' : n.kind === 'person' ? '#1F3447' : '#ffffff'
                const sw = n.id === sel || onPath.has(n.id) ? 3.5 : bad ? 2.5 : 1.5
                const dim = path?.ids && !onPath.has(n.id)
                return (
                  <g
                    key={n.id}
                    transform={`translate(${q.x},${q.y})`}
                    className="cursor-pointer"
                    opacity={dim ? 0.45 : 1}
                    onPointerDown={(e) => onDown(e, n.id)}
                    onDoubleClick={() => n.in_run && expand([n.id])}
                  >
                    {n.kind === 'vendor' || n.kind === 'person' ? (
                      <circle r={r} fill={fill} stroke={stroke} strokeWidth={sw} strokeDasharray={zero && !bad ? '3 2' : undefined} />
                    ) : n.kind === 'excluded' ? (
                      <polygon points="0,-12 11,-4 7,10 -7,10 -11,-4" fill={fill} stroke={n.id === sel || onPath.has(n.id) ? '#F2A900' : '#B0263A'} strokeWidth={sw} />
                    ) : (
                      <rect x={-r} y={-r} width={r * 2} height={r * 2} rx={2} fill={fill} stroke={stroke} strokeWidth={sw} transform={n.kind === 'building' ? 'rotate(45)' : undefined} />
                    )}
                    {n.kind === 'vendor' && n.in_run && !expanded.has(n.id) && !n.center && (
                      <text x={r * 0.72} y={-r * 0.72} fontSize={11} fontWeight={700} fill="#1F3447">
                        +
                      </text>
                    )}
                    <title>
                      {[`${KIND_LABEL[n.kind]}: ${n.label}`, n.uei, n.kind === 'vendor' ? `${money(m)} in ${fy(year)}` : '', n.note, n.in_run && !expanded.has(n.id) ? 'Double-click to expand its links' : '']
                        .filter(Boolean)
                        .join('\n')}
                    </title>
                    {labelled(n) && (
                      <text y={r + 13 / view.k} textAnchor="middle" fontSize={11.5 / view.k} fill={n.hub ? '#94a3b8' : '#141B22'} fontWeight={n.center ? 600 : 400} paintOrder="stroke" stroke="#ffffff" strokeWidth={3 / view.k}>
                        {trunc(n.label)}
                        {n.kind === 'vendor' && m > 0 ? ` · ${money(m)}` : ''}
                      </text>
                    )}
                  </g>
                )
              })}
            </g>
          </svg>
          <p className="border-t border-slate-100 px-3 py-1.5 text-xs text-slate-500">
            Drag to pan, scroll to zoom, drag a node to move it. Click a node for details; double-click a vendor marked + to expand its links.
          </p>
        </div>
        {full && panel}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={findPath}>Path to an excluded party?</Button>
        <Button variant="secondary" disabled={!!busy || frontier.length === 0} onClick={() => expand(frontier)}>
          Expand linked vendors{frontier.length ? ` (${Math.min(frontier.length, 12)})` : ''}
        </Button>
        <Button variant="secondary" disabled={!selected && !path?.ids} onClick={addNote}>
          Add {path?.ids ? 'path' : 'selection'} to case notes
        </Button>
        <span className="ml-auto flex gap-2">
          <Button variant="secondary" onClick={exportPng}>
            Export PNG
          </Button>
          <Button variant="secondary" onClick={exportCsv}>
            Export CSV
          </Button>
        </span>
      </div>
      {path && (
        <div className={`rounded-md px-3 py-2 text-sm ${path.ids ? 'bg-amber-50 text-amber-950' : 'bg-slate-50 text-slate-700'}`} role="status">
          {path.ids ? (
            <>
              <strong>
                {path.ids.length - 1} {path.ids.length === 2 ? 'hop' : 'hops'} to {byId.get(path.ids.at(-1)!)?.label}:
              </strong>{' '}
              {pathText(path.ids)}.{' '}
              <button type="button" className="text-navy underline" onClick={() => setPath(null)}>
                Clear
              </button>
            </>
          ) : (
            <>
              No excluded party connects to this vendor among the {path.searched} nodes on the map (suppressed hubs are not followed).
              {frontier.length > 0 && ' Expand linked vendors to search further out.'}
            </>
          )}
        </div>
      )}
      {note && <p className="text-sm text-slate-600">{note}</p>}
      {!full && panel}
      <p className="text-xs text-slate-500">
        Shared names, contacts and addresses are leads to test, not proof of common control. Money: the run’s FY24 and FY25 file, and USAspending by
        fiscal year where a subject screen looked it up.
      </p>
    </div>
  )
}
