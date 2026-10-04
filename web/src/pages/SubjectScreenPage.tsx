import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, money, SUBJECT_STATUS, type SubjectChanges, type SubjectResult, type SubjectScreen } from '../api'
import { useAnalystName } from '../App'
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

function StatusChip({ status }: { status: string }) {
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[status] ?? ''}`}>{SUBJECT_STATUS[status] ?? status}</span>
}

function Subject({ s, withDollars }: { s: SubjectResult; withDollars: boolean }) {
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
      {ch.subjects.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">Nothing changed for any subject.</p>
      ) : (
        <ul className="mt-3 divide-y divide-slate-100">
          {ch.subjects.map((r) => (
            <li key={r.ref} className="py-2 text-sm">
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

function Header({ data }: { data: SubjectScreen }) {
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
          </p>
        </div>
        <div className="flex flex-wrap items-start gap-2">
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
        </div>
      </div>
    </div>
  )
}

export default function SubjectScreenPage() {
  const { id = '' } = useParams()
  const { data, error } = useAsync(() => api.subjectScreen(id), [id])
  const [status, setStatus] = useState('')
  if (error) return <ErrorNote error={error} />
  if (!data) return <Loading />
  const c = data.counts
  const shown = data.subjects.filter((s) => !status || s.status === status)
  return (
    <div className="space-y-6">
      <Header data={data} />
      {data.changes && <Changes ch={data.changes} />}
      <Card>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Subjects" value={c.subjects} />
          <Stat label="Excluded or tied to an excluded party" value={(c.excluded ?? 0) + (c.tied ?? 0)} />
          <Stat label="Related entity excluded" value={c.related_excluded ?? 0} />
          <Stat label="Related entities found" value={c.related} />
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
        <Subject key={s.ref} s={s} withDollars={!!data.meta.dollars_run} />
      ))}
      <p className="text-xs text-slate-500">
        Public federal data only. Shared contacts, addresses and names are leads to test, not proof of common ownership or control. A subject with no hits is
        not cleared; the sources and dates above are the scope of this check.
      </p>
    </div>
  )
}
