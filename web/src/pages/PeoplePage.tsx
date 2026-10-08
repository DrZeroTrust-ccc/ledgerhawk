import { useState } from 'react'
import { api, type Person } from '../api'
import { usePlace } from '../nav'
import { Button, Card, ErrorNote, Loading, useAsync } from '../ui'

const ROLE_HELP: Record<string, string> = {
  admin: 'Everything an analyst does, plus people, roles and (soon) policies.',
  analyst: 'Works imports, the queue, cases and subject screens.',
  executive: 'Sees everything; can’t change anything.',
}

// Admins only: who can sign in, under what name, with which role. Sign-in itself happens at Cloudflare Access.
export default function PeoplePage({ myEmail }: { myEmail: string }) {
  usePlace('People')
  const { data, error, reload } = useAsync(() => api.people(), [])
  const [form, setForm] = useState({ email: '', name: '', role: 'analyst' })
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const save = async (p: { email: string; name: string; role: string }, done: string) => {
    setBusy(true)
    setErr(null)
    setNote(null)
    const f = new FormData()
    f.append('email', p.email)
    f.append('name', p.name)
    f.append('role', p.role)
    try {
      await api.savePerson(f)
      setNote(done)
      reload()
      return true
    } catch (e) {
      setErr((e as Error).message)
      return false
    } finally {
      setBusy(false)
    }
  }
  const remove = async (p: Person) => {
    if (!window.confirm(`Remove ${p.name} (${p.email})? They won't be able to use LedgerHawk until an Admin adds them again.`)) return
    setErr(null)
    const f = new FormData()
    f.append('email', p.email)
    try {
      await api.removePerson(f)
      setNote(`Removed ${p.name}.`)
      reload()
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const roles = data?.roles ?? {
    admin: 'Admin',
    analyst: 'Analyst',
    executive: 'Executive',
  }
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-navy">People</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">
          People sign in with their work email through Cloudflare Access. Here an Admin decides what each email can do and the name their work is
          recorded under. Every change goes in the audit log.
        </p>
      </div>
      <ErrorNote error={error || err} />
      {note && (
        <p role="status" className="text-sm text-emerald-800">
          {note}
        </p>
      )}
      <Card title="Add someone">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={async (e) => {
            e.preventDefault()
            if (await save(form, `Added ${form.name.trim()} as ${roles[form.role]}.`)) setForm({ email: '', name: '', role: 'analyst' })
          }}
        >
          <label className="space-y-1 text-sm">
            <span className="block font-medium">Work email</span>
            <input
              type="email"
              required
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              className="w-64 rounded-md border border-slate-300 px-2 py-1.5"
            />
          </label>
          <label className="space-y-1 text-sm">
            <span className="block font-medium">Name on their work</span>
            <input
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="w-52 rounded-md border border-slate-300 px-2 py-1.5"
            />
          </label>
          <label className="space-y-1 text-sm">
            <span className="block font-medium">Role</span>
            <select
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value })}
              className="rounded-md border border-slate-300 px-2 py-1.5"
            >
              {Object.entries(roles).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <Button type="submit" disabled={busy}>
            Add
          </Button>
        </form>
        <p className="mt-2 text-xs text-slate-500">{ROLE_HELP[form.role]} Their email also has to be allowed in Cloudflare Access.</p>
      </Card>
      <Card title="Who can use LedgerHawk">
        {!data && !error && <Loading />}
        {data && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="text-left text-xs text-slate-500">
                <tr>
                  <th className="pb-2 font-medium">Name</th>
                  <th className="pb-2 font-medium">Email</th>
                  <th className="pb-2 font-medium">Role</th>
                  <th className="pb-2 font-medium">Last changed</th>
                  <th className="pb-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.bootstrap
                  .filter((b) => !data.people.some((p) => p.email === b.email))
                  .map((b) => (
                    <tr key={b.email}>
                      <td className="py-2 font-medium">{b.name}</td>
                      <td className="py-2 text-slate-600">{b.email}</td>
                      <td className="py-2">Admin</td>
                      <td className="py-2 text-xs text-slate-500">Permanent Admin, set on the server</td>
                      <td />
                    </tr>
                  ))}
                {data.people.map((p) => {
                  const permanent = data.bootstrap.some((b) => b.email === p.email)
                  return (
                    <tr key={p.email}>
                      <td className="py-2 font-medium">{p.name}</td>
                      <td className="py-2 text-slate-600">{p.email}</td>
                      <td className="py-2">
                        {permanent || p.email === myEmail ? (
                          roles[p.role]
                        ) : (
                          <select
                            aria-label={`Role for ${p.name}`}
                            value={p.role}
                            disabled={busy}
                            onChange={(e) => save({ ...p, role: e.target.value }, `${p.name} is now ${roles[e.target.value]}.`)}
                            className="rounded-md border border-slate-300 px-2 py-1"
                          >
                            {Object.entries(roles).map(([k, v]) => (
                              <option key={k} value={k}>
                                {v}
                              </option>
                            ))}
                          </select>
                        )}
                      </td>
                      <td className="py-2 text-xs text-slate-500">
                        {permanent ? 'Permanent Admin · ' : ''}
                        {p.added_by}, {new Date(p.at).toLocaleDateString()}
                      </td>
                      <td className="py-2 text-right">
                        {!permanent && p.email !== myEmail && (
                          <Button variant="ghost" onClick={() => remove(p)}>
                            Remove
                          </Button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
