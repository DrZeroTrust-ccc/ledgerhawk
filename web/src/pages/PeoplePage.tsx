import { useEffect, useState } from 'react'
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
  const [invite, setInvite] = useState<{ name: string; email: string; role: string } | null>(null)

  const save = async (p: { email: string; name: string; role: string }, done: string) => {
    setBusy(true)
    setErr(null)
    setNote(null)
    const f = new FormData()
    f.append('email', p.email)
    f.append('name', p.name)
    f.append('role', p.role)
    try {
      const saved = await api.savePerson(f)
      setNote(saved.access_note ? `${done} ${saved.access_note}` : done)
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
      const r = await api.removePerson(f)
      setNote(`Removed ${p.name}.${r.access_note ? ` ${r.access_note}` : ''}`)
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
            if (await save(form, `Added ${form.name.trim()} as ${roles[form.role]}.`)) {
              setInvite({ name: form.name.trim(), email: form.email.trim(), role: roles[form.role] })
              setForm({ email: '', name: '', role: 'analyst' })
            }
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
        <p className="mt-2 text-xs text-slate-500">
          {ROLE_HELP[form.role]}{' '}
          {data?.access_sync ? 'Cloudflare Access is updated for you.' : 'Their email also has to be allowed in Cloudflare Access.'}
        </p>
        {invite && <InviteMessage invite={invite} site={data?.site || window.location.origin} onClose={() => setInvite(null)} />}
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
                  <th className="pb-2 font-medium">Last signed in</th>
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
                      <td className="py-2 text-xs text-slate-500">{seen(b.last_seen)}</td>
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
                      <td className="py-2 text-xs text-slate-500">{seen(p.last_seen)}</td>
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
      <AlertsCard />
      <BackupsCard />
    </div>
  )
}

const seen = (at?: string | null) => (at ? new Date(at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'Not yet')

// A message to send the person just added: where to go and how signing in works.
function InviteMessage({ invite, site, onClose }: { invite: { name: string; email: string; role: string }; site: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false)
  const text =
    `Hi ${invite.name.split(' ')[0]},\n\n` +
    `You've been given ${invite.role} access to LedgerHawk, the vendor screening tool.\n\n` +
    `1. Go to ${site}\n` +
    `2. Enter your work email (${invite.email}) and choose "Send login code".\n` +
    `3. Type in the code that arrives by email. There's no password to remember.\n\n` +
    `Everything you open, decide or download is recorded in LedgerHawk's audit log.`
  return (
    <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-medium">Invite message for {invite.name}</span>
        <span className="flex gap-3">
          <button
            type="button"
            className="text-navy underline"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(text)
                setCopied(true)
              } catch {
                setCopied(false)
              }
            }}
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
          <a
            className="text-navy underline"
            href={`mailto:${invite.email}?subject=${encodeURIComponent('Your LedgerHawk access')}&body=${encodeURIComponent(text)}`}
          >
            Open in email
          </a>
          <button type="button" className="text-slate-500 underline" onClick={onClose}>
            Close
          </button>
        </span>
      </div>
      <pre className="whitespace-pre-wrap font-sans text-slate-700">{text}</pre>
    </div>
  )
}

// Admins only: what went wrong while nobody was looking, and where those alerts go.
function AlertsCard() {
  const { data, error, reload } = useAsync(() => api.alerts(), [])
  const [note, setNote] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  return (
    <Card
      title="Alerts"
      action={
        <Button
          variant="secondary"
          onClick={async () => {
            setErr(null)
            setNote(null)
            try {
              const r = await api.testAlert()
              setNote(
                r.channels.length
                  ? r.sent.length
                    ? `Test alert sent by ${r.sent.join(' and ')}.`
                    : 'The test alert wasn’t delivered; see below.'
                  : 'Test alert kept here. No chat webhook or email is set up, so nothing was sent.',
              )
              reload()
            } catch (e) {
              setErr((e as Error).message)
            }
          }}
        >
          Send a test alert
        </Button>
      }
    >
      <ErrorNote error={error || err} />
      {note && (
        <p role="status" className="mb-2 text-sm text-emerald-800">
          {note}
        </p>
      )}
      {!data && !error && <Loading />}
      {data && (
        <div className="space-y-3 text-sm">
          <p className="text-slate-600">
            {data.channels.length
              ? `Sent to ${data.channels.join(' and ')} when an import or check fails, a backup fails, or the server restarts without being asked to.`
              : 'Kept here only. Set LEDGERHAWK_ALERT_WEBHOOK (Slack, Teams, Google Chat) or LEDGERHAWK_ALERT_EMAIL with SMTP settings on the server to be told right away.'}
          </p>
          {data.alerts.length === 0 ? (
            <p className="text-slate-500">Nothing has gone wrong.</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {data.alerts.slice(0, 15).map((a) => (
                <li key={a.at + a.title} className="py-2">
                  <div className="flex flex-wrap justify-between gap-x-4">
                    <span className={a.kind === 'test' ? 'font-medium text-slate-700' : 'font-medium text-crimson'}>{a.title}</span>
                    <span className="tabular text-xs text-slate-500">
                      {when(a.at)}
                      {a.repeat ? ' · repeat, not re-sent' : a.sent.length ? ` · sent by ${a.sent.join(', ')}` : ''}
                    </span>
                  </div>
                  {a.detail && <p className="mt-0.5 text-xs text-slate-600">{a.detail}</p>}
                  {a.errors.length > 0 && <p className="mt-0.5 text-xs text-crimson">Not delivered: {a.errors.join('; ')}</p>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  )
}

const mb = (b: number) => `${(b / 1e6).toFixed(1)} MB`
const when = (iso: string) => new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })

// Admins only: nightly copies of everything to a bucket off the server, and a download of everything on demand.
function BackupsCard() {
  const { data, error, reload } = useAsync(() => api.backups(), [])
  const [err, setErr] = useState<string | null>(null)
  const [started, setStarted] = useState(false)
  const running = !!data?.running || started
  useEffect(() => {
    if (!running) return
    const t = setInterval(async () => {
      const s = await api.backups().catch(() => null)
      if (s && !s.running) {
        setStarted(false)
        reload()
      }
    }, 3000)
    return () => clearInterval(t)
  }, [running, reload])
  const failedLast = data?.last_error && (!data.last_ok || data.last_error.at > data.last_ok.at)
  return (
    <Card
      title="Backups"
      action={
        <a href="/api/admin/export-all" className="text-sm font-medium text-navy underline" title="Everything LedgerHawk holds, as one .tar.gz">
          Download all data
        </a>
      }
    >
      <ErrorNote error={error || err} />
      {!data && !error && <Loading />}
      {data && !data.configured && (
        <p className="text-sm text-amber-800">
          No backup bucket is set up, so imports, decisions and lookups live only on the server’s disk. Set the LEDGERHAWK_BACKUP_* settings on the
          server (a Cloudflare R2 or S3 bucket) to back up every night, and use “Download all data” in the meantime.
        </p>
      )}
      {data?.configured && (
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className={failedLast ? 'text-crimson' : 'text-slate-700'}>
              {data.last_ok
                ? `Last backup ${when(data.last_ok.at)} (${mb(data.last_ok.bytes)}, ${data.last_ok.files} files, by ${data.last_ok.by})`
                : 'No backup yet'}
            </span>
            <Button
              variant="secondary"
              disabled={running}
              onClick={async () => {
                setErr(null)
                try {
                  await api.backupNow()
                  setStarted(true)
                } catch (e) {
                  setErr((e as Error).message)
                }
              }}
            >
              {running ? 'Backing up…' : 'Back up now'}
            </Button>
          </div>
          {failedLast && data.last_error && (
            <p className="text-crimson">
              The last backup failed ({when(data.last_error.at)}): {data.last_error.error}
            </p>
          )}
          <p className="text-xs text-slate-500">
            Every night after {String(data.hour_utc).padStart(2, '0')}:00 UTC (skipped while an import runs, then tried the next hour) to the{' '}
            {data.bucket} bucket; the newest {data.keep} are kept.
          </p>
          {data.remote_error && <p className="text-crimson">{data.remote_error}</p>}
          {data.remote.length > 0 && (
            <ul className="divide-y divide-slate-100 text-xs text-slate-600">
              {data.remote.map((o) => (
                <li key={o.name} className="flex justify-between py-1.5">
                  <span className="tabular">{o.name}</span>
                  <span className="tabular">{mb(o.bytes)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  )
}
