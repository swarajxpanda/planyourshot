export interface User {
  id: string;
  email: string;
  name: string;
  picture: string;
  plan: string;
  max_scripts: number;
  max_upload_mb: number;
  is_admin?: boolean;
}

export interface LegalDoc {
  name: string;
  title: string;
  markdown: string;
}

export interface AdminUser {
  id: string;
  email: string;
  name: string;
  plan: string;
  disabled: boolean;
  calls: number;
  chars: number;
  created_at: string;
  last_login_at: string;
}

export interface AdminOverview {
  days: { day: string; calls: number; failed: number; refused: number }[];
  queue: { queued: number; running: number; oldest_queued_seconds: number; failed_total: number };
  failed_jobs: { id: number; kind: string; user_email: string | null; script_id: string | null; attempts: number; error: string; updated_at: string }[];
  top_users: AdminUser[];
  totals: { users: number; scripts: number };
}

export interface Scene {
  number: number;
  heading: string;
  int_ext: string | null;
  location: string | null;
  time_of_day: string | null;
  start: number;
  end: number;
}

export interface Revision {
  id: number;
  number: number;
  source: "upload" | "ai_edit" | "manual" | "restore";
  summary: string;
  chars: number;
  created_at: string;
  content?: string;
}

export interface Script {
  id: string;
  title: string;
  original_filename: string;
  file_type: string;
  file_size: number;
  status: "processing" | "ready" | "failed";
  error: string;
  shotlist_status: ShotlistStatus;
  shot_count: number;
  revision_count: number;
  created_at: string;
  updated_at: string;
  revision?: Revision & { content: string };
  scenes?: Scene[];
}

export type ShotlistStatus = "none" | "generating" | "ready" | "failed";

export interface Edit {
  find: string;
  replace: string;
  reason: string;
  status: "pending" | "applied" | "rejected" | "failed";
}

export interface Proposal {
  id: number;
  status: "pending" | "applied" | "rejected";
  edits: Edit[];
}

export interface Message {
  id: number;
  role: "user" | "assistant";
  mode: "discuss" | "edit";
  content: string;
  /** off-topic exchange: shown, but never sent back to the model */
  refused: boolean;
  created_at: string;
  proposal: Proposal | null;
}

export interface Shot {
  id: number;
  scene_number: number;
  scene_heading: string;
  position: number;
  size: string;
  angle: string;
  movement: string;
  lens: string;
  description: string;
  composition: string;
  characters: string[];
  props: string[];
  location: string;
  notes: string;
  /** empty angle, movement or lens, as judged by the backend */
  missing: boolean;
}

export interface ShotList {
  shots: Shot[];
  status: ShotlistStatus;
  error: string;
  done: number;
  total: number;
}

export type Channel = "script" | "shots";

export interface PlanLimits {
  max_scripts: number;
  max_script_chars: number;
  messages_per_day: number;
  full_generations_per_month: number;
  scene_generations_per_day: number;
  shot_regenerations_per_day: number;
  requests_per_minute: number;
  csv_export: boolean;
}

/** One limit, as the backend describes it (label included). */
export interface UsageRow {
  key: string;
  label: string;
  used: number;
  limit: number;
  /** rolling window, e.g. "24 hours"; null for limits that don't reset */
  window: string | null;
  /** when the oldest counted event leaves the window; null when nothing is counted */
  resets_at: string | null;
  reset_in_seconds: number | null;
}

export interface UsageFeature {
  key: string;
  label: string;
  included: boolean;
}

export interface Usage {
  plan: string;
  plan_name: string;
  rows: UsageRow[];
  features: UsageFeature[];
  limits: PlanLimits;
  usage: {
    scripts: number;
    messages: number;
    full_generations: number;
    scene_generations: number;
    shot_regenerations: number;
    requests_last_minute: number;
  };
}
