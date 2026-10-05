// Case tools shared by subject screens and leads in a run: notes and evidence, two-person sign-off, awards and the
// evidence ledger. Each page supplies a CaseCtx that says where its notes and sign-off are stored.
import { useState } from 'react'
import { api, money, type AwardEntity, type CaseNote, type CaseSummary, type Ledger, type ScreenAwards, type ScreenReview, type SummaryLine } from './api'
import { useAnalystName } from './App'
import { Button, Card, ErrorNote } from './ui'

export type CaseCtx = {
  what: 'screen' | 'case'
  review: ScreenReview
  reload: () => void
  add: (f: FormData) => Promise<unknown>
  remove: (nid: string, f: FormData) => Promise<unknown>
  evidence: (nid: string) => string
  sign: (f: FormData) => Promise<unknown>
}

export function screenCtx(screenId: string, review: ScreenReview, reload: () => void): CaseCtx {
  const id = encodeURIComponent(screenId)
  return {
    what: 'screen',
    review,
    reload,
    add: (f) => api.addScreenNote(screenId, f),
    remove: (nid, f) => api.deleteScreenNote(screenId, nid, f),
    evidence: (nid) => `/api/subject-screens/${id}/evidence/${nid}`,
    sign: (f) => api.reviewScreen(screenId, f),
  }
}

export function leadCtx(runId: string, uei: string, review: ScreenReview, reload: () => void): CaseCtx {
  const base = `/api/runs/${runId}/vendors/${encodeURIComponent(uei)}`
  return {
    what: 'case',
    review,
    reload,
    add: (f) => api.caseNote(runId, uei, f),
    remove: (nid, f) => api.deleteCaseNote(runId, uei, nid, f),
    evidence: (nid) => `${base}/evidence/${nid}`,
    sign: (f) => api.reviewCase(runId, uei, f),
  }
}

const field = 'w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm'

function sameName(a: string, b: string) {
  return a.trim().replace(/\s+/g, ' ').toLowerCase() === b.trim().replace(/\s+/g, ' ').toLowerCase()
}

const LEAN_STYLE: Record<string, string> = {
  strengthens: 'bg-crimson-50 text-crimson',
  weakens: 'bg-emerald-50 text-emerald-800',
  context: 'bg-slate-100 text-slate-600',
}
const LEAN_TEXT: Record<string, string> = { strengthens: 'Strengthens', weakens: 'Weakens', context: 'Context' }

export function LeanPill({ lean }: { lean: string }) {
  return <span className={`mr-1 inline-block rounded px-1.5 py-0.5 text-[11px] font-medium ${LEAN_STYLE[lean] ?? ''}`}>{LEAN_TEXT[lean] ?? lean}</span>
}

/** Every finding that bears on the lead, for or against, with a bar showing which way it leans. */
export function LedgerCard({ ledger, action }: { ledger: Ledger; action?: React.ReactNode }) {
  const b = ledger.balance
  const total = b.for + b.against || 1
  const lean =
    b.lean === 'strengthens' ? 'Leans toward a problem' : b.lean === 'weakens' ? 'Leans toward an explanation' : b.lean === 'mixed' ? 'Mixed' : 'Nothing yet'
  return (
    <Card title="Evidence ledger" action={action}>
      <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
        <span className="font-medium">{lean}</span>
        <span className="flex h-2.5 w-48 overflow-hidden rounded bg-slate-100" aria-hidden>
          <span className="h-full bg-crimson/80" style={{ width: `${(100 * b.for) / total}%` }} />
          <span className="h-full bg-emerald-600/80" style={{ width: `${(100 * b.against) / total}%` }} />
        </span>
        <span className="text-xs text-slate-500">
          {b.counts.strengthens} strengthen · {b.counts.weakens} weaken · {b.counts.context} context
        </span>
      </div>
      {ledger.rows.length === 0 && <p className="text-sm text-slate-500">No findings yet.</p>}
      <ul className="divide-y divide-slate-100 text-sm">
        {ledger.rows.map((r, i) => (
          <li key={i} id={`ledger-${r.id}`} className="flex scroll-mt-4 gap-3 py-2 target:bg-amber-50">
            <span className="w-8 shrink-0 pt-0.5 font-mono text-xs text-slate-400">{r.id}</span>
            <span className="w-24 shrink-0">
              <LeanPill lean={r.lean} />
            </span>
            <span className="min-w-0 flex-1">
              {r.link ? (
                <a href={r.link} target="_blank" rel="noreferrer" className="text-navy underline">
                  {r.text}
                </a>
              ) : (
                r.text
              )}
              <span className="block text-xs text-slate-500">
                {r.source}
                {r.by && ` · ${r.by}`}
                {r.at && ` · ${r.at.slice(0, 10)}`}
              </span>
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-slate-500">Outside items count only after an analyst confirms they are about this subject. Add your own findings as notes.</p>
    </Card>
  )
}

function NoteItem({ n, ctx, locked }: { n: CaseNote; ctx: CaseCtx; locked: boolean }) {
  const [analyst] = useAnalystName()
  const [error, setError] = useState<string | null>(null)
  return (
    <li className="py-2 text-sm">
      {n.lean && n.lean !== 'context' && <LeanPill lean={n.lean} />}
      {n.text && <p className="whitespace-pre-wrap text-ink">{n.text}</p>}
      <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-slate-500">
        <span>
          {n.analyst} · {n.at.slice(0, 16).replace('T', ' ')} UTC
        </span>
        {n.source && <span>· Source: {/^https?:\/\//.test(n.source) ? <a href={n.source} target="_blank" rel="noreferrer" className="text-navy underline">{n.source}</a> : n.source}</span>}
        {n.file && (
          <span title={`SHA-256 ${n.file_sha256}`}>
            ·{' '}
            <a href={ctx.evidence(n.id)} className="text-navy underline">
              {n.file}
            </a>{' '}
            (SHA-256 {n.file_sha256?.slice(0, 12)}…)
          </span>
        )}
        {n.carried_from && <span>· carried from the check of {n.carried_from.created_at.slice(0, 10)}</span>}
        {n.run && <span>· from run {n.run.label}, {n.run.created_at.slice(0, 10)}</span>}
        {!locked && !n.carried_from && !n.run && sameName(n.analyst, analyst) && (
          <button
            className="text-crimson hover:underline"
            onClick={async () => {
              const f = new FormData()
              f.append('analyst', analyst)
              try {
                await ctx.remove(n.id, f)
                ctx.reload()
              } catch (err) {
                setError((err as Error).message)
              }
            }}
          >
            Remove
          </button>
        )}
      </div>
      <ErrorNote error={error} />
    </li>
  )
}

export function Notes({ target, ctx, title = 'Investigator notes', earlier = [] }: { target: string; ctx: CaseCtx; title?: string; earlier?: CaseNote[] }) {
  const [analyst] = useAnalystName()
  const [text, setText] = useState('')
  const [source, setSource] = useState('')
  const [lean, setLean] = useState('context')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [formKey, setFormKey] = useState(0)
  const notes = ctx.review.notes.filter((n) => n.target === target)
  const locked = ctx.review.state === 'approved'
  const add = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const f = new FormData()
    f.append('analyst', analyst)
    f.append('target', target)
    f.append('text', text)
    f.append('source', source)
    f.append('lean', lean)
    if (file) f.append('file', file)
    try {
      await ctx.add(f)
      setText('')
      setSource('')
      setLean('context')
      setFile(null)
      setFormKey((k) => k + 1)
      ctx.reload()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title}
        {notes.length > 0 && ` (${notes.length})`}
      </h3>
      {notes.length > 0 && (
        <ul className="divide-y divide-slate-100">
          {notes.map((n) => (
            <NoteItem key={n.id} n={n} ctx={ctx} locked={locked} />
          ))}
        </ul>
      )}
      {earlier.length > 0 && (
        <details className="mt-1">
          <summary className="cursor-pointer text-xs text-slate-500">{earlier.length} note(s) from earlier runs</summary>
          <ul className="divide-y divide-slate-100 opacity-80">
            {earlier.map((n) => (
              <NoteItem key={n.id} n={n} ctx={ctx} locked />
            ))}
          </ul>
        </details>
      )}
      {locked ? (
        <p className="text-xs text-slate-500">Approved {ctx.what}s are locked. Reopen the review to add notes.</p>
      ) : (
        <form key={formKey} onSubmit={add} className="mt-2 space-y-2">
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} placeholder="What you did or found" className={field} />
          <input value={source} onChange={(e) => setSource(e.target.value)} placeholder="Source (link or citation)" className={field} />
          <div className="flex flex-wrap items-center gap-2">
            <select value={lean} onChange={(e) => setLean(e.target.value)} className="rounded-md border border-slate-300 px-2 py-1.5 text-sm" title="How this bears on the lead; it goes in the evidence ledger">
              <option value="context">Context</option>
              <option value="strengthens">Strengthens the lead</option>
              <option value="weakens">Weakens the lead</option>
            </select>
            <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="max-w-full text-xs" title="Attach evidence (up to 25 MB)" />
            <Button
              type="submit"
              variant="secondary"
              disabled={busy || !analyst.trim() || (!text.trim() && !file)}
              title={analyst.trim() ? undefined : 'Enter your name in the header first'}
            >
              {busy ? 'Saving…' : 'Add note'}
            </Button>
          </div>
          <ErrorNote error={error} />
        </form>
      )}
    </section>
  )
}

const REVIEW_STYLE: Record<string, string> = {
  draft: 'bg-slate-100 text-slate-600',
  submitted: 'bg-amber-50 text-amber-800 ring-1 ring-amber-200',
  returned: 'bg-crimson-50 text-crimson ring-1 ring-crimson/30',
  approved: 'bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200',
}
const ACTION_TEXT = { submit: 'Submitted by', approve: 'Approved by', return: 'Returned by', reopen: 'Reopened by' }

export function Review({ ctx, notesTarget }: { ctx: CaseCtx; notesTarget?: string }) {
  const [analyst] = useAnalystName()
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const r = ctx.review
  const submitter = [...r.history].reverse().find((h) => h.action === 'submit')?.by ?? ''
  const isSubmitter = !!submitter && sameName(submitter, analyst)
  const act = async (action: string) => {
    setBusy(true)
    setError(null)
    const f = new FormData()
    f.append('analyst', analyst)
    f.append('action', action)
    f.append('comment', comment)
    try {
      await ctx.sign(f)
      setComment('')
      ctx.reload()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const noName = !analyst.trim()
  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          Review and sign-off <span className={`rounded px-2 py-0.5 text-xs font-medium ${REVIEW_STYLE[r.state]}`}>{r.state_label}</span>
        </span>
      }
    >
      <div className={notesTarget ? 'grid gap-6 lg:grid-cols-2' : ''}>
        <div className="space-y-3">
          {r.history.length > 0 ? (
            <ul className="space-y-1 text-sm">
              {r.history.map((h, i) => (
                <li key={i}>
                  <span className="text-slate-500">{ACTION_TEXT[h.action]}</span> {h.by} · {h.at.slice(0, 16).replace('T', ' ')} UTC
                  {h.comment && <span className="text-slate-600">: {h.comment}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-slate-600">
              {ctx.what === 'screen'
                ? 'Add notes and evidence to each subject, then submit the screen. A second person approves it or returns it with comments, and approval locks the notes. The sign-off prints in the Word report and the workbook.'
                : 'Add notes and evidence, then submit the case. A second person approves it or returns it with comments, and approval locks the notes. The sign-off prints in the Word case file.'}
            </p>
          )}
          {(r.state === 'submitted' || r.state === 'approved') && (
            <input
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={r.state === 'approved' ? 'Why it is being reopened' : 'Comment (required to return it)'}
              className={field}
            />
          )}
          <div className="flex flex-wrap items-center gap-2">
            {(r.state === 'draft' || r.state === 'returned') && (
              <Button disabled={busy || noName} onClick={() => act('submit')}>
                Submit for review
              </Button>
            )}
            {r.state === 'submitted' && (
              <>
                <Button disabled={busy || noName || isSubmitter} onClick={() => act('approve')}>
                  Approve
                </Button>
                <Button variant="secondary" disabled={busy || noName || isSubmitter || !comment.trim()} onClick={() => act('return')}>
                  Return with comments
                </Button>
                {isSubmitter && <span className="text-xs text-slate-500">You submitted this, so someone else needs to review it.</span>}
              </>
            )}
            {r.state === 'approved' && (
              <Button variant="secondary" disabled={busy || noName || !comment.trim()} onClick={() => act('reopen')}>
                Reopen
              </Button>
            )}
            {noName && <span className="text-xs text-slate-500">Enter your name in the header first.</span>}
          </div>
          <ErrorNote error={error} />
        </div>
        {notesTarget && <Notes target={notesTarget} ctx={ctx} title="Notes on the whole screen" />}
      </div>
    </Card>
  )
}

export function AwardBlock({ entities, awards }: { entities: AwardEntity[]; awards: ScreenAwards }) {
  return (
    <section>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Federal awards · USAspending, {awards.fetched_at.slice(0, 10)}
      </h3>
      <div className="space-y-3">
        {entities.map((e) => (
          <div key={e.uei} className="text-sm">
            <div className="font-medium">
              {e.name} <span className="font-mono text-xs font-normal text-slate-500">{e.uei}</span>
              {e.role !== 'subject' && <span className="ml-1 text-xs font-normal text-slate-500">({e.role})</span>}
            </div>
            {e.error ? (
              <div className="text-xs text-crimson">{e.error}</div>
            ) : e.count === 0 ? (
              <div className="text-xs text-slate-500">No contracts or IDVs on USAspending.</div>
            ) : (
              <>
                <div className="tabular text-xs text-slate-600">
                  {e.count} contracts and IDVs{e.truncated ? ' (largest shown)' : ''} · {money(e.total)} obligated · {e.first.slice(0, 4)}–{e.last.slice(0, 4)} ·{' '}
                  {e.agencies.slice(0, 3).join(', ')}
                  {e.agencies.length > 3 && ` +${e.agencies.length - 3}`}
                </div>
                {e.after_exclusion > 0 && (
                  <div className="mt-1 text-xs font-medium text-crimson">
                    {e.after_exclusion} award{e.after_exclusion === 1 ? '' : 's'} started on or after the exclusion of {e.excluded_since}
                  </div>
                )}
                <ul className="mt-1 divide-y divide-slate-100">
                  {e.awards.slice(0, 5).map((a) => (
                    <li key={a.award_id + a.start} className="flex flex-wrap items-baseline justify-between gap-x-3 py-1 text-xs">
                      <span>
                        {a.url ? (
                          <a href={a.url} target="_blank" rel="noreferrer" className="font-mono text-navy underline">
                            {a.award_id}
                          </a>
                        ) : (
                          <span className="font-mono">{a.award_id}</span>
                        )}{' '}
                        <span className="text-slate-500">
                          {a.agency} · {a.start}
                        </span>
                        {a.after_exclusion && <span className="ml-1 rounded bg-crimson-50 px-1 text-crimson">after exclusion</span>}
                      </span>
                      <span className="tabular">{money(a.amount)}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}


function Cites({ ids, cited }: { ids: string[]; cited: Record<string, string> }) {
  return (
    <>
      {ids.map((id) => (
        <a
          key={id}
          href={`#ledger-${id}`}
          title={cited[id] || 'No longer in the ledger'}
          className={`ml-1 rounded px-1 align-text-top font-mono text-[11px] ${cited[id] ? 'bg-slate-100 text-navy hover:bg-navy hover:text-white' : 'bg-slate-50 text-slate-400 line-through'}`}
        >
          {id}
        </a>
      ))}
    </>
  )
}

/** Claude's draft theory of the case: every sentence cites ledger rows; the analyst edits it before it is relied on. */
export function WrittenSummary({
  runId,
  uei,
  summary,
  earlier,
  enabled,
  locked,
  ledgerIds,
  reload,
}: {
  runId: string
  uei: string
  summary: CaseSummary | null
  earlier: (CaseSummary & { run: { label?: string; created_at: string } }) | null
  enabled: boolean
  locked: boolean
  ledgerIds: string[]
  reload: () => void
}) {
  const [analyst] = useAnalystName()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [edit, setEdit] = useState<{ sentences: SummaryLine[]; next_steps: SummaryLine[] } | null>(null)
  const live = new Set(ledgerIds)
  const cited = summary ? Object.fromEntries(Object.entries(summary.cited).filter(([k]) => live.has(k))) : {}

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      setEdit(null)
      reload()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const draft = () => {
    const f = new FormData()
    f.set('analyst', analyst)
    return run(() => api.draftSummary(runId, uei, f))
  }
  const save = () => edit && run(() => api.saveSummary(runId, uei, { analyst, ...edit }))
  const noName = !analyst.trim()

  if (edit) {
    const line = (k: 'sentences' | 'next_steps', i: number, text: string) =>
      setEdit({ ...edit, [k]: edit[k].map((x, j) => (j === i ? { ...x, text } : x)) })
    const block = (k: 'sentences' | 'next_steps', label: string) => (
      <div className="space-y-2">
        <div className="text-xs font-medium text-slate-600">{label}</div>
        {edit[k].map((x, i) => (
          <div key={i} className="flex items-start gap-2">
            <textarea
              value={x.text}
              onChange={(e) => line(k, i, e.target.value)}
              rows={2}
              className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              aria-label={`${label} ${i + 1}`}
            />
            <span className="shrink-0 pt-1.5">
              <Cites ids={x.sources} cited={cited} />
            </span>
          </div>
        ))}
      </div>
    )
    return (
      <div className="space-y-3">
        {block('sentences', 'Summary')}
        {edit.next_steps.length > 0 && block('next_steps', 'Next checks')}
        <p className="text-xs text-slate-500">Clear a box to drop that sentence. Each sentence keeps the evidence it cites.</p>
        <div className="flex gap-2">
          <Button disabled={busy || noName} onClick={save}>
            {busy ? 'Saving…' : 'Save edit'}
          </Button>
          <Button variant="secondary" onClick={() => setEdit(null)}>
            Cancel
          </Button>
        </div>
        <ErrorNote error={error} />
      </div>
    )
  }

  if (!summary) {
    return (
      <div className="rounded-md border border-dashed border-slate-300 p-3 text-sm">
        {earlier && (
          <div className="mb-3 text-slate-600">
            <div className="text-xs font-medium text-amber-800">Summary from the {earlier.run.label || earlier.run.created_at.slice(0, 10)} run, for reference</div>
            <p className="mt-1">{earlier.sentences.map((x) => x.text).join(' ')}</p>
          </div>
        )}
        {enabled ? (
          <div className="flex flex-wrap items-center gap-3">
            <Button disabled={busy || noName || locked || ledgerIds.length === 0} onClick={draft}>
              {busy ? 'Claude is writing…' : 'Draft a summary with Claude'}
            </Button>
            <span className="text-xs text-slate-500">
              {ledgerIds.length === 0 ? 'Needs at least one finding in the ledger.' : 'Written only from the evidence ledger below, every sentence sourced. You edit it before it counts.'}
            </span>
          </div>
        ) : (
          <p className="text-slate-500">Claude-written summaries are off on this server. An admin turns them on by adding an Anthropic API key in Render.</p>
        )}
        {noName && enabled && <p className="mt-2 text-xs text-slate-500">Enter your name in the header first.</p>}
        <ErrorNote error={error} />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className={`rounded px-1.5 py-0.5 font-medium ${summary.edited_by ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-amber-800'}`}>
          {summary.edited_by ? `Edited by ${summary.edited_by}` : 'Claude draft, not yet edited'}
        </span>
        <span className="text-slate-500">
          Drafted for {summary.requested_by} on {summary.drafted_at.slice(0, 10)}
          {summary.edited_by && `, edited ${summary.edited_at.slice(0, 10)}`}
        </span>
        {summary.stale && <span className="rounded bg-crimson/10 px-1.5 py-0.5 font-medium text-crimson">The evidence has changed since this was written</span>}
      </div>
      <p className="text-[15px] leading-relaxed">
        {summary.sentences.map((x, i) => (
          <span key={i}>
            {x.text}
            <Cites ids={x.sources} cited={cited} />{' '}
          </span>
        ))}
      </p>
      {summary.next_steps.length > 0 && (
        <div>
          <div className="text-xs font-medium text-slate-600">Next checks</div>
          <ol className="mt-1 list-decimal space-y-1 pl-5 text-sm">
            {summary.next_steps.map((x, i) => (
              <li key={i}>
                {x.text}
                <Cites ids={x.sources} cited={cited} />
              </li>
            ))}
          </ol>
        </div>
      )}
      {!locked && (
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" disabled={busy} onClick={() => setEdit({ sentences: summary.sentences, next_steps: summary.next_steps })}>
            Edit
          </Button>
          {enabled && (
            <Button variant="secondary" disabled={busy || noName} onClick={draft}>
              {busy ? 'Claude is writing…' : summary.stale ? 'Redraft from the new evidence' : 'Redraft'}
            </Button>
          )}
        </div>
      )}
      <ErrorNote error={error} />
    </div>
  )
}
