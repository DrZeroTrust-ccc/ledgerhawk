import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAnalystName } from './App'
import { api, money, type FarElement, type FarProvision, type FarState } from './api'
import { Button, Card, ErrorNote, useAsync } from './ui'

const MARK: Record<FarState, { glyph: string; cls: string; label: string }> = {
  shown: { glyph: '✓', cls: 'text-emerald-700', label: 'Data shows' },
  confirmed: { glyph: '✓', cls: 'text-navy', label: 'Analyst confirmed' },
  needs_record: { glyph: '○', cls: 'text-amber-700', label: 'Needs a record' },
  not_applicable: { glyph: '–', cls: 'text-slate-400', label: 'Not applicable' },
}

function statusPill(p: FarProvision) {
  if (p.status === 'not_applicable') return <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">Not applicable</span>
  const n = p.elements.filter((e) => e.state !== 'not_applicable').length
  if (p.status === 'supported')
    return <span className="rounded bg-crimson/10 px-2 py-0.5 text-xs font-medium text-crimson">Every element shown or confirmed</span>
  return (
    <span className="rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-800">
      Data shows {p.shown} of {n}
      {p.confirmed > 0 && `, ${p.confirmed} confirmed`}
    </span>
  )
}

type Pending = { provision: string; element: string; state: '' | 'confirmed' | 'not_applicable'; prompt: string }

function DecisionForm({ runId, uei, pending, onDone }: { runId: string; uei: string; pending: Pending; onDone: (saved: boolean) => void }) {
  const [analyst] = useAnalystName()
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <form
      className="mt-2 space-y-2 rounded-md bg-slate-50 p-3"
      onSubmit={async (e) => {
        e.preventDefault()
        setBusy(true)
        setError(null)
        try {
          await api.setFar(runId, uei, { ...pending, note, analyst })
          onDone(true)
        } catch (err) {
          setError((err as Error).message)
        } finally {
          setBusy(false)
        }
      }}
    >
      <textarea
        required
        autoFocus
        rows={2}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder={pending.prompt}
        className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={busy || !note.trim() || !analyst.trim()}>
          Save
        </Button>
        <Button type="button" variant="ghost" onClick={() => onDone(false)}>
          Cancel
        </Button>
        {!analyst.trim() && <span className="text-xs text-slate-500">Enter your name in the header first.</span>}
      </div>
      <ErrorNote error={error} />
    </form>
  )
}

function Decided({ d }: { d: NonNullable<FarElement['decision']> }) {
  return (
    <div className={`mt-1 text-xs ${d.carried_from ? 'text-amber-800' : 'text-slate-500'}`}>
      {MARK[d.state].label}: {d.note} ({d.analyst}, {d.at.slice(0, 10)}
      {d.carried_from && `; carried from import ${d.carried_from.label}`})
    </div>
  )
}

function ProvisionRow({ runId, uei, p, onSaved }: { runId: string; uei: string; p: FarProvision; onSaved: () => void }) {
  const [pending, setPending] = useState<Pending | null>(null)
  const done = (saved: boolean) => {
    setPending(null)
    if (saved) onSaved()
  }
  const link = 'text-xs font-medium text-navy hover:underline'
  const off = p.status === 'not_applicable'
  return (
    <li className="py-4 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className={off ? 'opacity-60' : ''}>
          <span className="font-mono text-xs text-slate-600">{p.cite}</span> <span className="font-medium text-ink">{p.title}</span>
        </div>
        {statusPill(p)}
      </div>
      <div className="mt-1 text-xs text-slate-500">
        Binds: {p.binds} · Routes to: {p.routes_to}
      </div>
      {p.decision && <Decided d={p.decision} />}
      {!off && (
        <ul className="mt-2 space-y-2">
          {p.elements.map((e) => (
            <li key={e.id} className="flex gap-2 text-sm">
              <span
                className={`w-4 shrink-0 text-center font-semibold ${MARK[e.state].cls}`}
                title={MARK[e.state].label}
                aria-label={MARK[e.state].label}
              >
                {MARK[e.state].glyph}
              </span>
              <div className="min-w-0 flex-1">
                <div className={e.state === 'not_applicable' ? 'text-slate-400 line-through' : 'text-ink'}>{e.text}</div>
                <div className="text-xs text-slate-600">
                  {e.detail}
                  {e.source && <span className="text-slate-400"> · {e.source}</span>}
                </div>
                {e.decision && <Decided d={e.decision} />}
                <div className="mt-1 flex gap-3">
                  {e.decision ? (
                    <button
                      type="button"
                      className={link}
                      onClick={() => setPending({ provision: p.id, element: e.id, state: '', prompt: 'Why you are taking this decision back' })}
                    >
                      Undo
                    </button>
                  ) : (
                    <>
                      {e.state === 'needs_record' && (
                        <button
                          type="button"
                          className={link}
                          onClick={() =>
                            setPending({ provision: p.id, element: e.id, state: 'confirmed', prompt: 'The record you checked and what it shows' })
                          }
                        >
                          Confirm from a record
                        </button>
                      )}
                      <button
                        type="button"
                        className={link}
                        onClick={() =>
                          setPending({ provision: p.id, element: e.id, state: 'not_applicable', prompt: "Why this element doesn't apply" })
                        }
                      >
                        Not applicable
                      </button>
                    </>
                  )}
                </div>
                {pending?.element === e.id && <DecisionForm runId={runId} uei={uei} pending={pending} onDone={done} />}
              </div>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2">
        <button
          type="button"
          className={link}
          onClick={() =>
            setPending(
              off
                ? { provision: p.id, element: '*', state: '', prompt: 'Why this provision is back under review' }
                : { provision: p.id, element: '*', state: 'not_applicable', prompt: "Why this provision doesn't apply to this vendor" },
            )
          }
        >
          {off ? 'Review this provision again' : 'Provision does not apply'}
        </button>
        {pending?.element === '*' && <DecisionForm runId={runId} uei={uei} pending={pending} onDone={done} />}
      </div>
    </li>
  )
}

// The FAR provisions this vendor's evidence may implicate, on its case page.
export function FarCard({ runId, uei, far, onSaved }: { runId: string; uei: string; far: FarProvision[]; onSaved: () => void }) {
  if (!far.length) return null
  return (
    <Card
      title="FAR provisions to review"
      action={<span className="text-xs text-slate-500">What the evidence may implicate, not a finding of a violation</span>}
    >
      <ul className="divide-y divide-slate-100">
        {far.map((p) => (
          <ProvisionRow key={p.id} runId={runId} uei={uei} p={p} onSaved={onSaved} />
        ))}
      </ul>
    </Card>
  )
}

// Import-wide counts by provision, each linking to the queue filtered to it.
export function FarPanel({ runId }: { runId: string }) {
  const { data } = useAsync(() => api.far(runId), [runId])
  if (!data) return null
  const rows = data.provisions.filter((p) => p.vendors > 0)
  const green = data.provisions.reduce((n, p) => n + p.green, 0)
  return (
    <Card title="FAR provisions to review" action={<span className="text-xs text-slate-500">FAR map {data.version}</span>}>
      {rows.length === 0 ? (
        <p className="text-sm text-slate-500">No vendor's evidence implicates a provision LedgerHawk checks.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-500">
              <th className="pb-2 font-medium">Provision</th>
              <th className="pb-2 font-medium">Binds</th>
              <th className="pb-2 text-right font-medium">Vendors</th>
              <th className="pb-2 text-right font-medium" title="Every element shown by the data or confirmed by an analyst">
                All elements
              </th>
              <th
                className="pb-2 text-right font-medium"
                title="All FY24–FY25 GSA obligations of these vendors, not the amount at issue under the provision"
              >
                Their FY24–FY25 total
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((p) => (
              <tr key={p.id}>
                <td className="py-2 pr-3">
                  <Link to={`/runs/${runId}/queue?queue=&color=red,yellow&far=${encodeURIComponent(p.id)}`} className="hover:underline">
                    <span className="font-mono text-xs text-slate-600">{p.cite}</span> <span className="text-navy">{p.title}</span>
                  </Link>
                </td>
                <td className="py-2 pr-3 text-slate-600">{p.binds}</td>
                <td className="tabular py-2 text-right">{p.vendors}</td>
                <td className="tabular py-2 text-right">{p.supported}</td>
                <td className="tabular py-2 text-right">{money(p.dollars)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-3 text-xs text-slate-500">
        Red and yellow vendors only
        {green > 0 && `; ${green} more provision matches are on green vendors (the watch list or cleared), shown on their case pages`}. LedgerHawk
        shows which elements its data supports and which need a contract record; it doesn't decide that a provision was violated.
      </p>
    </Card>
  )
}
