import type { AppApi } from '@kirocrew/app-sdk'

export const API = '/apps/integration-radar/api'

export type Confidence = 'exact' | 'likely' | 'possible'
export type Status = 'new' | 'accepted' | 'dismissed' | 'snoozed'

export interface Match {
  connector: string
  confidence: Confidence
  reasons: string[]
  action: string
  criticality: 'high' | 'medium' | 'low'
}

export interface Lesson {
  changeId: string
  rule: string
  status: 'pending' | 'saved' | 'failed'
  error: string | null
}

export interface Change {
  id: string
  partner: string
  partnerName: string
  kind: 'deprecation' | 'sunset' | 'breaking' | 'additive'
  title: string
  body: string
  versions: string[]
  publishedAt: string | null
  effectiveAt: string | null
  effectivePrecision: 'day' | 'month'
  sourceUrl: string
  sources: string[]
  status: Status
  statusReason: string | null
  snoozedUntil: string | null
  matches: Match[]
  impact: Confidence | null
  daysLeft: number | null
  urgent: boolean
  score: number
  lesson: Lesson | null
  priorDismissal: { changeId: string; title: string; reason: string | null; lesson: Lesson | null } | null
}

export interface Summary {
  untriaged: number
  urgent: number
  stalePartners: string[]
  pendingLessons: number
  lastSweepAt: string | null
  manifestLoaded: boolean
}

export interface Issue {
  line: number
  path: string
  message: string
  severity: 'error' | 'notice'
}

export interface ManifestStatus {
  inUse: null | {
    path: string
    loadedAt: number | null
    product: string
    partners: { id: string; displayName: string; versioning: string; poll: string; sources: number }[]
    connectors: { id: string; partner: string; version: string; lifecycle: string; criticality: string; action: string }[]
    allowedHosts: string[]
  }
  lastAttempt: null | { path: string; ok: boolean; errors: Issue[]; notices: Issue[] }
  usingFallback: boolean
}

export interface Candidate {
  id: string
  kind: 'mcp' | 'interface'
  name: string
  usable: boolean
  status: string
  evidence: string[]
  deliverable: boolean
  note: string
}

export interface RoleState {
  role: 'tracker' | 'wiki' | 'chat' | 'repo'
  status: 'bound' | 'degraded' | 'off' | 'proposed' | 'needs_choice'
  candidates: Candidate[]
  binding: null | { candidate?: string; off?: boolean; destination?: { id: string; name: string } }
  proposal: string | null
  reason: string
}

export interface Schedule {
  timezone: string
  sweepTime: string
  digestEnabled: boolean
  digestDay: string
  digestTime: string
}

export interface Setup {
  roles: Record<RoleState['role'], RoleState>
  detection: { detectedAt: string; errors: string[] }
  manifestPath: string | null
  manifest: ManifestStatus
  scheduler: { schedule: Schedule; lastScheduledSweep: string | null; lastScheduledSweepError: string | null; nextSweepAt: string }
  configError: string | null
}

export interface SourceHealth {
  partner: string
  sourceUrl: string
  sourceKind: string
  status: 'ok' | 'failing' | 'unsupported'
  consecutiveFailures: number
  lastAttempt: string | null
  lastSuccess: string | null
  lastError: string | null
  itemsLastRun: number
  stale: boolean
}

/** The SDK throws on HTTP errors with the body in the message; pull out a readable reason. */
export function errorText(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e)
  const json = raw.indexOf('{')
  if (json >= 0) {
    try {
      const body = JSON.parse(raw.slice(json))
      if (typeof body.error === 'string') return body.error
      if (Array.isArray(body.errors)) return body.errors.join('; ')
    } catch {
      /* not JSON */
    }
  }
  return raw
}

export const radar = (api: AppApi) => ({
  summary: () => api.get<Summary>(`${API}/summary`),
  changes: (q: { status?: Status; matched?: boolean }) => {
    const params = new URLSearchParams()
    if (q.status) params.set('status', q.status)
    if (q.matched) params.set('matched', '1')
    const qs = params.toString()
    return api.get<{ changes: Change[] }>(`${API}/changes${qs ? `?${qs}` : ''}`).then((r) => r.changes)
  },
  triage: (id: string, action: 'accept' | 'dismiss' | 'snooze' | 'reopen', body?: unknown) =>
    api.post<Change>(`${API}/changes/${encodeURIComponent(id)}/${action}`, body ?? {}),
  markdown: (id: string) =>
    api.get<{ markdown: string }>(`${API}/changes/${encodeURIComponent(id)}/markdown`).then((r) => r.markdown),
  sweep: () => api.post<{ outcomes: Record<string, number>; stalePartners: string[] }>(`${API}/sweep`, {}),
  sources: () => api.get<{ sources: SourceHealth[]; stalePartners: string[] }>(`${API}/sources`),
  setup: () => api.get<Setup>(`${API}/setup`),
  detect: () => api.post<Setup>(`${API}/setup/detect`, {}),
  destinations: (candidate: string) =>
    api
      .get<{ destinations: { id: string; name: string }[] }>(
        `${API}/setup/roles/chat/destinations?candidate=${encodeURIComponent(candidate)}`,
      )
      .then((r) => r.destinations),
  bind: (role: string, body: unknown) => api.put<Setup>(`${API}/setup/roles/${role}`, body),
  loadManifest: (path: string) => api.post<ManifestStatus & { pathSaved: boolean }>(`${API}/manifest/load`, { path }),
  saveSchedule: (schedule: Partial<Schedule>) => api.put<Setup['scheduler']>(`${API}/setup/schedule`, schedule),
})

export type RadarApi = ReturnType<typeof radar>
