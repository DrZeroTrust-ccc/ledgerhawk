import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, num, type MustCatch, type PolicyChange, type Workload } from '../api'
import { useAnalystName } from '../App'
import { Breadcrumbs, usePlace } from '../nav'
import { changeText, formatSetting, SETTINGS, settingLabel } from '../policyLabels'
import { Button, Card, ErrorNote, Loading, useAsync } from '../ui'
import { usePolicyRights } from './PoliciesPage'


function Change({ c }: { c: PolicyChange }) {
  if (c.added || c.removed)
    return (
      <li>
        {settingLabel(c.key)}: {c.added?.length ? `added ${c.added.join(', ')}` : ''}
        {c.added?.length && c.removed?.length ? '; ' : ''}
        {c.removed?.length ? `removed ${c.removed.join(', ')}` : ''}
      </li>
    )
  return (
    <li>
      {settingLabel(c.key)}: <s className="text-slate-500">{formatSetting(c.key, c.from)}</s> → <strong>{formatSetting(c.key, c.to)}</strong>
    </li>
  )
}

// Vendors the pack must keep flagging: a draft that drops one can't be approved.
function MustCatchCard({ id, items, canManage }: { id: string; items: MustCatch[]; canManage: boolean }) {
  const [analyst] = useAnalystName()
  const [list, setList] = useState(items)
  const [form, setForm] = useState({ uei: '', name: '', reason: '' })
  const [err, setErr] = useState<string | null>(null)
  const post = async (fn: (f: FormData) => Promise<{ must_catch: MustCatch[] }>, fields: Record<string, string>) => {
    const f = new FormData()
    Object.entries({ ...fields, analyst }).forEach(([k, v]) => f.append(k, v))
    try {
      setList((await fn(f)).must_catch)
      setErr(null)
      return true
    } catch (e) {
      setErr((e as Error).message)
      return false
    }
  }
  return (
    <Card title="Must-catch vendors">
      <p className="mb-2 text-xs text-slate-500">Known cases this pack has to keep flagging. A draft that drops one can’t be approved.</p>
      {list.length === 0 && <p className="text-sm text-slate-500">None yet.</p>}
      <ul className="space-y-2 text-sm">
        {list.map((m) => (
          <li key={m.uei}>
            <span className="font-medium">{m.name || m.uei}</span> <span className="font-mono text-xs text-slate-500">{m.uei}</span>
            <div className="text-xs text-slate-600">
              {m.reason} · {m.added_by}
            </div>
            {canManage && (
              <button
                type="button"
                className="text-xs text-navy underline"
                onClick={() => {
                  const reason = window.prompt(`Why should ${m.name || m.uei} no longer have to stay flagged?`)
                  if (reason) post((f) => api.removeMustCatch(id, f), { uei: m.uei, reason })
                }}
              >
                Remove
              </button>
            )}
          </li>
        ))}
      </ul>
      {canManage && (
        <form
          className="mt-3 space-y-2 border-t border-slate-100 pt-3"
          onSubmit={async (e) => {
            e.preventDefault()
            if (await post((f) => api.addMustCatch(id, f), form)) setForm({ uei: '', name: '', reason: '' })
          }}
        >
          <input
            aria-label="UEI"
            placeholder="UEI"
            value={form.uei}
            onChange={(e) => setForm({ ...form, uei: e.target.value })}
            className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
          <input
            aria-label="Vendor name"
            placeholder="Name (optional)"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
          <input
            aria-label="Why it must stay flagged"
            placeholder="Why it must stay flagged"
            value={form.reason}
            onChange={(e) => setForm({ ...form, reason: e.target.value })}
            className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
          <Button type="submit" variant="secondary">
            Add
          </Button>
        </form>
      )}
      <ErrorNote error={err} />
    </Card>
  )
}

// The review-time assumptions behind the editor's hours and per-analyst figures.
function WorkloadCard({ id, w, canManage }: { id: string; w: Workload; canManage: boolean }) {
  const [analyst] = useAnalystName()
  const [cur, setCur] = useState(w)
  const [form, setForm] = useState({ hours: String(w.hours_per_lead), analysts: String(w.analysts) })
  const [err, setErr] = useState<string | null>(null)
  return (
    <Card title="Workload assumptions">
      <p className="text-sm text-slate-700">
        {cur.hours_per_lead} review hours per lead, {cur.analysts} {cur.analysts === 1 ? 'analyst' : 'analysts'} at 40 hours a week
        {cur.set ? '' : ' (default, not set yet)'}.
      </p>
      {canManage && (
        <form
          className="mt-2 flex flex-wrap items-end gap-2"
          onSubmit={async (e) => {
            e.preventDefault()
            const f = new FormData()
            f.append('hours_per_lead', form.hours)
            f.append('analysts', form.analysts)
            f.append('analyst', analyst)
            try {
              setCur(await api.setWorkload(id, f))
              setErr(null)
            } catch (x) {
              setErr((x as Error).message)
            }
          }}
        >
          <label className="text-xs">
            Hours per lead
            <input
              value={form.hours}
              onChange={(e) => setForm({ ...form, hours: e.target.value })}
              className="block w-20 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="text-xs">
            Analysts
            <input
              value={form.analysts}
              onChange={(e) => setForm({ ...form, analysts: e.target.value })}
              className="block w-20 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <Button type="submit" variant="secondary">
            Save
          </Button>
        </form>
      )}
      <ErrorNote error={err} />
    </Card>
  )
}

export default function PolicyPage() {
  const { id = '' } = useParams()
  const [analyst] = useAnalystName()
  const rights = usePolicyRights()
  const canManage = rights.manage
  const { data: p, error, reload } = useAsync(() => api.policy(id), [id])
  usePlace(p ? `${p.name} (policy pack)` : null)
  const [edit, setEdit] = useState<{
    name: string
    description: string
  } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)
  const nav = useNavigate()
  if (error) return <ErrorNote error={error} />
  if (!p) return <Loading />
  const rollback = async (n: number) => {
    if (!window.confirm(`Start a draft that puts v${n}'s rules back? It goes through the full preview and approval like any change.`)) return
    const f = new FormData()
    f.append('version', String(n))
    f.append('analyst', analyst)
    try {
      await api.rollbackPolicy(p.id, f)
      nav(`/policies/${p.id}/review`)
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const groups: Record<string, [string, unknown][]> = {}
  for (const [k, v] of Object.entries(p.live_rules)) (groups[SETTINGS[k]?.group ?? 'Other'] ??= []).push([k, v])

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Policies', to: '/policies' }, { label: p.name }]} />
      {edit ? (
        <form
          className="space-y-2 rounded-lg border border-slate-200 bg-white p-4"
          onSubmit={async (e) => {
            e.preventDefault()
            const f = new FormData()
            f.append('name', edit.name)
            f.append('description', edit.description)
            f.append('analyst', analyst)
            try {
              await api.describePolicy(p.id, f)
              setEdit(null)
              setErr(null)
              reload()
            } catch (x) {
              setErr((x as Error).message)
            }
          }}
        >
          <input
            aria-label="Pack name"
            value={edit.name}
            onChange={(e) => setEdit({ ...edit, name: e.target.value })}
            className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-lg font-semibold"
          />
          <input
            aria-label="What it's for"
            value={edit.description}
            onChange={(e) => setEdit({ ...edit, description: e.target.value })}
            className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          />
          <div className="flex gap-2">
            <Button type="submit">Save</Button>
            <Button type="button" variant="secondary" onClick={() => setEdit(null)}>
              Cancel
            </Button>
          </div>
          <ErrorNote error={err} />
        </form>
      ) : (
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-navy">{p.name}</h1>
            {p.description && <p className="mt-1 text-sm text-slate-600">{p.description}</p>}
            <p className="mt-1 text-xs text-slate-500">
              {p.locked
                ? 'Built in and read-only: the rules LedgerHawk ships with.'
                : `Created by ${p.created_by} on ${p.created_at.slice(0, 10)}` +
                  (p.copied_from ? `, copied from ${p.copied_from.pack_name} v${p.copied_from.version}` : '')}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {rights.draft && !p.locked && (
              <Link to={`/policies/${p.id}/edit`} className="rounded-md bg-navy px-3 py-1.5 text-sm font-medium text-white hover:bg-ink">
                {p.draft ? `Open draft v${p.draft.n}` : 'Edit rules'}
              </Link>
            )}
            {canManage && !p.locked && (
              <Button variant="secondary" onClick={() => setEdit({ name: p.name, description: p.description })}>
                Rename
              </Button>
            )}
          </div>
        </div>
      )}
      {p.draft && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
          Draft v{p.draft.n} by {p.draft.created_by}
          {p.draft.updated_by && p.draft.updated_by !== p.draft.created_by ? `, last changed by ${p.draft.updated_by}` : ''}: {p.draft.changes.length}{' '}
          {p.draft.changes.length === 1 ? 'change' : 'changes'} from v{p.live}. Imports keep using v{p.live} until it’s approved and deployed.
          {p.draft.submitted_by ? ` Submitted for approval by ${p.draft.submitted_by}.` : ''}
          {p.draft.returned ? ` Returned by ${p.draft.returned.by}: “${p.draft.returned.comment}”` : ''}{' '}
          <Link to={`/policies/${p.id}/review`} className="font-medium text-navy underline">
            Review and deploy
          </Link>
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {!p.locked && (
            <Card title={`How v${p.live} differs from LedgerHawk defaults`}>
              {p.vs_defaults.length === 0 ? (
                <p className="text-sm text-slate-600">No differences: this version screens exactly like LedgerHawk defaults.</p>
              ) : (
                <ul className="list-disc space-y-1 pl-5 text-sm">
                  {p.vs_defaults.map((c) => (
                    <Change key={c.key} c={c} />
                  ))}
                </ul>
              )}
            </Card>
          )}
          <Card
            title={`All rules in v${p.live}`}
            action={
              <Button variant="ghost" onClick={() => setShowAll(!showAll)}>
                {showAll ? 'Hide' : 'Show'}
              </Button>
            }
          >
            {!showAll ? (
              <p className="text-sm text-slate-500">
                {Object.keys(p.live_rules).length} settings. Edit rules shows them as plain-English sentences.
              </p>
            ) : (
              <div className="space-y-4">
                {Object.entries(groups).map(([g, items]) => (
                  <div key={g}>
                    <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">{g}</h3>
                    <dl className="mt-1 divide-y divide-slate-100 text-sm">
                      {items.map(([k, v]) => (
                        <div key={k} className="flex justify-between gap-4 py-1.5">
                          <dt className="text-slate-700">{settingLabel(k)}</dt>
                          <dd className="tabular shrink-0 font-medium" title={Array.isArray(v) ? (v as string[]).join(', ') : undefined}>
                            {formatSetting(k, v)}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
        <div className="space-y-6">
          {!p.locked && <MustCatchCard id={p.id} items={p.must_catch} canManage={canManage} />}
          {!p.locked && <WorkloadCard id={p.id} w={p.workload} canManage={canManage} />}
          <Card title="Versions">
            <ol className="space-y-3 text-sm">
              {p.versions.map((v) => (
                <li key={v.n}>
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">v{v.n}</span>
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs font-medium ${v.status === 'live' ? 'bg-emerald-50 text-emerald-800' : v.status === 'draft' ? 'bg-amber-50 text-amber-800' : 'bg-slate-100 text-slate-600'}`}
                    >
                      {v.status === 'live' ? 'Live' : v.status === 'draft' ? 'Draft' : 'Retired'}
                    </span>
                  </div>
                  <div className="text-slate-700">{v.reason}</div>
                  {v.changes.length > 0 && <div className="text-xs text-slate-600">{v.changes.map(changeText).join('; ')}</div>}
                  <div className="text-xs text-slate-500">
                    {v.created_by}
                    {v.approved_by ? `, approved by ${v.approved_by}` : ''}
                    {(v.approved_at ?? v.at) ? ` · ${(v.approved_at ?? v.at).slice(0, 10)}` : ''}
                  </div>
                  {v.approval_comment && <div className="text-xs text-slate-600">“{v.approval_comment}”</div>}
                  {v.impact && (
                    <div className="text-xs text-slate-600">
                      On {v.impact.import.label}: leads {num(v.impact.leads[0])} → {num(v.impact.leads[1])}, {num(v.impact.moves)} vendors moved
                      {v.impact.conflicts ? `, ${v.impact.conflicts} decided leads dropped` : ''}
                    </div>
                  )}
                  {v.status === 'retired' && rights.draft && !p.draft && (
                    <button type="button" onClick={() => rollback(v.n)} className="text-xs text-navy underline">
                      Roll back to this
                    </button>
                  )}
                </li>
              ))}
            </ol>
          </Card>
          <Card title="Imports screened with this pack">
            {p.imports.length === 0 ? (
              <p className="text-sm text-slate-500">None yet. Pick this pack when you start an import.</p>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {p.imports.map((i) => (
                  <li key={i.id}>
                    <Link to={`/runs/${i.id}`} className="text-navy hover:underline">
                      {i.label}
                    </Link>{' '}
                    <span className="text-xs text-slate-500">
                      {i.created_at.slice(0, 10)} · v{i.version}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}
