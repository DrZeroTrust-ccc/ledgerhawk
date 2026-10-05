// Triage pane: decide a lead from the queue without leaving the list. Keyboard: 1–5 picks a disposition,
// Ctrl+Enter saves and moves on, Enter opens the full case.
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money, num, type QueueProgress } from './api'
import { useAnalystName } from './App'
import { Button, ErrorNote, FlagChip, LeadLine, Loading, SignalChip, TierChip, useAsync } from './ui'

export function TriagePane({
  runId,
  uei,
  dispositions,
  pick,
  onDecided,
  onClose,
  position,
}: {
  runId: string
  uei: string
  dispositions: string[]
  pick: { n: number; at: number } | null
  onDecided: () => void
  onClose: () => void
  position: string
}) {
  const [analyst] = useAnalystName()
  const { data: v, error: loadError } = useAsync(() => api.vendor(runId, uei), [runId, uei])
  const [value, setValue] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const noteRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    setValue('')
    setNote('')
    setError(null)
  }, [uei])
  useEffect(() => {
    if (pick && dispositions[pick.n - 1]) {
      setValue(dispositions[pick.n - 1])
      noteRef.current?.focus()
    }
  }, [pick, dispositions])

  const save = async () => {
    if (!value || !note.trim() || !analyst.trim()) return
    setBusy(true)
    setError(null)
    try {
      await api.setDisposition(runId, uei, { value, note, analyst })
      noteRef.current?.blur() // so j/k work again on the next lead
      onDecided()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const keep = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.confirmCarried(runId, { ueis: [uei], analyst })
      onDecided()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <aside className="rounded-lg border border-slate-200 bg-white shadow-sm lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2 text-xs text-slate-500">
        <span>{position}</span>
        <button onClick={onClose} className="rounded px-1.5 py-0.5 hover:bg-slate-100" aria-label="Close the side pane">
          Close (Esc)
        </button>
      </div>
      <ErrorNote error={loadError} />
      {!v && !loadError && (
        <div className="p-4">
          <Loading />
        </div>
      )}
      {v && (
        <div className="space-y-4 p-4 text-sm">
          <div>
            <h2 className="text-lg font-semibold leading-snug text-navy">{v.name}</h2>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <span className="font-mono">{v.uei}</span>
              {v.sam && (
                <span>
                  {v.sam.city}, {v.sam.state}
                </span>
              )}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <TierChip tier={v.tier} changed={!!v.tier_change} />
              <span className="tabular font-semibold">{money(v.tot)}</span>
              <span className="tabular text-xs text-slate-500">
                FY24 {money(v.fy24)} → FY25 {money(v.fy25)}
              </span>
            </div>
          </div>
          <LeadLine hawk={v.hawk} headline={v.headline} className="font-medium text-ink" />
          <p className="text-slate-700">{v.why}</p>
          {(v.signals.length > 0 || v.exclusion_flags.length > 0) && (
            <div className="flex flex-wrap gap-1">
              {v.signals.map((s, i) => (
                <SignalChip key={i} s={s} />
              ))}
              {v.exclusion_flags.map((f) => (
                <FlagChip key={f} flag={f} />
              ))}
            </div>
          )}
          <div className="text-xs text-slate-500">
            Owner: {v.owner || 'none'}
            {v.assignee && ` · assigned to ${v.assignee}`}
            {v.links.length > 0 && ` · ${num(v.links.length)} linked vendor${v.links.length > 1 ? 's' : ''}`}
          </div>

          {v.disposition && (
            <div className={`rounded-md p-3 ${v.disposition.carried_from ? 'border border-dashed border-amber-300 bg-amber-50/60' : 'bg-slate-50'}`}>
              {v.disposition.carried_from && (
                <div className="mb-1 text-xs font-medium text-amber-800">Carried from the {v.disposition.carried_from.created_at.slice(0, 10)} run</div>
              )}
              <div className="font-medium">{v.disposition.value}</div>
              <div className="text-slate-600">{v.disposition.note}</div>
              <div className="text-xs text-slate-500">
                {v.disposition.analyst} · {new Date(v.disposition.at).toLocaleDateString()}
              </div>
              {v.disposition.carried_from && (
                <Button variant="secondary" className="mt-2" disabled={busy || !analyst.trim()} onClick={keep}>
                  Keep it in this run
                </Button>
              )}
            </div>
          )}

          <div className="space-y-2 border-t border-slate-100 pt-3">
            <div className="text-xs font-medium text-slate-600">{v.disposition ? 'Decide again' : 'Decide'}</div>
            <div className="flex flex-wrap gap-1.5">
              {dispositions.map((d, i) => (
                <button
                  key={d}
                  onClick={() => {
                    setValue(d)
                    noteRef.current?.focus()
                  }}
                  className={`rounded-md border px-2 py-1 text-xs ${value === d ? 'border-navy bg-navy text-white' : 'border-slate-300 hover:border-navy'}`}
                >
                  <span className="mr-1 font-mono opacity-60">{i + 1}</span>
                  {d}
                </button>
              ))}
            </div>
            <textarea
              ref={noteRef}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault()
                  save()
                }
                if (e.key === 'Escape') (e.target as HTMLTextAreaElement).blur()
              }}
              rows={3}
              placeholder="Note (required): what you checked and why"
              className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button disabled={busy || !value || !note.trim() || !analyst.trim()} onClick={save}>
                {busy ? 'Saving…' : 'Save and next'}
              </Button>
              <span className="text-xs text-slate-500">Ctrl+Enter</span>
              <Link to={`/runs/${runId}/vendors/${encodeURIComponent(uei)}`} className="ml-auto text-sm font-medium text-navy hover:underline">
                Open full case →
              </Link>
            </div>
            {!analyst.trim() && <p className="text-xs text-slate-500">Enter your name in the header first.</p>}
            <ErrorNote error={error} />
          </div>
        </div>
      )}
    </aside>
  )
}

/** How much of the run's queue is left, and what you've done today. */
export function Progress({ p }: { p: QueueProgress }) {
  const pct = p.total ? Math.round((100 * (p.total - p.open)) / p.total) : 0
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm">
      <span>
        <span className="tabular font-semibold">{num(p.open)}</span> open of {num(p.total)}
      </span>
      <span className="h-2 w-40 rounded bg-slate-100" aria-hidden>
        <span className="block h-2 rounded bg-navy" style={{ width: `${pct}%` }} />
      </span>
      <span className="text-slate-600">
        {num(p.decided)} decided in this run · {num(p.decided_today)} today{p.mine_today ? ` (${num(p.mine_today)} by you)` : ''}
      </span>
      {p.carried > 0 && <span className="text-amber-800">{num(p.carried)} carried from the earlier run</span>}
      {p.assigned_to_me_open > 0 && <span className="text-slate-600">{num(p.assigned_to_me_open)} assigned to you and open</span>}
    </div>
  )
}

export const KEYS: [string, string][] = [
  ['j / k', 'next / previous lead'],
  ['x', 'select'],
  ['1–5', 'pick a disposition'],
  ['Ctrl+Enter', 'save and next'],
  ['Enter', 'open full case'],
  ['/', 'search'],
  ['Esc', 'close pane'],
]
