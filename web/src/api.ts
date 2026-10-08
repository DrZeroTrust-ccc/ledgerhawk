export type Signal = { id: string; label: string; detail: string }

/** A run that an earlier decision was made in. */
export type RunRef = { id: string; label: string; created_at: string }

export type Disposition = { value: string; note: string; analyst: string; at: string; run_id?: string; carried_from?: RunRef }

export type VendorColor = 'red' | 'yellow' | 'green' | ''
export type VendorRow = {
  uei: string
  name: string
  /** Red, yellow or green, as in the export for analysis; empty when none applies. */
  color?: VendorColor
  color_why?: string[]
  queue: string
  bucket: string
  lane: string
  reason_code: string
  reason: string
  headline?: string
  /** One-line reason written by the Hawk (AI), when someone asked for it on this run. */
  hawk?: string
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

export type TierChange = { tier: string; prior: string; reason: string; analyst: string; at: string; carried_from?: RunRef }

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
  /** Ownership from SAM's business types, e.g. "Alaska Native Corporation owned"; imports before Oct 2026 lack it. */
  owner?: string[]
  business_types?: string[]
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
  /** Vendors: obligations by fiscal year ("2024": dollars), from the run file and any USAspending lookup. */
  money?: Record<string, number>
  in_run?: boolean
  source?: string
}
/** An import running on the server in the background (new, follow-up or restore). */
export type ImportJob = {
  id: string
  kind: 'new' | 'follow_up' | 'restore'
  label: string
  by: string
  state: 'queued' | 'running' | 'done' | 'error'
  step: string
  started_at: string
  finished_at: string
  run_id: string
  error: string
}
export type ImportStart = { id: string | null; job: ImportJob }

/** Who is signed in. auth "open": sign-in is off and analysts type their name. */
export type Me =
  | { auth: 'open' }
  | { auth: 'access'; email: string; name: string; role: 'admin' | 'analyst' | 'executive' | null; role_label: string; bootstrap: boolean }
/** The policy pack version an import was screened under. */
export type PolicyRef = { pack_id: string; pack_name: string; version: number; fingerprint: string }
export type PolicyChange = { key: string; from?: unknown; to?: unknown; added?: string[]; removed?: string[] }
export type PolicyImpact = {
  import: RunRef
  leads: [number, number]
  dollars: [number, number]
  moves: number
  tier_moves: number
  conflicts: number
  workload: Workload
  triage_only: boolean
}
export type PolicyVersion = {
  n: number
  status: 'live' | 'retired' | 'draft'
  fingerprint: string
  created_by: string
  updated_by?: string
  approved_by: string
  approved_at?: string
  approval_comment?: string
  impact?: PolicyImpact
  submitted_by?: string
  submitted_at?: string
  returned?: { by: string; at: string; comment: string } | null
  at: string
  reason: string
  changes: PolicyChange[]
}
export type PolicyRecent = {
  pack_id: string
  pack_name: string
  n: number
  approved_by: string
  approved_at: string
  created_by: string
  reason: string
  approval_comment: string
  changes: PolicyChange[]
  impact: PolicyImpact
}
export type PolicyPreview = {
  fingerprint: string
  state: 'running' | 'done' | 'error'
  step: string
  by: string
  started_at: string
  finished_at: string
  error: string
  result:
    | (Omit<PolicyEstimate, 'unestimated'> & {
        at: string
        tier_moves: { uei: string; name: string; tot: number; queue: string; from: string; to: string }[]
        tier_moves_total: number
      })
    | null
}
export type PolicyPack = {
  id: string
  name: string
  description: string
  locked: boolean
  created_by: string
  created_at: string
  copied_from?: { pack_id: string; pack_name: string; version: number }
  live: number | null
  versions: PolicyVersion[]
  imports: number
}
export type MustCatch = { uei: string; name: string; reason: string; added_by: string; at: string }
/** A pinned vendor under a draft vs the live rules. Only "dropped" blocks a deploy. */
export type MustCatchStatus = 'kept' | 'dropped' | 'added' | 'missed' | 'absent'
export const MUST_CATCH_LABEL: Record<MustCatchStatus, string> = {
  kept: 'still flagged',
  dropped: 'would be dropped',
  added: 'would now be flagged',
  missed: 'not flagged by the screen today, nor by this draft',
  absent: 'not in this import',
}
export type Workload = { hours_per_lead: number; analysts: number; set: boolean }
export type PolicyDetail = Omit<PolicyPack, 'imports'> & {
  live_rules: Record<string, unknown>
  vs_defaults: PolicyChange[]
  imports: { id: string; label: string; created_at: string; version: number }[]
  draft: (PolicyVersion & { rules: Record<string, unknown>; updated_by?: string }) | null
  must_catch: MustCatch[]
  workload: Workload
}
export type PolicyMove = { uei: string; name: string; fy24: number; fy25: number; tot: number; kind: 'in' | 'out' | 'moved'; from: string; to: string; because: string }
type QueueTally = { leads: number; dollars: number; by_queue: Record<string, number> }
export type PolicyEstimate = {
  import: RunRef
  live: QueueTally
  draft: QueueTally
  moves: PolicyMove[]
  moves_total: number
  conflicts: (PolicyMove & { decision: string; decided_by: string })[]
  must_catch: (MustCatch & { status: MustCatchStatus })[]
  changes: PolicyChange[]
  unestimated: string[]
  workload: Workload
}

export type Person = { email: string; name: string; role: 'admin' | 'analyst' | 'executive'; added_by: string; at: string }
export type People = { people: Person[]; roles: Record<string, string>; bootstrap: { email: string; name: string }[] }

export type GraphEdge = { source: string; target: string; kind: string; label: string }
export type Graph = {
  nodes: GraphNode[]
  edges: GraphEdge[]
  paths_to_excluded: { to: string; agency: string; hops: number | null }[]
  years?: string[]
}

type AutoKind = { checked_at?: string; fetched_at?: string; as_of?: string; error?: string }
/** Automatic downloads from SAM.gov: on when the server has a SAM_API_KEY. */
export type AutoSources = { enabled: boolean; running: boolean; sam?: AutoKind; exclusions?: AutoKind }

export type Source = {
  id: string
  kind: 'sam' | 'exclusions'
  label: string
  as_of: string
  file: string
  sha256: string
  bytes: number
  entities?: number
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
  kind: 'direct' | 'name_match' | 'alias' | 'address' | 'person' | 'jv_partner'
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

export type HistoryItem = { at: string; analyst: string; action: string; uei: string | null; run_id: string | null; detail: string; other_run?: RunRef }

export type VendorDetail = VendorRow & {
  struct: string
  naics: string
  psc: string
  why: string
  exclusion: ExclusionHit[]
  history: HistoryItem[]
  case: { review: ScreenReview; earlier_notes: CaseNote[]; awards: ScreenAwards | null; summary: CaseSummary | null; earlier_summary: (CaseSummary & { run: RunRef }) | null }
  ledger: Ledger
  summary_enabled: boolean
  sam: SamCard | null
  links: LinkedVendor[]
  screens: VendorScreen[]
}

// What a subject screen found about one vendor, for the vendor record.
export type VendorScreen = {
  id: string
  matter: string
  created_at: string
  ref: number
  role: 'subject' | 'related'
  subject: string
  // as a subject
  status?: string
  status_label?: string
  findings?: string[]
  next_steps?: string[]
  related?: { uei: string; name: string; via: string[]; excluded: boolean }[]
  related_total?: number
  awards?: {
    fetched_at: string
    by_uei: { uei: string; name: string; role: string; by_fy: Record<string, number> }[]
    growth: string
    anomalies: string[]
    actions_summary: string
    shift: string
  } | null
  // as a firm related to another subject
  via?: string[]
  excluded?: boolean
}

export type VendorWhere = {
  uei: string
  name: string
  runs: { id: string; label: string; created_at: string; data_class: string }[]
  screens: VendorScreen[]
}

export type VendorSearchRow = {
  uei: string
  name: string
  run: { id: string; label: string; created_at: string } | null
  runs: number
  screens: number
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
  follows_id?: string | null
  app_version?: string
  policy?: PolicyRef
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
  follows: RunRef | null
  followed_by: RunRef[]
  changes: RunChanges | null
}

export type ChangeItem = { uei: string; name: string; tot: number; why?: string; what?: ChangeWhat[] }
export type ChangeWhat =
  | { kind: 'flags'; added: string[] }
  | { kind: 'signals'; added: string[] }
  | { kind: 'signals_gone'; removed: string[] }
  | { kind: 'tier'; from: string; to: string }
  | { kind: 'dollars'; from: number; to: number }
export type RunChanges = { new: ChangeItem[]; dropped: ChangeItem[]; changed: ChangeItem[]; counts: { new: number; dropped: number; changed: number } }

export type QueueProgress = {
  total: number
  open: number
  decided: number
  carried: number
  decided_today: number
  mine_today: number
  assigned_to_me_open: number
}

export type RunRecord = {
  meta: RunMeta
  inputs: { role: string; file: string; sha256: string | null; as_of: string | null }[]
  data_class: string
  rule_set: { version: string; fingerprint: string }
  policy: PolicyRef
  pipeline_version: string
  app_version: string
  restored: string[]
  restored_from: RunRef[]
  follows: RunRef | null
  followed_by: RunRef[]
  changes: RunChanges['counts'] | null
  queue: { total: number; dollars: number; decided: number; carried: number; open: number; by_value: Record<string, number> }
  log: HistoryItem[]
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
  dollars_from?: 'run' | 'list' | ''
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
  parent_id?: string | null
  original_matter?: string
  original_client?: string
  renamed_by?: string
  renamed_at?: string
}

export type JobKind = 'awards' | 'context' | 'recheck'

export type ScreenJob = {
  kind: JobKind
  label: string
  state: 'running' | 'done' | 'error'
  done: number
  total: number
  step: string
  by: string
  started_at: string
  finished_at: string
  error: string
  result: Record<string, string | number> | null
}

export type ScreenJobs = Partial<Record<JobKind, ScreenJob>>

export type SubjectChange = {
  ref: number
  name: string
  status_before: string
  status_before_label: string
  status_now: string
  status_now_label: string
  direction: 'worse' | 'better' | 'same'
  added: string[]
  removed: string[]
}

export type ChangeCounts = { changed: number; worse: number; better: number; unchanged: number }

export type SubjectChanges = {
  parent_id: string
  parent_created_at: string
  parent_sources: SubjectScreenSources
  subjects: SubjectChange[]
  people?: SubjectChange[]
  counts: ChangeCounts
}

export type SubjectScreenSources = {
  sam_file: string | null
  sam_extract_date: string | null
  sam_sha256: string | null
  exclusions_file: string | null
  exclusions_extract_date: string | null
  rule_set_version: string
}

export type PersonResult = {
  ref: number
  input: string
  first: string
  last: string
  state: string
  status: string
  status_label: string
  common: boolean
  registrations: { uei: string; name: string; roles: string[]; place: string; active: boolean; excluded: boolean }[]
  registrations_total: number
  exclusions: { name: string; agency: string; type: string; active_date: string; city: string; state: string; support: string }[]
  findings: string[]
  next_steps: string[]
}

export type SubjectScreen = {
  meta: SubjectScreenMeta
  jobs?: ScreenJobs
  sources: SubjectScreenSources
  counts: Record<string, number>
  subjects: SubjectResult[]
  people?: PersonResult[]
  changes?: SubjectChanges
  review: ScreenReview
  awards: ScreenAwards | null
  context: ScreenContext | null
}

export type Award = {
  group: 'contract' | 'idv'
  award_id: string
  amount: number
  description: string
  agency: string
  sub_agency: string
  start: string
  end: string
  type: string
  url: string
  after_exclusion: boolean
}

export type AwardAction = {
  award_id: string
  mod: string
  date: string
  kind: string
  label: string
  amount: number
  agency: string
  schedule: boolean
  flagged: boolean
  url: string
}

export type AwardEntity = {
  by_fy?: Record<string, number>
  lifetime?: number
  growth?: string
  anomalies?: string[]
  history_error?: string
  actions?: AwardAction[]
  actions_flagged?: number
  actions_summary?: string
  actions_error?: string
  schedule_actions?: number
  uei: string
  name: string
  refs: number[]
  role: string
  excluded_since: string
  awards: Award[]
  truncated: boolean
  error: string
  total: number
  count: number
  after_exclusion: number
  agencies: string[]
  first: string
  last: string
}

export type ContextItem = {
  source: string
  title: string
  url: string
  date: string
  where: string
  snippet: string
  match: string
  tags: string[]
  id: string
  confidence: 'strong' | 'possible' | 'weak'
  score: number
  why: string[]
  query?: string
  verdict: { verdict: 'same' | 'not' | 'unsure'; note: string; by: string; at: string; muted?: boolean } | null
}

export type ContextTally = { confirmed: number; strong: number; possible: number; weak: number; dismissed: number; unsure: number }

export type OutsideContext = {
  name: string
  uei: string
  query: string
  person: boolean
  fetched_at: string
  fetched_by?: string
  sources: Record<string, { items: ContextItem[]; error: string }>
  labels: Record<string, string>
  count: number
  adverse: number
  errors: number
  manual: { label: string; url: string }[]
  tally: ContextTally
  generic: boolean
  web_search?: boolean
  keys?: { opensanctions: boolean; opencorporates: boolean; smarty: boolean }
  checks?: { website?: WebsiteCheck; address?: AddressCheck }
  ref?: number | null
  person_ref?: number | null
}

export type CheckFinding = { lean: 'strengthens' | 'weakens' | 'context'; text: string }
export type WebsiteCheck = {
  domain: string
  url: string
  registered: string
  first_capture: string
  last_capture: string
  captures: number | null
  errors: string[]
  findings: CheckFinding[]
}
export type AddressCheck = { address: string; rdi: string; cmra: boolean; vacant: boolean; deliverable: boolean | null; errors: string[]; findings: CheckFinding[] }

export type ScreenContext = { fetched_at: string; fetched_by: string; entities: OutsideContext[] }

export type ScreenAwards = {
  fetched_at: string
  fetched_by: string
  entities: AwardEntity[]
  skipped: number
  errors: number
  shifts?: Record<string, string>
}

export type ScreenNote = {
  id: string
  target: string
  text: string
  source: string
  analyst: string
  at: string
  file?: string
  file_bytes?: number
  file_sha256?: string
  carried_from?: { id: string; created_at: string }
}

export type ReviewState = 'draft' | 'submitted' | 'returned' | 'approved'

export type CaseNote = ScreenNote & { lean?: string; run?: RunRef }
export type SummaryLine = { text: string; sources: string[] }
export type CaseSummary = {
  sentences: SummaryLine[]
  next_steps: SummaryLine[]
  model: string
  drafted_at: string
  requested_by: string
  edited_by: string
  edited_at: string
  cited: Record<string, string>
  stale?: boolean
}

export type LedgerRow = { id: string; kind: string; lean: 'strengthens' | 'weakens' | 'context'; text: string; source: string; link: string; at: string; by: string; weight: number }
export type Ledger = {
  rows: LedgerRow[]
  balance: { for: number; against: number; lean: 'strengthens' | 'weakens' | 'mixed' | 'none'; counts: Record<'strengthens' | 'weakens' | 'context', number> }
}

export type ScreenReview = {
  state: ReviewState
  state_label: string
  notes: CaseNote[]
  history: { action: 'submit' | 'approve' | 'return' | 'reopen'; state: ReviewState; by: string; at: string; comment: string }[]
}

export type SubjectScreenListItem = SubjectScreenMeta & {
  counts: Record<string, number>
  sources: SubjectScreenSources
  change_counts: ChangeCounts | null
  review_state: ReviewState
}

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

export type HawkReasons = {
  state: 'none' | 'running' | 'done' | 'error'
  done?: number
  total?: number
  written: number
  elapsed_s: number
  by?: string
  error?: string
  enabled: boolean
}

export const api = {
  meta: () => req<Meta>('/api/meta'),
  me: () => req<Me>('/api/me'),
  people: () => req<People>('/api/people'),
  savePerson: (form: FormData) => req<Person>('/api/people', { method: 'POST', body: form }),
  removePerson: (form: FormData) => req<{ ok: boolean }>('/api/people/remove', { method: 'POST', body: form }),
  policies: () => req<{ packs: PolicyPack[]; recent: PolicyRecent[] }>('/api/policies'),
  policyPreview: (id: string) => req<{ preview: PolicyPreview | null }>(`/api/policies/${encodeURIComponent(id)}/preview`),
  startPolicyPreview: (id: string, form: FormData) =>
    req<{ preview: PolicyPreview | null }>(`/api/policies/${encodeURIComponent(id)}/preview`, { method: 'POST', body: form }),
  submitPolicy: (id: string, form: FormData) => req<PolicyVersion>(`/api/policies/${encodeURIComponent(id)}/submit`, { method: 'POST', body: form }),
  returnPolicy: (id: string, form: FormData) => req<PolicyVersion>(`/api/policies/${encodeURIComponent(id)}/return`, { method: 'POST', body: form }),
  deployPolicy: (id: string, form: FormData) =>
    req<{ version: PolicyVersion; follow_up: string | null; follow_up_job: ImportJob | null; follow_up_error?: string }>(`/api/policies/${encodeURIComponent(id)}/deploy`, {
      method: 'POST',
      body: form,
    }),
  rollbackPolicy: (id: string, form: FormData) =>
    req<PolicyVersion>(`/api/policies/${encodeURIComponent(id)}/rollback`, { method: 'POST', body: form }),
  policy: (id: string) => req<PolicyDetail>(`/api/policies/${encodeURIComponent(id)}`),
  createPolicy: (form: FormData) => req<PolicyPack>('/api/policies', { method: 'POST', body: form }),
  savePolicyDraft: (id: string, body: { rules: Record<string, unknown>; reason: string; analyst: string }) =>
    req<PolicyVersion>(`/api/policies/${encodeURIComponent(id)}/draft`, json(body)),
  discardPolicyDraft: (id: string, form: FormData) =>
    req<{ ok: boolean }>(`/api/policies/${encodeURIComponent(id)}/draft/discard`, { method: 'POST', body: form }),
  policyEstimate: (id: string, body: { rules: Record<string, unknown>; import_id?: string }) =>
    req<PolicyEstimate>(`/api/policies/${encodeURIComponent(id)}/estimate`, json(body)),
  policySensitivity: (id: string, body: { rules: Record<string, unknown>; key: string; values: number[] }) =>
    req<{ import: RunRef; key: string; points: { value: number; leads: number; dollars: number }[] }>(
      `/api/policies/${encodeURIComponent(id)}/sensitivity`,
      json(body),
    ),
  addMustCatch: (id: string, form: FormData) =>
    req<{ must_catch: MustCatch[] }>(`/api/policies/${encodeURIComponent(id)}/must-catch`, { method: 'POST', body: form }),
  removeMustCatch: (id: string, form: FormData) =>
    req<{ must_catch: MustCatch[] }>(`/api/policies/${encodeURIComponent(id)}/must-catch/remove`, { method: 'POST', body: form }),
  setWorkload: (id: string, form: FormData) => req<Workload>(`/api/policies/${encodeURIComponent(id)}/workload`, { method: 'POST', body: form }),
  describePolicy: (id: string, form: FormData) =>
    req<PolicyPack>(`/api/policies/${encodeURIComponent(id)}/describe`, { method: 'POST', body: form }),
  runs: () => req<RunMeta[]>('/api/runs'),
  run: (id: string) => req<RunSummary>(`/api/runs/${id}`),
  createRun: (form: FormData) => req<ImportStart>('/api/runs', { method: 'POST', body: form }),
  importJobs: (active: boolean) => req<{ jobs: ImportJob[] }>(`/api/import-jobs${active ? '?active=true' : ''}`),
  importJob: (id: string) => req<ImportJob>(`/api/import-jobs/${encodeURIComponent(id)}`),
  vendors: (id: string, params: Record<string, string>) =>
    req<{ total: number; dollars: number; rows: VendorRow[] }>(`/api/runs/${id}/vendors?${new URLSearchParams(params)}`),
  histogram: (id: string) => req<{ signals: Record<string, number>; combinations: Record<string, number> }>(`/api/runs/${id}/signal-histogram`),
  vendor: (id: string, uei: string) => req<VendorDetail>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}`),
  setDisposition: (id: string, uei: string, body: { value: string; note: string; analyst: string }) =>
    req<Disposition>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/disposition`, json(body)),
  restore: (id: string, uei: string, body: { note: string; analyst: string }) =>
    req<ImportStart>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/restore`, json(body)),
  audit: () => req<HistoryItem[]>('/api/audit'),
  sources: () => req<{ sources: Source[]; auto?: AutoSources }>('/api/sources'),
  refreshSources: (form: FormData) => req<AutoSources>('/api/sources/refresh', { method: 'POST', body: form }),
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
  recheckSubjectScreen: (id: string, form: FormData) =>
    req<ScreenJob>(`/api/subject-screens/${encodeURIComponent(id)}/recheck`, { method: 'POST', body: form }),
  screenJobs: (id: string) => req<ScreenJobs>(`/api/subject-screens/${encodeURIComponent(id)}/jobs`),
  renameScreen: (id: string, form: FormData) =>
    req<SubjectScreenMeta>(`/api/subject-screens/${encodeURIComponent(id)}/rename`, { method: 'POST', body: form }),
  addScreenNote: (id: string, form: FormData) =>
    req<ScreenNote>(`/api/subject-screens/${encodeURIComponent(id)}/notes`, { method: 'POST', body: form }),
  deleteScreenNote: (id: string, nid: string, form: FormData) =>
    req<unknown>(`/api/subject-screens/${encodeURIComponent(id)}/notes/${nid}/delete`, { method: 'POST', body: form }),
  context: (q: { uei?: string; name?: string; person?: boolean }) =>
    req<{ context: OutsideContext | null }>(
      `/api/context?${new URLSearchParams(Object.entries(q).filter(([, v]) => v).map(([k, v]) => [k, String(v)]))}`,
    ),
  lookupContext: (form: FormData) => req<OutsideContext>('/api/context', { method: 'POST', body: form }),
  contextVerdict: (form: FormData) => req<OutsideContext>('/api/context/verdict', { method: 'POST', body: form }),
  muteSite: (form: FormData) =>
    req<{ sites: Record<string, { by: string; at: string; note: string }> }>('/api/context/muted-sites', { method: 'POST', body: form }),
  fetchScreenContext: (id: string, form: FormData) =>
    req<ScreenJob>(`/api/subject-screens/${encodeURIComponent(id)}/context`, { method: 'POST', body: form }),
  fetchScreenAwards: (id: string, form: FormData) =>
    req<ScreenJob>(`/api/subject-screens/${encodeURIComponent(id)}/awards`, { method: 'POST', body: form }),
  reviewScreen: (id: string, form: FormData) =>
    req<ScreenReview>(`/api/subject-screens/${encodeURIComponent(id)}/review`, { method: 'POST', body: form }),
  createSubjectScreen: (form: FormData) => req<{ id: string }>('/api/subject-screens', { method: 'POST', body: form }),
  caseNote: (id: string, uei: string, form: FormData) =>
    req<CaseNote>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/notes`, { method: 'POST', body: form }),
  deleteCaseNote: (id: string, uei: string, nid: string, form: FormData) =>
    req<unknown>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/notes/${nid}/delete`, { method: 'POST', body: form }),
  reviewCase: (id: string, uei: string, form: FormData) =>
    req<ScreenReview>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/review`, { method: 'POST', body: form }),
  caseAwards: (id: string, uei: string, form: FormData) =>
    req<ScreenAwards>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/awards`, { method: 'POST', body: form }),
  hawkReasons: (id: string) => req<HawkReasons>(`/api/runs/${id}/hawk-reasons`),
  startHawkReasons: (id: string, form: FormData) => req<HawkReasons>(`/api/runs/${id}/hawk-reasons`, { method: 'POST', body: form }),
  draftSummary: (id: string, uei: string, form: FormData) =>
    req<CaseSummary>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/summary/draft`, { method: 'POST', body: form }),
  saveSummary: (id: string, uei: string, body: { analyst: string; sentences: SummaryLine[]; next_steps: SummaryLine[] }) =>
    req<CaseSummary>(`/api/runs/${id}/vendors/${encodeURIComponent(uei)}/summary`, json(body)),
  bulkDisposition: (id: string, body: { ueis: string[]; value: string; note: string; analyst: string }) =>
    req<{ decided: number }>(`/api/runs/${id}/dispositions`, json(body)),
  vendorWhere: (uei: string) => req<VendorWhere>(`/api/vendors/${encodeURIComponent(uei)}`),
  searchVendors: (q: string) => req<{ rows: VendorSearchRow[] }>(`/api/vendors?${new URLSearchParams({ q })}`),
  bulkTier: (id: string, body: { ueis: string[]; tier: string; reason: string; analyst: string }) =>
    req<{ changed: number }>(`/api/runs/${id}/tiers`, json(body)),
  importDecisions: (id: string, form: FormData) =>
    req<DecisionImport>(`/api/runs/${id}/import-decisions`, { method: 'POST', body: form }),
  progress: (id: string, analyst: string) => req<QueueProgress>(`/api/runs/${id}/progress?${new URLSearchParams({ analyst })}`),
  myCases: (analyst: string) => req<{ rows: (VendorRow & { run: RunRef })[] }>(`/api/my-cases?${new URLSearchParams({ analyst })}`),
  record: (id: string) => req<RunRecord>(`/api/runs/${id}/record`),
  followUp: (id: string, form: FormData) => req<ImportStart>(`/api/runs/${id}/follow-up`, { method: 'POST', body: form }),
  confirmCarried: (id: string, body: { ueis: string[]; analyst: string }) =>
    req<{ confirmed: number }>(`/api/runs/${id}/confirm-carried`, json(body)),
  assign: (id: string, body: { ueis: string[]; assignee: string; analyst: string }) => req<{ assigned: number }>(`/api/runs/${id}/assign`, json(body)),
}

export type DecisionImport = {
  file: string
  rows: number
  changes: { uei: string; name: string; tier_from: string; tier_to: string; disposition_from: string; disposition_to: string; detail: string }[]
  unchanged: number
  unmatched: string[]
  problems: string[]
  applied: boolean
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
  JV_PARTNER_EXCLUDED: 'JV with excluded partner name',
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
