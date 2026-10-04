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
  certs?: string[]
  in_sam?: boolean
  disposition: Disposition | null
  tier: string
  tier_default: string
  tier_change: TierChange | null
  owner: string
  owner_suggested: string
  owner_set: { owner: string; analyst: string; at: string } | null
  assignee: string
  last_touched: string | null
  integrity?: Integrity | null
}

export type Integrity = { tier: string; excluded: boolean; excluded_on: string; agency: string; reasons: string[]; second: string[]; after_exclusion: number }

export type IntegrityView = {
  funnel: { key: string; label: string; vendors: number; dollars: number }[]
  tiers: { tier: string; label: string; meaning: string; vendors: number; dollars: number; after_exclusion: number }[]
  gaps: { agency: string; A: number; B: number; C: number; excluded: number; after_exclusion: number; dollars: number; summary: string }[]
  rows: VendorRow[]
}

export type TierChange = { tier: string; prior: string; reason: string; analyst: string; at: string }

export type TierRollup = {
  tiers: { tier: string; label: string; meaning: string; vendors: number; dollars: number; fy25: number }[]
  assignees: Record<string, number>
  dispositions: Record<string, number>
}

export type Poc = { role: string; name: string; title: string; city: string; state: string; pkey: string; universe: number }

export type SamCard = {
  legal_name: string
  dba: string
  cage: string
  active: boolean
  reg_date: string
  exp_date: string
  last_update: string
  start_date: string
  certs: string[]
  naics: string
  address: string
  city: string
  state: string
  akey: string
  bkey: string
  suite_count: number
  bldg_count: number
  residential: boolean
  virtual: boolean
  pocs: Poc[]
}

export type LinkedVendor = { uei: string; name: string; via: string; same_suite: boolean; certified: boolean; lane: string; tot: number }

export type GraphNode = {
  id: string
  kind: 'vendor' | 'person' | 'suite' | 'building' | 'excluded'
  label: string
  uei?: string
  lane?: string
  queue?: string
  excluded?: boolean
  center?: boolean
  kept_pair?: boolean
  hub?: boolean
  universe?: number
  note?: string
  agency?: string
  type?: string
  active_date?: string
  scope?: string
  tot?: number
}
export type GraphEdge = { source: string; target: string; kind: string; label: string }
export type Graph = { nodes: GraphNode[]; edges: GraphEdge[]; paths_to_excluded: { to: string; agency: string; hops: number | null }[] }

export type Source = {
  id: string
  kind: 'sam' | 'exclusions'
  label: string
  as_of: string
  file: string
  sha256: string
  bytes: number
  uploaded_by: string
  uploaded_at: string
  age_days: number
  stale: boolean
  stale_after_days: number
}

export type GapGroup = {
  agency: string
  vendors: { uei: string; name: string; tot: number; lane: string; tie: string; excluded_party: string; type: string; active_date: string; evidence: string }[]
}

export type ExclusionHit = {
  kind: 'direct' | 'name_match' | 'alias' | 'address' | 'person'
  support?: string
  evidence?: string
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
  sam: SamCard | null
  links: LinkedVendor[]
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
  sam_source?: string | null
  sam_date?: string | null
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
  tiers: Record<string, string>
  tier_meaning: Record<string, string>
  owners: string[]
}

export type SubjectEntity = {
  uei: string
  name: string
  fy24: number
  fy25: number
  tot: number
  in_dollars_run: boolean
  sam: SamCard | null
  exclusion: ExclusionHit[]
  exclusion_flags: string[]
  signals: Signal[]
}

export type RelatedEntity = { uei: string; name: string; via: string[]; of: string[]; excluded: boolean; flags: string[]; exclusion: ExclusionHit[]; tot: number }

export type SubjectResult = {
  ref: number
  input_uei: string
  input_name: string
  role: string
  resolution: string
  status: string
  status_label: string
  entities: SubjectEntity[]
  related: RelatedEntity[]
  related_total: number
  findings: string[]
  next_steps: string[]
}

export type SubjectScreenMeta = {
  id: string
  created_at: string
  created_by: string
  matter: string
  client: string
  privileged: boolean
  data_class: string
  dollars_run: string | null
}

export type SubjectScreenSources = {
  sam_file: string | null
  sam_extract_date: string | null
  sam_sha256: string | null
  exclusions_file: string | null
  exclusions_extract_date: string | null
  rule_set_version: string
}

export type SubjectScreen = {
  meta: SubjectScreenMeta
  sources: SubjectScreenSources
  counts: Record<string, number>
  subjects: SubjectResult[]
}

export type SubjectScreenListItem = SubjectScreenMeta & { counts: Record<string, number>; sources: SubjectScreenSources }

export const SUBJECT_STATUS: Record<string, string> = {
  excluded: 'Excluded',
  tied: 'Tied to an excluded party',
  related_excluded: 'A related entity is excluded',
  name_only: 'Same name as an excluded party (unconfirmed)',
  signals: 'Other signals to review',
  registration: 'Registration question',
  clear: 'No hits in these sources',
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
  histogram: (id: string) => req<{ signals: Record<string, number>; combinations: Record<string, number> }>(`/api/runs/${id}/signal-histogram`),
  vendor: (id: string, uei: string) => req<VendorDetail>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}`),
  setDisposition: (id: string, uei: string, body: { value: string; note: string; analyst: string }) =>
    req<Disposition>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/disposition`, json(body)),
  restore: (id: string, uei: string, body: { note: string; analyst: string }) =>
    req<{ id: string }>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/restore`, json(body)),
  audit: () => req<HistoryItem[]>('/api/audit'),
  sources: () => req<{ sources: Source[] }>('/api/sources'),
  addSource: (form: FormData) => req<Source>('/api/sources', { method: 'POST', body: form }),
  graph: (id: string, uei: string) => req<Graph>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/graph`),
  gaps: (id: string) => req<GapGroup[]>(`/api/runs/${id}/exclusion-gaps`),
  tierRollup: (id: string) => req<TierRollup>(`/api/runs/${id}/tier-rollup`),
  setTier: (id: string, uei: string, body: { tier: string; reason: string; analyst: string }) =>
    req<TierChange>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/tier`, json(body)),
  setRouting: (id: string, uei: string, body: { owner: string; analyst: string }) =>
    req<unknown>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/routing`, json(body)),
  integrity: (id: string) => req<IntegrityView>(`/api/runs/${id}/integrity`),
  subjectScreens: () => req<SubjectScreenListItem[]>('/api/subject-screens'),
  subjectScreen: (id: string) => req<SubjectScreen>(`/api/subject-screens/${encodeURIComponent(id)}`),
  createSubjectScreen: (form: FormData) => req<{ id: string }>('/api/subject-screens', { method: 'POST', body: form }),
  assign: (id: string, body: { ueis: string[]; assignee: string; analyst: string }) => req<{ assigned: number }>(`/api/runs/${id}/assign`, json(body)),
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
  relationship: 'Relationship screen',
  strong: 'Strong single signal',
  exclusion: 'Exclusion-linked',
  integrity: 'Integrity lane',
  watch: 'Watch (deferred)',
}

export const FLAG_LABEL: Record<string, string> = {
  EXCLUDED: 'Excluded',
  ALIAS_MATCH: 'Alias of excluded party',
  SITE_UEI_QUESTION: 'Same name as excluded UEI',
  STALE_PENDING: 'Pending over 12 months',
  NAME_MATCH_CANDIDATE: 'Name-only candidate',
  NAME_MATCH_SUPPORTED: 'Name match, same area',
  R_EXADDR: 'Shares suite with excluded party',
  R_EXPOC: 'Shares contact with excluded party',
}

export const queueTotal = (q: QueueCounts) => (q.priority ?? 0) + (q.relationship ?? 0) + (q.strong ?? 0) + (q.exclusion ?? 0) + (q.integrity_leads ?? 0)

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
