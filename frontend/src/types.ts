/**
 * TypeScript types mirroring the FastAPI Pydantic models in backend/app/models.py
 */

/** Per weekday — worker window; visit-day fields apply only when that day has tasks. */
export type WorkerAvailabilityDay = {
  earliest: string
  latest: string
  /** If true, days with visits may run until visit_day_latest (e.g. weekend bundles). */
  visit_day_longer?: boolean
  /** Upper end when visit_day_longer; defaults to 10:00 PM if omitted. */
  visit_day_latest?: string
  /** Optional target shift length (minutes) when seeding default task placement for that weekday. */
  preferred_duration_min?: number
}

/** One suggested fix from POST … 422 `DAY_CAPACITY_EXCEEDED` detail.suggestions. */
export type DayCapacitySuggestion = {
  label: string
  action: string
  weekday?: string
  latest?: string
  visit_day_longer?: boolean
  visit_day_latest?: string
  task_name?: string
  reduce_by?: number
}

/** Structured 422 body when scheduled minutes exceed worker window for one weekday. */
export type DayCapacityDetail = {
  code: "DAY_CAPACITY_EXCEEDED"
  weekday: string
  needed_minutes: number
  available_minutes: number
  earliest: string
  latest: string
  message?: string
  suggestions: DayCapacitySuggestion[]
}

/** Result of `evaluateAvailabilityWindow` — mirrors backend availability span rules. */
export type AvailabilityWindowEvaluation = {
  minutes: number
  reversed: boolean
  tooNarrow: boolean
  note: string | null
}

export interface Client {
  id: number
  client_id: string
  client_name: string
  case_number: string
  county: string
  asw_name: string
  asw_email: string
  asw_phone: string
  pay_rate: number
  /** Provider/agency as on MDHHS-6064-P (persisted for workbook rebuilds). */
  provider_name?: string
  /** Authorization date string from 6064-P. */
  auth_date?: string
  created_at: string
  updated_at: string
  /** Full day names (Monday…Sunday). Omitted on very old API responses. */
  availability?: Record<string, WorkerAvailabilityDay>
  /** Other adults reside — ASM 120 shared-living IADL proration may apply (validator warning). */
  shared_living?: boolean
  /** IADLs completed separately — no half-cap warning when shared living. */
  iadl_separate_documented?: boolean
}

/** A single day block inside Schedule.days (backend calculate.generate_schedule). */
export interface DaySchedule {
  start: string
  end: string
  minutes: number
  tasks: string[]
}

/**
 * Full schedule object returned by the pipeline (dict with weekly_minutes, monthly_*, days).
 */
export interface Schedule {
  weekly_minutes: number
  monthly_minutes: number
  monthly_amount: number
  /** Σ per-line monthly minutes — same invariant as backend ``mdhhs_monthly_minutes`` (optional on older payloads). */
  mdhhs_monthly_minutes?: number
  /** Calendar-month Σ duration_min — optional on older payloads. */
  delivered_minutes?: number
  delivered_amount?: number
  billable_minutes?: number
  billable_amount?: number
  days: Record<string, DaySchedule>
}

/** One row in the MDHHS task table from PDF extract (per-task line item). */
export interface Task {
  task_name: string
  min_per_day: number
  days_per_week: number
  monthly_time_str?: string
  monthly_amount?: number
  [key: string]: unknown
}

export interface ValidationCheck {
  name: string
  passed: boolean
  expected: unknown
  actual: unknown
  tolerance: string
  detail: string
}

export interface ValidationReport {
  checks: ValidationCheck[]
  all_passed: boolean
  summary: string
  /** Backend billing posture: BILLABLE_EXACT | BILLABLE_AT_CAP | BILLABLE_UNDER_CAP | INVALID */
  validation_status?: string
  delivered_minutes?: number
  authorized_minutes?: number
  billable_minutes?: number
}

/** One scheduler override when a preferred weekday could not be honored. */
export type PlacementOverride = {
  preferred: string
  placed_on: string
  reason: string
}

/** Logged when a per-weekday preferred shift length could not be met. */
export type WeekdayDurationOverride = {
  weekday: string
  preferred_duration: number
  actual_duration: number
  reason: string
}

/**
 * Placement for one task. `selected_*` = actual schedule; `preferred_*` = user
 * picks the backend tries to honor. Mirrors backend `calculate.TaskPlacement`.
 */
export interface TaskPlacement {
  task_name: string
  min_per_day: number
  days_per_week: number
  selected_weekdays: string[]
  selected_dates: string[]
  /** ISO dates excluded for this month (overshoot trim). */
  excluded_dates?: string[]
  placement_fallback?: boolean
  preferred_weekdays?: string[]
  preferred_dates?: string[]
  placement_overrides?: PlacementOverride[]
  /** When true, preferred weekdays were inferred (legacy) — chips show auto styling. */
  preference_unspecified?: boolean
}

/**
 * User-editable ScheduleConfig — the source of truth for what's scheduled
 * on which day at what time. Mirrors backend `calculate.ScheduleConfig`.
 */
export interface ScheduleConfig {
  tasks: TaskPlacement[]
  start_time_by_weekday: Record<string, string>
  weekday_override_log?: WeekdayDurationOverride[]
}

export interface Plan {
  id: number
  client_row_id: number
  version: number
  weekly_minutes: number
  monthly_minutes: number
  /** Scheduled monthly $ — matches ``schedule.monthly_amount`` / backend ``mdhhs_*`` totals. */
  monthly_amount: number
  /**
   * Sum of per-line monthly $ as printed on the MDHHS-6064-P bottom line
   * (each line already rounded to $0.01). This is the MDHHS-authorized
   * total the agency must reconcile against. 0 on legacy plans that
   * predate the field — use the hierarchy in ReconciliationBanner.
   */
  mdhhs_form_amount?: number
  /** Duplicated from ``schedule`` for API convenience; 0 when absent on legacy rows. */
  delivered_minutes?: number
  delivered_amount?: number
  billable_minutes?: number
  billable_amount?: number
  /**
   * Calendar month this plan is calibrated against (1-12). 0 means legacy
   * plan written before the column existed — UI should fall back to
   * `schedule.year` / `schedule.month` or current month.
   */
  year?: number
  month?: number
  /** Same shape as backend schedule dict. */
  schedule: Schedule & Record<string, unknown>
  tasks: Task[]
  /**
   * ScheduleConfig used to build this plan's schedule. Empty object ({})
   * on legacy plans pre-dating the editor — the UI falls back to the
   * default weekend-weighted layout in that case.
   */
  config?: Partial<ScheduleConfig> & Record<string, unknown>
  validation: ValidationReport
  validation_passed: boolean
  source_pdf_path: string
  xlsx_path: string
  pdf_path: string
  notes: string | null
  created_at: string
}

export interface LatestPlanSummary {
  version: number
  monthly_amount: number
  validation_passed: boolean
  created_at: string
  /** True when `/download/source` serves the stored authorization PDF. */
  has_source_pdf?: boolean
}

/** List row from GET /api/clients (ClientListItem). */
export interface ClientSummary {
  client_id: string
  client_name: string
  latest_plan: LatestPlanSummary | null
  updated_at: string
}

export interface ClientDetail {
  client: Client
  plans: Plan[]
}

export type DownloadArtifact = {
  filename: string
  media_type: string
  base64: string
}

export interface UploadResult {
  client: Client
  plan: Plan
  artifacts?: Partial<Record<"xlsx" | "pdf" | "source" | "weekly", DownloadArtifact>>
}

/** PDF extraction only — `POST /api/clients/preview-parse`. */
export interface PreviewParseOut {
  client_name: string
  client_id: string
  pay_rate: number
  monthly_total_amount: number
  tasks: Task[]
}

/** Body for `POST /api/clients/preview-calibrate`. */
export interface PreviewCalibrateBody {
  tasks: Task[]
  availability: Record<string, WorkerAvailabilityDay>
  year?: number
  month?: number
  pay_rate?: number
}

/** Dry-run calibration for upload preflight — always 200 when the request validates. */
export interface PreviewCalibrateOut {
  weekly_authorized_minutes: number
  weekly_base_capacity_minutes: number
  weekly_extension_minutes: number
  weekly_total_capacity_minutes: number
  headroom_percent: number
  schedule_ok: boolean
  day_capacity: Record<string, unknown> | null
  availability_error: string | null
  authorization_capacity_error: Record<string, unknown> | null
}

export interface PatchPlanRequest {
  schedule: Record<string, unknown>
  notes?: string | null
}

export interface PatchPlanConfigRequest {
  config: ScheduleConfig
  /** Server replaces placements with fresh ``default_config_for`` (merge start times from config). */
  reseed_placement?: boolean
}

export interface PreferredWindow {
  weekday_start: string
  weekend_start: string
  /** Full day names (Monday…Sunday) → display time e.g. "7:00 AM". */
  start_time_by_weekday?: Record<string, string>
}

export interface RerunPlanRequest {
  preferred_window: PreferredWindow
  use_llm: boolean
  /** Plain-text preferences sent to Claude when use_llm is true (max ~12k chars). */
  llm_notes?: string | null
  /** Optional service-month override; omitted values default to current month. */
  year?: number
  month?: number
  /** Optional ScheduleConfig; omitted → backend regenerates defaults. */
  config?: ScheduleConfig
}

export interface HealthResponse {
  status: string
  version: string
}
