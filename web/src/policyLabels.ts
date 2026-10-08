import type { PolicyChange } from './api'

// Plain-English names for the rule-set settings, grouped the way an analyst thinks about them. The sentence editor
// (next step) builds on these.

export type SettingKind = 'money' | 'ratio' | 'count' | 'days' | 'date' | 'list' | 'text' | 'bool'
export type Setting = { label: string; kind: SettingKind; group: string }

const g1 = 'Who is screened'
const g2 = 'What gets flagged'
const g3 = 'Strong signals'
const g4 = 'SAM registration checks'
const g5 = 'Links between firms'
const g6 = 'Exclusion matching'
const g7 = 'Lists'

export const SETTINGS: Record<string, Setting> = {
  version: { label: 'Rule set version', kind: 'text', group: g1 },
  immaterial_total: { label: 'Leave out vendors with FY24 + FY25 total under', kind: 'money', group: g1 },
  major_total: { label: 'Treat as an established major when the total is at least', kind: 'money', group: g1 },
  major_each_year: { label: '…and each year is at least', kind: 'money', group: g1 },
  s1_min: { label: 'Re-formed successor: one registration had at least', kind: 'money', group: g2 },
  s1_fade_ratio: { label: 'Re-formed successor: the other year at most this share of it', kind: 'ratio', group: g2 },
  s2_fy25_min: { label: 'New-entrant spike: nothing in FY24, and FY25 at least', kind: 'money', group: g2 },
  s3_fy24_min: { label: 'Hypergrowth: FY24 at least', kind: 'money', group: g2 },
  s3_fy25_min: { label: 'Hypergrowth: FY25 at least', kind: 'money', group: g2 },
  s3_ratio: { label: 'Hypergrowth: FY25 at least this many times FY24', kind: 'count', group: g2 },
  s4_total: { label: 'Sole proprietor, large dollars: total at least', kind: 'money', group: g2 },
  s4_total_weapons: { label: 'Sole proprietor in weapons, vehicles or aircraft: total at least', kind: 'money', group: g2 },
  s5_total: { label: 'Line-of-business mismatch: total at least', kind: 'money', group: g2 },
  s6_deob: { label: 'Large deobligation: FY25 negative by at least', kind: 'money', group: g2 },
  s6_share: { label: 'Large deobligation: at least this share of FY24', kind: 'ratio', group: g2 },
  strong_s2_fy25: { label: 'A spike alone is strong when FY25 is at least', kind: 'money', group: g3 },
  strong_s3_ratio: { label: 'Hypergrowth alone is strong at this many times FY24', kind: 'count', group: g3 },
  strong_s3_fy25: { label: '…with FY25 at least', kind: 'money', group: g3 },
  strong_s4_total: { label: 'A sole proprietor alone is strong at a total of', kind: 'money', group: g3 },
  split_cert_alone: { label: 'A certified firm split across UEIs is enough on its own', kind: 'bool', group: g3 },
  split_cert_alone_min: { label: '…when the family’s total is at least', kind: 'money', group: g3 },
  r_young_start: { label: 'Young registration: started on or after', kind: 'date', group: g4 },
  r_young_min: { label: 'Young registration: total at least', kind: 'money', group: g4 },
  r_split_cert_min: { label: 'Certified firm split across UEIs: family total at least', kind: 'money', group: g4 },
  sam_stale_days: { label: 'Warn when the SAM extract is older than', kind: 'days', group: g4 },
  exclusions_stale_days: { label: 'Warn when the exclusions extract is older than', kind: 'days', group: g4 },
  hub_cap: { label: 'A contact or address shared by more registrations than this is a hub, not a link', kind: 'count', group: g5 },
  person_vendor_cap: { label: 'Ignore a contact linking more vendors than', kind: 'count', group: g5 },
  l_min: { label: 'Linked successor: one firm had at least', kind: 'money', group: g5 },
  l_fade_ratio: { label: 'Linked successor: the other at most this share of it', kind: 'ratio', group: g5 },
  name_match_min_len: { label: 'Match a name to an excluded firm only if it has at least this many characters', kind: 'count', group: g6 },
  stale_pending_days: { label: 'Treat a pending exclusion as stale after', kind: 'days', group: g6 },
  majors: { label: 'Major contractors (set aside)', kind: 'list', group: g7 },
  jv_patterns: { label: 'Joint-venture name patterns', kind: 'list', group: g7 },
  tribal_patterns: { label: 'Tribal enterprise name patterns', kind: 'list', group: g7 },
  qio_patterns: { label: 'Quality Improvement Organization patterns', kind: 'list', group: g7 },
  dialysis_patterns: { label: 'Dialysis provider patterns', kind: 'list', group: g7 },
  foreign_suffixes: { label: 'Foreign company suffixes', kind: 'list', group: g7 },
  foreign_words: { label: 'Foreign company words', kind: 'list', group: g7 },
  noncommercial_structs: { label: 'Non-commercial entity types (set aside)', kind: 'list', group: g7 },
  s4_psc_prefixes: { label: 'Weapons, vehicles and aircraft PSC prefixes', kind: 'list', group: g7 },
  s5_naics2: { label: 'Line-of-business mismatch: NAICS sectors', kind: 'list', group: g7 },
  s5_psc_prefixes: { label: 'Line-of-business mismatch: PSC prefixes', kind: 'list', group: g7 },
  stem_stop_words: { label: 'Words ignored when comparing firm names', kind: 'list', group: g7 },
}

export function settingLabel(key: string): string {
  return SETTINGS[key]?.label ?? key.replace(/_/g, ' ')
}

export function formatSetting(key: string, v: unknown): string {
  const kind = SETTINGS[key]?.kind
  if (Array.isArray(v)) return `${v.length} ${v.length === 1 ? 'entry' : 'entries'}`
  if (typeof v === 'boolean') return v ? 'on' : 'off'
  if (typeof v === 'number') {
    if (kind === 'money') return v >= 1e6 ? `$${(v / 1e6).toLocaleString()}M` : `$${v.toLocaleString()}`
    if (kind === 'ratio') return `${Math.round(v * 100)}%`
    if (kind === 'days') return `${v} days`
    if (key.endsWith('s3_ratio')) return `${v}×`
    return v.toLocaleString()
  }
  return String(v ?? '')
}

/** One change, in words: "Leave out vendors with … under: $250,000 → $2M". */
export function changeText(c: PolicyChange): string {
  if (c.added || c.removed)
    return `${settingLabel(c.key)}: ${[
      c.added?.length ? `added ${c.added.join(', ')}` : '',
      c.removed?.length ? `removed ${c.removed.join(', ')}` : '',
    ]
      .filter(Boolean)
      .join('; ')}`
  return `${settingLabel(c.key)}: ${formatSetting(c.key, c.from)} → ${formatSetting(c.key, c.to)}`
}
