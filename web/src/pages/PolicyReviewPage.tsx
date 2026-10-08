import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, money, num, MUST_CATCH_LABEL, type PolicyPreview } from '../api'
import { useAnalystName } from '../App'
import { Breadcrumbs, usePlace } from '../nav'
import { changeText } from '../policyLabels'
import { Button, Card, ErrorNote, Loading, useAsync } from '../ui'
import { usePolicyRights } from './PoliciesPage'

// Settings that only decide which queue a flagged vendor lands in (mirrors TRIAGE_KEYS in api/policies.py).
const TRIAGE = new Set(['strong_s2_fy25', 'strong_s3_ratio', 'strong_s3_fy25', 'strong_s4_total', 'sam_stale_days', 'exclusions_stale_days'])

type Tab = 'out' | 'in' | 'tier' | 'conf'

function Results({ r }: { r: NonNullable<PolicyPreview['result']> }) {
  const [tab, setTab] = useState<Tab>('out')
  const w = r.workload
  const outs = r.moves.filter((m) => m.kind === 'out')
  const ins = r.moves.filter((m) => m.kind === 'in')
  const dl = r.draft.leads - r.live.leads
  const weeks = (leads: number) => ((leads * w.hours_per_lead) / w.analysts / 40).toFixed(1)
  const dropped = r.must_catch.filter((m) => m.status === 'dropped')
  const tile = 'rounded-lg border border-slate-200 bg-white p-4'
  const tabs: [Tab, string, number][] = [
    ['out', 'Dropping out', outs.length],
    ['in', 'Coming in', ins.length],
    ['tier', 'Tier moves', r.tier_moves_total],
    ['conf', 'Conflicts with decisions', r.conflicts.length],
  ]
  const rows =
    tab === 'tier'
      ? r.tier_moves.map((t) => ({
          uei: t.uei,
          name: t.name,
          tot: t.tot,
          change: `Tier ${t.from} → ${t.to}`,
          because: `still in the ${t.queue} queue`,
        }))
      : tab === 'conf'
        ? r.conflicts.map((m) => ({ uei: m.uei, name: m.name, tot: m.tot, change: `${m.decision} by ${m.decided_by}`, because: m.because }))
        : (tab === 'out' ? outs : ins).map((m) => ({
            uei: m.uei,
            name: m.name,
            tot: m.tot,
            change: m.kind === 'in' ? `New lead (${m.to})` : `Leaves the ${m.from} queue`,
            because: m.because,
          }))
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">
        The whole screen re-run on {r.import.label} ({r.import.created_at.slice(0, 10)}) with the draft, compared with the live version. Nothing was
        saved as an import.
      </p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className={tile}>
          <div className="text-xs text-slate-500">Leads in the queue</div>
          <div className="tabular text-xl font-semibold">
            {num(r.live.leads)} → {num(r.draft.leads)}
          </div>
          <div className="text-xs text-slate-500">
            {outs.length} drop out, {ins.length} come in
          </div>
        </div>
        <div className={tile}>
          <div className="text-xs text-slate-500">Dollars under review</div>
          <div className="tabular text-xl font-semibold">
            {money(r.live.dollars)} → {money(r.draft.dollars)}
          </div>
        </div>
        <div className={tile}>
          <div className="text-xs text-slate-500">Review time</div>
          <div className="tabular text-xl font-semibold">
            {num(Math.round(r.draft.leads * w.hours_per_lead))} h ({dl > 0 ? '+' : dl < 0 ? '−' : ''}
            {num(Math.abs(Math.round(dl * w.hours_per_lead)))} h)
          </div>
          <div className="text-xs text-slate-500">
            about {weeks(r.draft.leads)} weeks per analyst (was {weeks(r.live.leads)}), {w.analysts} {w.analysts === 1 ? 'analyst' : 'analysts'}
          </div>
        </div>
        <div className={`${tile} ${r.conflicts.length || dropped.length ? 'border-crimson/30 bg-crimson-50' : 'border-emerald-200 bg-emerald-50'}`}>
          <div className="text-xs text-slate-600">Checks</div>
          <div className="text-sm font-medium">
            {r.conflicts.length} decided {r.conflicts.length === 1 ? 'lead' : 'leads'} would drop
          </div>
          <div className="text-sm font-medium">
            {r.must_catch.length === 0
              ? 'No must-catch vendors set'
              : dropped.length
                ? `${dropped.length} must-catch ${dropped.length === 1 ? 'vendor' : 'vendors'} dropped`
                : `No must-catch vendor dropped`}
          </div>
          {r.must_catch.length > 0 && (
            <div className="text-xs text-slate-600">
              {(['kept', 'added', 'missed', 'absent'] as const)
                .map((s) => [s, r.must_catch.filter((m) => m.status === s).length] as const)
                .filter(([, n]) => n > 0)
                .map(([s, n]) => `${n} ${MUST_CATCH_LABEL[s]}`)
                .join(' · ')}
            </div>
          )}
          <div className="text-xs text-slate-500">{r.tier_moves_total} tier moves</div>
        </div>
      </div>
      <Card>
        <div role="tablist" className="-mx-5 -mt-5 mb-3 flex flex-wrap gap-1 border-b border-slate-200 px-4">
          {tabs.map(([k, label, n]) => (
            <button
              key={k}
              role="tab"
              aria-selected={tab === k}
              onClick={() => setTab(k)}
              className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${tab === k ? 'border-crimson text-navy' : 'border-transparent text-slate-500 hover:text-navy'}`}
            >
              {label} ({num(n)})
            </button>
          ))}
        </div>
        {rows.length === 0 ? (
          <p className="text-sm text-slate-500">None.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="text-left text-xs text-slate-500">
                <tr>
                  <th className="pb-2 font-medium">Vendor</th>
                  <th className="pb-2 font-medium">FY24 + FY25</th>
                  <th className="pb-2 font-medium">Change</th>
                  <th className="pb-2 font-medium">Because</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rows.slice(0, 100).map((m) => (
                  <tr key={m.uei + m.change} className="align-top">
                    <td className="py-2">
                      <Link to={`/runs/${r.import.id}/vendors/${encodeURIComponent(m.uei)}`} className="font-medium text-navy hover:underline">
                        {m.name}
                      </Link>
                      <div className="font-mono text-xs text-slate-500">{m.uei}</div>
                    </td>
                    <td className="tabular py-2">{money(m.tot)}</td>
                    <td className="py-2">{m.change}</td>
                    <td className="py-2 text-slate-700">{m.because}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > 100 && <p className="mt-2 text-xs text-slate-500">Showing 100 of {num(rows.length)}.</p>}
          </div>
        )}
      </Card>
    </div>
  )
}

export default function PolicyReviewPage() {
  const { id = '' } = useParams()
  const [analyst] = useAnalystName()
  const rights = usePolicyRights()
  const me = useAsync(() => api.me(), [])
  const { data: p, error, reload } = useAsync(() => api.policy(id), [id])
  const [pv, setPv] = useState<PolicyPreview | null | undefined>(undefined)
  const [err, setErr] = useState<string | null>(null)
  const [comment, setComment] = useState('')
  const [ack, setAck] = useState(false)
  const [follow, setFollow] = useState(true)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<{ n: number; follow: string | null; followErr?: string } | null>(null)
  usePlace(p ? `${p.name} (review)` : null)

  // Load the preview, and keep checking while it runs.
  useEffect(() => {
    let live = true
    let t: ReturnType<typeof setTimeout>
    const tick = () =>
      api.policyPreview(id).then(
        (r) => {
          if (!live) return
          setPv(r.preview)
          if (r.preview?.state === 'running') t = setTimeout(tick, 2000)
        },
        (e) => live && setErr(String(e.message ?? e)),
      )
    tick()
    return () => {
      live = false
      clearTimeout(t)
    }
  }, [id, pv?.state === 'running' ? 'running' : 'idle'])

  if (error) return <ErrorNote error={error} />
  if (!p || pv === undefined) return <Loading />
  const d = p.draft
  const form = (fields: Record<string, string>) => {
    const f = new FormData()
    Object.entries({ ...fields, analyst }).forEach(([k, v]) => f.append(k, v))
    return f
  }
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setErr(null)
    try {
      await fn()
      reload()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (done)
    return (
      <div className="space-y-4">
        <Breadcrumbs items={[{ label: 'Policies', to: '/policies' }, { label: p.name, to: `/policies/${id}` }, { label: 'Deployed' }]} />
        <div role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 p-5">
          <h1 className="text-xl font-semibold text-emerald-900">
            {p.name} v{done.n} is live
          </h1>
          <p className="mt-1 text-sm text-emerald-900">
            New imports in this pack use it from now on. Imports already made keep the rules they used. The change and its impact are on the Policies
            page for everyone, executives included.
          </p>
          {done.follow && (
            <p className="mt-2 text-sm">
              A follow-up import re-screened under v{done.n}:{' '}
              <Link to={`/runs/${done.follow}/record`} className="font-medium text-navy underline">
                open it
              </Link>
              .
            </p>
          )}
          {done.followErr && <p className="mt-2 text-sm text-crimson">The follow-up import didn’t run: {done.followErr}</p>}
        </div>
        <Link to={`/policies/${id}`} className="text-sm text-navy underline">
          Back to the pack
        </Link>
      </div>
    )

  if (!d)
    return (
      <div className="space-y-3">
        <Breadcrumbs items={[{ label: 'Policies', to: '/policies' }, { label: p.name, to: `/policies/${id}` }, { label: 'Review' }]} />
        <p className="text-sm text-slate-600">This pack has no draft to review.</p>
      </div>
    )

  const triage = d.changes.length > 0 && d.changes.every((c) => TRIAGE.has(c.key))
  const myName = me.data?.auth === 'access' ? me.data.name : analyst.trim()
  const authors = new Set([d.created_by, d.updated_by ?? d.created_by])
  const r = pv?.state === 'done' ? pv.result : null
  const dropped = r?.must_catch.some((m) => m.status === 'dropped')
  const canApprove = rights.manage && (triage || !authors.has(myName))

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Policies', to: '/policies' }, { label: p.name, to: `/policies/${id}` }, { label: `Review draft v${d.n}` }]} />
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-navy">
            Review draft v{d.n} of {p.name}
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Written by {d.created_by}
            {d.updated_by && d.updated_by !== d.created_by ? `, last changed by ${d.updated_by}` : ''}.{' '}
            {triage
              ? 'This only changes which queue flagged vendors land in, so an Admin can deploy it.'
              : 'This changes what gets flagged, so an Admin who didn’t write it has to approve it.'}
            {d.submitted_by ? ` Submitted for approval by ${d.submitted_by}.` : ''}
          </p>
        </div>
        <Link to={`/policies/${id}/edit`} className="rounded-md px-3 py-1.5 text-sm font-medium text-navy ring-1 ring-slate-300 hover:bg-slate-50">
          Back to editing
        </Link>
      </div>
      {d.returned && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
          Returned by {d.returned.by}: “{d.returned.comment}”
        </p>
      )}
      <Card title="What changes">
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {d.changes.map((c) => (
            <li key={c.key}>{changeText(c)}</li>
          ))}
        </ul>
        {d.reason && (
          <p className="mt-3 rounded-md bg-slate-50 p-3 text-sm">
            “{d.reason}” — {d.created_by}
          </p>
        )}
      </Card>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-lg font-semibold text-navy">Full preview</h2>
          {rights.draft && pv?.state !== 'running' && (
            <Button
              variant={r ? 'secondary' : 'primary'}
              disabled={busy}
              onClick={() => act(async () => setPv((await api.startPolicyPreview(id, form({}))).preview))}
            >
              {r ? 'Run it again' : 'Run the full preview'}
            </Button>
          )}
        </div>
        {!pv && (
          <p className="text-sm text-slate-600">
            Not run yet for this draft. It re-runs the whole screen on the latest import, which can take a few minutes on a large file.
          </p>
        )}
        {pv?.state === 'running' && (
          <p role="status" className="text-sm text-slate-700">
            Running: {pv.step}… (started by {pv.by})
          </p>
        )}
        {pv?.state === 'error' && <ErrorNote error={`The preview didn’t finish: ${pv.error}`} />}
        {r && <Results r={r} />}
      </section>

      {r && (
        <Card title={rights.manage ? 'Approve and deploy' : 'Send it on'}>
          {!rights.manage ? (
            <div className="space-y-2 text-sm">
              <p>An Admin reviews this preview and deploys it, or returns it with comments.</p>
              <Button disabled={busy || !!d.submitted_by} onClick={() => act(() => api.submitPolicy(id, form({})))}>
                {d.submitted_by ? 'Submitted' : 'Submit for approval'}
              </Button>
            </div>
          ) : (
            <div className="space-y-3 text-sm">
              {!canApprove && (
                <p className="text-crimson">You wrote this draft, and it changes what gets flagged, so another Admin has to approve it.</p>
              )}
              {dropped && (
                <p className="text-crimson">
                  A must-catch vendor would be dropped. Change the draft, or remove the vendor from the must-catch list with a reason.
                </p>
              )}
              {r.conflicts.length > 0 && (
                <label className="flex items-start gap-2 rounded-md bg-crimson-50 p-3">
                  <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-1" />
                  <span>
                    I’ve looked at the {r.conflicts.length} decided {r.conflicts.length === 1 ? 'lead' : 'leads'} this would drop from new imports.
                    Their decisions stay on record.
                  </span>
                </label>
              )}
              <label className="flex items-start gap-2">
                <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} className="mt-1" />
                <span>Also start a follow-up import of {r.import.label} under the new version (it carries existing decisions over, labeled).</span>
              </label>
              <label className="block space-y-1">
                <span className="font-medium">Comment (required; goes in the audit log and the version history)</span>
                <textarea
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  rows={2}
                  className="block w-full rounded-md border border-slate-300 px-2 py-1.5"
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={busy || !canApprove || dropped || (r.conflicts.length > 0 && !ack) || !comment.trim()}
                  onClick={() =>
                    act(async () => {
                      const out = await api.deployPolicy(id, form({ comment, ack_conflicts: String(ack), follow_up: String(follow) }))
                      setDone({ n: out.version.n, follow: out.follow_up, followErr: out.follow_up_error })
                    })
                  }
                >
                  Approve and deploy
                </Button>
                <Button variant="secondary" disabled={busy || !comment.trim()} onClick={() => act(() => api.returnPolicy(id, form({ comment })))}>
                  Return with comments
                </Button>
              </div>
            </div>
          )}
          <ErrorNote error={err} />
        </Card>
      )}
      {!r && <ErrorNote error={err} />}
    </div>
  )
}
