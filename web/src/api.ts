export type Signal = { id: string; label: string; detail: string }

export type Disposition = { value: string; note: string; analyst: string; at: string }

export type VendorRow = {
  uei: string
  name: string
  queue: string
  bucket: string
  lane: string
  reason_code: string
  reason: string
  cut_stage: string
  restored_from: string
  suppression: string
  fy24: number
  fy25: number
  tot: number
  signals: Signal[]
  exclusion_flags: string[]
  disposition: Disposition | null
}

export type ExclusionHit = {
  kind: 'direct' | 'name_match' | 'alias'
  support?: string
  name: string
  uei: string
  agency: string
  type: string
  program: string
  ct_code: string
  active_date: string
  termination_date: string
  city: string
  state: string
  comments: string
  scope: string
}

export type HistoryItem = { at: string; analyst: string; action: string; uei: string | null; run_id: string | null; detail: string }

export type VendorDetail = VendorRow & {
  struct: string
  naics: string
  psc: string
  why: string
  exclusion: ExclusionHit[]
  history: HistoryItem[]
}

export type FunnelStep = {
  key: string
  label: string
  vendors: number
  dollars: number
  cut: number
  cut_dollars: number
  reason_code: string
}

export type QueueCounts = Record<string, number>

export type RunMeta = {
  id: string
  created_at: string
  created_by: string
  parent_id: string | null
  label: string
  data_class: 'synthetic' | 'production'
  vendor_file: string
  exclusions_file: string | null
  exclusions_date: string | null
  restore: string[]
  funnel: FunnelStep[]
  queue_counts: QueueCounts
}

export type Validation = {
  file_name: string
  sha256: string
  rows: number
  total_dollars: number
  column_mapping: Record<string, string>
  missing_columns: string[]
  duplicate_ueis: number
  missing_ueis: number
  non_numeric_amounts: number
  negative_values: number
  warnings: string[]
}

export type RunSummary = {
  manifest: Record<string, unknown> & { thresholds: Record<string, number | string> }
  validation: Validation
  funnel: FunnelStep[]
  queue_counts: QueueCounts
  meta: RunMeta
}

export type Meta = {
  footer: string
  dispositions: string[]
  signals: Record<string, string>
  queues: Record<string, string>
  rule_set_version: string
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try {
      const body = await r.json()
      if (body.detail) msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* not JSON */
    }
    throw new Error(msg)
  }
  return r.json()
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  meta: () => req<Meta>('/api/meta'),
  runs: () => req<RunMeta[]>('/api/runs'),
  run: (id: string) => req<RunSummary>(`/api/runs/${id}`),
  createRun: (form: FormData) => req<{ id: string }>('/api/runs', { method: 'POST', body: form }),
  vendors: (id: string, params: Record<string, string>) =>
    req<{ total: number; dollars: number; rows: VendorRow[] }>(`/api/runs/${id}/vendors?${new URLSearchParams(params)}`),
  histogram: (id: string) =>
    req<{ signals: Record<string, number>; combinations: Record<string, number> }>(`/api/runs/${id}/signal-histogram`),
  vendor: (id: string, uei: string) => req<VendorDetail>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}`),
  setDisposition: (id: string, uei: string, body: { value: string; note: string; analyst: string }) =>
    req<Disposition>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/disposition`, json(body)),
  restore: (id: string, uei: string, body: { note: string; analyst: string }) =>
    req<{ id: string }>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/restore`, json(body)),
  audit: () => req<HistoryItem[]>('/api/audit'),
}

export function money(x: number): string {
  const neg = x < 0
  const v = Math.abs(x)
  let s: string
  if (v >= 1e12) s = `$${(v / 1e12).toFixed(2)}T`
  else if (v >= 1e9) s = `$${(v / 1e9).toFixed(2)}B`
  else if (v >= 1e6) s = `$${(v / 1e6).toFixed(1)}M`
  else if (v >= 1e3) s = `$${(v / 1e3).toFixed(0)}K`
  else s = `$${v.toFixed(0)}`
  return neg ? `-${s}` : s
}

export const num = (n: number) => n.toLocaleString('en-US')

export const QUEUE_LABEL: Record<string, string> = {
  priority: 'Priority',
  strong: 'Strong single signal',
  exclusion: 'Exclusion-linked',
  watch: 'Watch (deferred)',
}

export const FLAG_LABEL: Record<string, string> = {
  EXCLUDED: 'Excluded',
  ALIAS_MATCH: 'Alias of excluded party',
  SITE_UEI_QUESTION: 'Same name as excluded UEI',
  STALE_PENDING: 'Pending over 12 months',
  NAME_MATCH_CANDIDATE: 'Name-only candidate',
}

export const REASON_LABEL: Record<string, string> = {
  NONCOMMERCIAL: 'Not a commercial vendor',
  IMMATERIAL: 'Under $250K (integrity lane)',
  CLOSEOUT_NET: 'Closeout artifact',
  MAJOR_AUDITED: 'Major contractor, set aside',
  RESTORED: 'Restored by analyst',
}

export const LANE_LABEL: Record<string, string> = {
  outlier: 'Outlier pool',
  integrity: 'Integrity lane',
  closeout: 'Data-quality review',
  set_aside: 'Set aside (major)',
  noncommercial: 'Non-commercial',
}
