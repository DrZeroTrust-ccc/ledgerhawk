import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAnalystName } from '../App'
import { usePlace } from '../nav'
import { Button, Card, ErrorNote, Loading, useAsync } from '../ui'

/** Admins (or anyone, while sign-in is off) create and change packs; everyone can read them. */
export function useCanManagePolicies() {
  const me = useAsync(() => api.me(), [])
  return me.data ? me.data.auth === 'open' || me.data.role === 'admin' : false
}

export default function PoliciesPage() {
  usePlace('Policies')
  const nav = useNavigate()
  const [analyst] = useAnalystName()
  const canManage = useCanManagePolicies()
  const { data, error } = useAsync(() => api.policies(), [])
  const [form, setForm] = useState({
    name: '',
    description: '',
    copy_from: 'ledgerhawk-defaults',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-navy">Policies</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">
          A policy pack is the set of rules an import is screened with: thresholds, carve-out lists and signal settings. Keep one pack per use case.
          Each import records the pack version it used, so its results never change when a pack does.
        </p>
      </div>
      <ErrorNote error={error} />
      {!data && !error && <Loading />}
      {data && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {data.packs.map((p) => (
            <Link
              key={p.id}
              to={`/policies/${p.id}`}
              className="block rounded-lg border border-slate-200 bg-white p-4 hover:border-navy/40 hover:shadow-sm"
            >
              <div className="flex items-start justify-between gap-2">
                <span className="font-semibold text-navy">{p.name}</span>
                <span className="shrink-0 rounded bg-emerald-50 px-1.5 py-0.5 text-xs font-medium text-emerald-800">v{p.live} live</span>
              </div>
              {p.description && <p className="mt-1 line-clamp-2 text-sm text-slate-600">{p.description}</p>}
              <p className="mt-3 text-xs text-slate-500">
                {p.locked ? 'Built in, read-only' : `Created by ${p.created_by}, ${p.created_at.slice(0, 10)}`} · {p.versions.length}{' '}
                {p.versions.length === 1 ? 'version' : 'versions'} · used by {p.imports} {p.imports === 1 ? 'import' : 'imports'}
              </p>
            </Link>
          ))}
        </div>
      )}
      {canManage && data && (
        <Card title="New pack">
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={async (e) => {
              e.preventDefault()
              setBusy(true)
              setErr(null)
              const f = new FormData()
              Object.entries(form).forEach(([k, v]) => f.append(k, v))
              f.append('analyst', analyst)
              try {
                const p = await api.createPolicy(f)
                nav(`/policies/${p.id}`)
              } catch (x) {
                setErr((x as Error).message)
                setBusy(false)
              }
            }}
          >
            <label className="space-y-1 text-sm">
              <span className="block font-medium">Name</span>
              <input
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="e.g. GSA FY26 pilot"
                className="w-60 rounded-md border border-slate-300 px-2 py-1.5"
              />
            </label>
            <label className="min-w-0 flex-1 space-y-1 text-sm">
              <span className="block font-medium">What it's for</span>
              <input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="Program, team or question this pack serves"
                className="w-full rounded-md border border-slate-300 px-2 py-1.5"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="block font-medium">Start from</span>
              <select
                value={form.copy_from}
                onChange={(e) => setForm({ ...form, copy_from: e.target.value })}
                className="rounded-md border border-slate-300 px-2 py-1.5"
              >
                {data.packs.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} v{p.live}
                  </option>
                ))}
              </select>
            </label>
            <Button type="submit" disabled={busy}>
              Create pack
            </Button>
          </form>
          <p className="mt-2 text-xs text-slate-500">
            A new pack starts as an exact copy, so it screens the same way until its rules are changed. Editing rules as plain-English sentences, with
            a preview of the impact, comes next.
          </p>
          <ErrorNote error={err} />
        </Card>
      )}
    </div>
  )
}
