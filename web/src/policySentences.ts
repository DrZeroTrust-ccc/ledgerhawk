// Every rule as a sentence. `{key}` marks a setting the reader can click and change; the wording follows what the
// pipeline actually does (pipeline/stages.py, links.py, exclusions.py), so what you read is what it enforces.

export type Sentence = { text: string; note?: string; locked?: boolean }
export type SentenceGroup = { title: string; full?: boolean; sentences: Sentence[] }

export const SENTENCES: SentenceGroup[] = [
  {
    title: 'Who is screened',
    sentences: [
      { text: 'Leave out vendors whose FY24 + FY25 total is under {immaterial_total}; they go to the small-vendor integrity lane instead.' },
      {
        text: 'Set aside major contractors: any name on the {majors}, or any vendor with a total of at least {major_total}.',
        note: 'Set-aside vendors are not cleared, just not screened here. A set-aside is called "established" when each year is at least {major_each_year}.',
      },
      { text: 'Set aside {noncommercial_structs} as non-commercial.' },
      { text: 'Never flag joint ventures (names matching {jv_patterns}) as a re-formed successor, a spike or hypergrowth.' },
      {
        text: 'A spike or hypergrowth doesn’t count toward a lead for tribal enterprises ({tribal_patterns}), quality improvement organizations ({qio_patterns}), dialysis providers ({dialysis_patterns}), or foreign firms ({foreign_suffixes} and {foreign_words}).',
        note: 'Air charter carriers are treated the same way.',
      },
    ],
  },
  {
    title: 'What gets flagged',
    sentences: [
      {
        text: 'Flag a re-formed successor when two or three registrations share a name, one fades (from at least {s1_min} to at most {s1_fade_ratio} of that) as another rises the same way, and the legal form changed.',
      },
      { text: 'Flag a new-entrant spike when a vendor had nothing in FY24 and at least {s2_fy25_min} in FY25.' },
      { text: 'Flag hypergrowth when FY24 was at least {s3_fy24_min}, FY25 at least {s3_fy25_min}, and FY25 at least {s3_ratio} FY24.' },
      {
        text: 'Flag a sole proprietor with large dollars at a total of {s4_total}, or {s4_total_weapons} when paid under weapons, vehicle or aircraft codes ({s4_psc_prefixes}).',
      },
      {
        text: 'Flag a line-of-business mismatch when a vendor in one of {s5_naics2} is paid under one of {s5_psc_prefixes} and the total is at least {s5_total}.',
      },
      {
        text: 'Note a large deobligation when FY25 is negative by at least {s6_deob} and that is at least {s6_share} of FY24.',
        note: 'Context only: it never makes a lead on its own.',
      },
    ],
  },
  {
    title: 'When one signal is enough',
    sentences: [
      {
        text: 'Two or more signals put a vendor in the priority queue. One signal is enough on its own (the strong queue) for any re-formed successor, a sole proprietor with a total of {strong_s4_total}, a spike with FY25 of at least {strong_s2_fy25}, or hypergrowth of {strong_s3_ratio} FY24 with FY25 of at least {strong_s3_fy25}.',
        note: 'Anything else with a signal goes on the watch list.',
      },
    ],
  },
  {
    title: 'SAM registration checks',
    full: true,
    sentences: [
      { text: 'Flag a young registration when it started on or after {r_young_start} and the total is at least {r_young_min}.' },
      { text: 'Flag a certified firm split across UEIs when the family’s total is at least {r_split_cert_min}.' },
    ],
  },
  {
    title: 'Links between firms',
    full: true,
    sentences: [
      { text: 'Treat a contact or address shared by more than {hub_cap} SAM registrations as a common hub, not a link between firms.' },
      { text: 'Ignore a contact who links more than {person_vendor_cap} vendors.' },
      { text: 'Flag a linked successor when one linked firm had at least {l_min} and the other at most {l_fade_ratio} of that.' },
      { text: 'Ignore {stem_stop_words} when comparing firm names.' },
    ],
  },
  {
    title: 'Exclusion matching',
    full: true,
    sentences: [
      { text: 'A direct match on the SAM exclusions list always reaches the queue.', locked: true },
      { text: 'Match a vendor to an excluded firm by name only when the name has at least {name_match_min_len} characters.' },
      { text: 'Treat a pending exclusion as stale after {stale_pending_days}.' },
    ],
  },
]

/** Split a sentence into text and the settings in it. */
export function parts(text: string): (string | { key: string })[] {
  return text
    .split(/(\{[a-z0-9_]+\})/)
    .map((p) => (p.startsWith('{') ? { key: p.slice(1, -1) } : p))
    .filter((p) => p !== '')
}
