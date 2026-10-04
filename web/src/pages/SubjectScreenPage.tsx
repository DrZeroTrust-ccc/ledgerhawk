import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  api,
  money,
  SUBJECT_STATUS,
  type AwardEntity,
  type PersonResult,
  type ScreenAwards,
  type ScreenContext,
  type ScreenNote,
  type ScreenReview,
  type SubjectChanges,
  type SubjectResult,
  type SubjectScreen,
} from '../api'
import { useAnalystName } from '../App'
import { Breadcrumbs, usePlace } from '../nav'
import { ContextPanel } from '../Context'
import { Button, Card, DataClassBadge, ErrorNote, FlagChip, Loading, SignalChip, Stat, useAsync } from '../ui'

const STATUS_STYLE: Record<string, string> = {
  excluded: 'bg-crimson-50 text-crimson ring-1 ring-crimson/30',
  tied: 'bg-crimson-50 text-crimson ring-1 ring-crimson/30',
  related_excluded: 'bg-violet-50 text-violet-800 ring-1 ring-violet-200',
  name_only: 'bg-amber-50 text-amber-800 ring-1 ring-amber-200',
  signals: 'bg-amber-50 text-amber-800 ring-1 ring-amber-200',
  registration: 'bg-navy-50 text-navy ring-1 ring-navy-100',
  clear: 'bg-slate-100 text-slate-600',
}

function StatusChip({ status, label }: { status: string; label?: string }) {
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[status] ?? ''}`}>{label ?? SUBJECT_STATUS[status] ?? status}</span>
}

type NotesCtx = { screenId: string; review: ScreenReview; reload: () => void }

const field = 'w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm'

function sameName(a: string, b: string) {
  return a.trim().replace(/\s+/g, ' ').toLowerCase() === b.trim().replace(/\s+/g, ' ').toLowerCase()
}

function NoteItem({ n, ctx, locked }: { n: ScreenNote; ctx: NotesCtx; locked: boolean }) {
  const [analyst] = useAnalystName()
  const [error, setError] = useState<string | null>(null)
  return (
    <li className="py-2 text-sm">
      {n.text && <p className="whitespace-pre-wrap text-ink">{n.text}</p>}
      <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-slate-500">
        <span>
          {n.analyst} · {n.at.slice(0, 16).replace('T', ' ')} UTC
        </span>
        {n.source && <span>· Source: {/^https?:\/\//.test(n.source) ? <a href={n.source} target="_blank" rel="noreferrer" className="text-navy underline">{n.source}</a> : n.source}</span>}
        {n.file && (
          <span title={`SHA-256 ${n.file_sha256}`}>
            ·{' '}
            <a href={`/api/subject-screens/${encodeURIComponent(ctx.screenId)}/evidence/${n.id}`} className="text-navy underline">
              {n.file}
            </a>{' '}
            (SHA-256 {n.file_sha256?.slice(0, 12)}…)
          </span>
        )}
        {n.carried_from && <span>· carried from the check of {n.carried_from.created_at.slice(0, 10)}</span>}
        {!locked && !n.carried_from && sameName(n.analyst, analyst) && (
          <button
            className="text-crimson hover:underline"
            onClick={async () => {
              const f = new FormData()
              f.append('analyst', analyst)
              try {
                await api.deleteScreenNote(ctx.screenId, n.id, f)
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

function Notes({ target, ctx, title = 'Investigator notes' }: { target: string; ctx: NotesCtx; title?: string }) {
  const [analyst] = useAnalystName()
  const [text, setText] = useState('')
  const [source, setSource] = useState('')
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
    if (file) f.append('file', file)
    try {
      await api.addScreenNote(ctx.screenId, f)
      setText('')
      setSource('')
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
      {locked ? (
        <p className="text-xs text-slate-500">Approved screens are locked. Reopen the review to add notes.</p>
      ) : (
        <form key={formKey} onSubmit={add} className="mt-2 space-y-2">
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} placeholder="What you did or found" className={field} />
          <div className="flex flex-wrap items-center gap-2">
            <input value={source} onChange={(e) => setSource(e.target.value)} placeholder="Source (link or citation)" className={`${field} min-w-0 flex-1`} />
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

function Review({ ctx }: { ctx: NotesCtx }) {
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
      await api.reviewScreen(ctx.screenId, f)
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
      <div className="grid gap-6 lg:grid-cols-2">
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
              Add notes and evidence to each subject, then submit the screen. A second person approves it or returns it with comments, and approval locks the
              notes. The sign-off prints in the Word report and the workbook.
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
        <Notes target="screen" ctx={ctx} title="Notes on the whole screen" />
      </div>
    </Card>
  )
}

const PERSON_STYLE: Record<string, string> = { listed: 'signals' }

function Person({ p, ctx, context }: { p: PersonResult; ctx: NotesCtx; context: ScreenContext | null }) {
  const outside = context?.entities.find((c) => c.person_ref === p.ref)
  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-slate-400">Person {p.ref}</span>
          <span>{p.input}</span>
          <StatusChip status={PERSON_STYLE[p.status] ?? p.status} label={p.status_label} />
        </span>
      }
    >
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-4">
          <section>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">What we found</h3>
            <ul className="list-disc space-y-1 pl-5 text-sm text-ink">
              {p.findings.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </section>
          <section>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Next steps</h3>
            <ol className="list-decimal space-y-1 pl-5 text-sm text-ink">
              {p.next_steps.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ol>
          </section>
          {outside && <ContextPanel c={outside} />}
          <Notes target={`p:${p.ref}`} ctx={ctx} />
        </div>
        {p.registrations.length > 0 && (
          <section>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
              SAM registrations listing this person ({p.registrations_total}
              {p.registrations_total > p.registrations.length ? `, first ${p.registrations.length} shown` : ''})
            </h3>
            <ul className="divide-y divide-slate-100 text-sm">
              {p.registrations.map((r) => (
                <li key={r.uei} className="py-1.5">
                  <span className="font-medium">{r.name}</span> <span className="font-mono text-xs text-slate-500">{r.uei}</span>{' '}
                  {r.excluded && <FlagChip flag="EXCLUDED" />}
                  <div className="text-xs text-slate-500">
                    {r.roles.join(', ')} · {r.place}
                    {!r.active && ' · registration not active'}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </Card>
  )
}

function Subject({ s, withDollars, ctx, awards, context }: { s: SubjectResult; withDollars: boolean; ctx: NotesCtx; awards: ScreenAwards | null; context: ScreenContext | null }) {
  const outside = context?.entities.filter((c) => c.ref === s.ref) ?? []
  const awardEntities = awards?.entities.filter((e) => e.refs.includes(s.ref)) ?? []
  const noteCount = ctx.review.notes.filter((n) => n.target === `s:${s.ref}`).length
  const [open, setOpen] = useState(s.status !== 'clear')
  const given = [s.input_uei, s.input_name].filter(Boolean).join(' · ')
  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-slate-400">#{s.ref}</span>
          <span>{s.entities.map((e) => e.sam?.legal_name || e.name).join(' / ') || given}</span>
          <StatusChip status={s.status} />
          {s.role && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">{s.role}</span>}
          {noteCount > 0 && <span className="text-xs font-normal text-slate-500">{noteCount === 1 ? '1 note' : `${noteCount} notes`}</span>}
        </span>
      }
      action={
        <button className="text-xs text-navy hover:underline" onClick={() => setOpen(!open)}>
          {open ? 'Hide' : 'Show'}
        </button>
      }
    >
      <div className="text-xs text-slate-500">
        Given as {given}. {s.resolution}.
      </div>
      {open && (
        <div className="mt-4 grid gap-6 lg:grid-cols-2">
          <div className="space-y-4">
            <section>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">What we found</h3>
              <ul className="list-disc space-y-1 pl-5 text-sm text-ink">
                {s.findings.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ul>
            </section>
            <section>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Next steps</h3>
              <ol className="list-decimal space-y-1 pl-5 text-sm text-ink">
                {s.next_steps.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ol>
            </section>
            <Notes target={`s:${s.ref}`} ctx={ctx} />
          </div>
          <div className="space-y-4">
            {s.entities.map((e) => (
              <section key={e.uei || e.name} className="rounded-md border border-slate-100 p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{e.sam?.legal_name || e.name}</span>
                  {e.uei && <span className="font-mono text-xs text-slate-500">{e.uei}</span>}
                  {e.exclusion_flags.map((f) => (
                    <FlagChip key={f} flag={f} />
                  ))}
                  {e.signals.map((sig) => (
                    <SignalChip key={sig.id + sig.detail} s={sig} />
                  ))}
                </div>
                {e.sam ? (
                  <div className="mt-1 text-xs text-slate-600">
                    {e.sam.active ? 'Active registration' : `Registration not active (exp. ${e.sam.exp_date || '?'})`} · {e.sam.address}
                    {e.sam.start_date && ` · business start ${e.sam.start_date}`}
                    {e.sam.certs.length > 0 && ` · ${e.sam.certs.join(', ')}`}
                  </div>
                ) : (
                  <div className="mt-1 text-xs text-slate-500">No SAM registration in the extract.</div>
                )}
                {withDollars && (
                  <div className="tabular mt-1 text-xs text-slate-600">
                    {e.in_dollars_run ? `FY24 ${money(e.fy24)} · FY25 ${money(e.fy25)}` : 'Not in the selected run'}
                  </div>
                )}
              </section>
            ))}
            {s.related.length > 0 && (
              <section>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Related entities ({s.related_total}
                  {s.related_total > s.related.length ? `, first ${s.related.length} shown` : ''})
                </h3>
                <ul className="divide-y divide-slate-100 text-sm">
                  {s.related.map((r) => (
                    <li key={r.uei} className="py-1.5">
                      <span className="font-medium">{r.name}</span> <span className="font-mono text-xs text-slate-500">{r.uei}</span>{' '}
                      {r.excluded && <FlagChip flag="EXCLUDED" />} {r.flags.filter((f) => f !== 'EXCLUDED').map((f) => <FlagChip key={f} flag={f} />)}
                      <div className="text-xs text-slate-500">{r.via.join('; ')}</div>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {awards && awardEntities.length > 0 && <AwardBlock entities={awardEntities} awards={awards} />}
            {outside.map((c) => (
              <ContextPanel key={c.uei || c.name} c={c} title={outside.length > 1 ? `Outside context: ${c.name}` : 'Outside context'} />
            ))}
          </div>
        </div>
      )}
    </Card>
  )
}

function Changes({ ch }: { ch: SubjectChanges }) {
  const ps = ch.parent_sources
  const since = [ps.sam_extract_date && `SAM ${ps.sam_extract_date}`, ps.exclusions_extract_date && `exclusions ${ps.exclusions_extract_date}`]
    .filter(Boolean)
    .join(', ')
  const arrow = { worse: 'text-crimson', better: 'text-emerald-700', same: 'text-slate-600' }
  return (
    <Card title="What changed since the last check">
      <p className="text-sm text-slate-600">
        Compared with the <Link to={`/subjects/${ch.parent_id}`} className="text-navy underline">screen of {ch.parent_created_at.slice(0, 10)}</Link>
        {since && ` (${since})`}. {ch.counts.changed} changed, {ch.counts.worse} got worse, {ch.counts.better} improved, {ch.counts.unchanged} unchanged.
      </p>
      {ch.subjects.length + (ch.people?.length ?? 0) === 0 ? (
        <p className="mt-3 text-sm text-slate-500">Nothing changed for any subject.</p>
      ) : (
        <ul className="mt-3 divide-y divide-slate-100">
          {[...ch.subjects, ...(ch.people ?? [])].map((r) => (
            <li key={`${'status_before' in r ? 's' : 'p'}${r.ref}${r.name}`} className="py-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-slate-400">#{r.ref}</span>
                <span className="font-medium">{r.name}</span>
                <span className={arrow[r.direction]}>
                  {r.direction === 'same' ? r.status_now_label : `${r.status_before_label} → ${r.status_now_label}`}
                </span>
              </div>
              <ul className="mt-1 list-disc pl-5 text-ink">
                {r.added.map((x, i) => (
                  <li key={`a${i}`}>
                    <span className="font-medium">New:</span> {x}
                  </li>
                ))}
                {r.removed.map((x, i) => (
                  <li key={`r${i}`} className="text-slate-500">
                    No longer found: {x}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function ContextButton({ id, has, reload }: { id: string; has: boolean; reload: () => void }) {
  const [analyst] = useAnalystName()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <span className="flex flex-col items-end gap-1">
      <Button
        variant="secondary"
        disabled={busy || !analyst.trim()}
        title={analyst.trim() ? 'News, DOJ press releases, federal court records, SEC filings and the OFAC list for every subject and person' : 'Enter your name in the header first'}
        onClick={async () => {
          setBusy(true)
          setError(null)
          const f = new FormData()
          f.append('analyst', analyst)
          try {
            await api.fetchScreenContext(id, f)
            reload()
          } catch (err) {
            setError((err as Error).message)
          } finally {
            setBusy(false)
          }
        }}
      >
        {busy ? 'Searching outside sources…' : has ? 'Refresh outside context' : 'Search news, courts, DOJ, SEC, OFAC'}
      </Button>
      <ErrorNote error={error} />
    </span>
  )
}

function AwardsButton({ id, has, reload }: { id: string; has: boolean; reload: () => void }) {
  const [analyst] = useAnalystName()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <span className="flex flex-col items-end gap-1">
      <Button
        variant="secondary"
        disabled={busy || !analyst.trim()}
        title={analyst.trim() ? 'Contracts and IDVs reported to USAspending.gov for each subject UEI and excluded related firm' : 'Enter your name in the header first'}
        onClick={async () => {
          setBusy(true)
          setError(null)
          const f = new FormData()
          f.append('analyst', analyst)
          try {
            await api.fetchScreenAwards(id, f)
            reload()
          } catch (err) {
            setError((err as Error).message)
          } finally {
            setBusy(false)
          }
        }}
      >
        {busy ? 'Looking up awards…' : has ? 'Refresh awards' : 'Look up awards (USAspending)'}
      </Button>
      <ErrorNote error={error} />
    </span>
  )
}

function AwardBlock({ entities, awards }: { entities: AwardEntity[]; awards: ScreenAwards }) {
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

function Recheck({ id }: { id: string }) {
  const [analyst] = useAnalystName()
  const nav = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <span className="flex flex-col items-end gap-1">
      <Button
        variant="secondary"
        disabled={busy || !analyst.trim()}
        title={analyst.trim() ? 'Run the same subjects against the newest SAM and exclusions extracts' : 'Enter your name in the header first'}
        onClick={async () => {
          setBusy(true)
          setError(null)
          const f = new FormData()
          f.append('analyst', analyst)
          try {
            const r = await api.recheckSubjectScreen(id, f)
            nav(`/subjects/${r.id}`)
          } catch (err) {
            setError((err as Error).message)
          } finally {
            setBusy(false)
          }
        }}
      >
        {busy ? 'Re-checking…' : 'Re-check with latest data'}
      </Button>
      <ErrorNote error={error} />
    </span>
  )
}

function Header({ data, reload }: { data: SubjectScreen; reload: () => void }) {
  const m = data.meta
  const src = data.sources
  return (
    <div className="space-y-3">
      {m.privileged && (
        <div className="rounded-md bg-crimson-50 px-3 py-2 text-sm font-semibold text-crimson">Privileged and Confidential. Prepared at the direction of counsel.</div>
      )}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold text-ink">
            {m.matter || 'Subject screen'} <DataClassBadge dataClass={m.data_class} />
          </h1>
          <p className="text-sm text-slate-500">
            {m.client && `${m.client} · `}Screened by {m.created_by} on {m.created_at.slice(0, 10)} ·{' '}
            {[src.sam_extract_date && `SAM entity extract as of ${src.sam_extract_date}`, src.exclusions_extract_date && `exclusions as of ${src.exclusions_extract_date}`]
              .filter(Boolean)
              .join(' · ')}
            {data.awards && ` · awards from USAspending as of ${data.awards.fetched_at.slice(0, 16).replace('T', ' ')} UTC`}
            {data.context && ` · outside context as of ${data.context.fetched_at.slice(0, 16).replace('T', ' ')} UTC`}
          </p>
        </div>
        <div className="flex flex-wrap items-start gap-2">
          <ContextButton id={m.id} has={!!data.context} reload={reload} />
          {data.subjects.length > 0 && <AwardsButton id={m.id} has={!!data.awards} reload={reload} />}
          <Recheck id={m.id} />
          <a
            href={`/api/subject-screens/${encodeURIComponent(m.id)}/subject-screen.docx`}
            className="rounded-md bg-navy px-3 py-1.5 text-sm font-medium text-white hover:bg-ink"
          >
            Download report (Word)
          </a>
          <a
            href={`/api/subject-screens/${encodeURIComponent(m.id)}/subject-screen.xlsx`}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-navy hover:bg-slate-50"
          >
            Workbook (Excel)
          </a>
          <a
            href={`/api/subject-screens/${encodeURIComponent(m.id)}/link-chart.xlsx`}
            title="Entities and links to import into i2 Analyst's Notebook or Maltego"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-navy hover:bg-slate-50"
          >
            Link chart (i2 / Maltego)
          </a>
        </div>
      </div>
    </div>
  )
}

export default function SubjectScreenPage() {
  const { id = '' } = useParams()
  const { data, error, reload } = useAsync(() => api.subjectScreen(id), [id])
  usePlace(data ? `${data.meta.matter || 'Untitled matter'} (subject screen)` : null)
  const [status, setStatus] = useState('')
  if (error) return <ErrorNote error={error} />
  if (!data) return <Loading />
  const c = data.counts
  const shown = data.subjects.filter((s) => !status || s.status === status)
  const ctx: NotesCtx = { screenId: data.meta.id, review: data.review, reload }
  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Subject screens', to: '/subjects' }, { label: data.meta.matter || 'Untitled matter' }]} />
      <Header data={data} reload={reload} />
      {data.changes && <Changes ch={data.changes} />}
      <Review ctx={ctx} />
      <Card>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Subjects" value={c.subjects} />
          <Stat label="Excluded or tied to an excluded party" value={(c.excluded ?? 0) + (c.tied ?? 0)} />
          <Stat label="Related entity excluded" value={c.related_excluded ?? 0} />
          <Stat label="Related entities found" value={c.related} sub={c.people ? `${c.people} people screened` : undefined} />
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <button onClick={() => setStatus('')} className={`rounded px-2 py-1 text-xs ${status === '' ? 'bg-navy text-white' : 'bg-slate-100 text-slate-700'}`}>
            All ({c.subjects})
          </button>
          {Object.entries(SUBJECT_STATUS)
            .filter(([k]) => c[k])
            .map(([k, label]) => (
              <button key={k} onClick={() => setStatus(k)} className={`rounded px-2 py-1 text-xs ${status === k ? 'bg-navy text-white' : 'bg-slate-100 text-slate-700'}`}>
                {label} ({c[k]})
              </button>
            ))}
        </div>
      </Card>
      {shown.map((s) => (
        <Subject key={s.ref} s={s} withDollars={!!data.meta.dollars_run} ctx={ctx} awards={data.awards} context={data.context} />
      ))}
      {(data.people?.length ?? 0) > 0 && (
        <>
          <h2 className="pt-2 text-lg font-semibold text-ink">People</h2>
          {data.people!.map((p) => (
            <Person key={p.ref} p={p} ctx={ctx} context={data.context} />
          ))}
        </>
      )}
      <p className="text-xs text-slate-500">
        Public federal data only. Shared contacts, addresses and names are leads to test, not proof of common ownership or control. A subject with no hits is
        not cleared; the sources and dates above are the scope of this check.
      </p>
    </div>
  )
}
