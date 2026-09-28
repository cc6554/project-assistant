// 与 coach 领域模型对应的前端类型

export interface JDCard {
  job_title?: string | null;
  company?: string | null;
  salary?: string | null;
  location?: string | null;
  team?: string | null;
  hard_requirements: string[];
  skill_tags: string[];
  responsibilities: string[];
  nice_to_haves: string[];
  raw_summary?: string;
  uncertain_fields?: string[];
}

export interface SkillItem {
  name: string;
  proficiency: "aware" | "working" | "proficient" | "expert";
  confidence: number;
  sources: string[];
  evidence: string;
}

export interface UserSkillProfile {
  user_id: string;
  summary: string;
  target_direction?: string | null;
  skills: SkillItem[];
  open_questions: string[];
}

export interface SkillGap {
  requirement: string;
  matched_skill?: string | null;
  status: "met" | "weak" | "missing" | "evidence_insufficient";
  note: string;
}

export interface LearningPhase {
  phase: string;
  goal: string;
  topics: string[];
  suggested_practice: string;
  duration_weeks?: number | null;
}

export interface LearningPlan {
  direction: string;
  gaps: SkillGap[];
  phases: LearningPhase[];
  github_search_queries: string[];
}

export interface GitHubRepo {
  full_name: string;
  html_url: string;
  description: string;
  language?: string | null;
  stargazers_count: number;
  updated_at: string;
  reason: string;
  match_score: number;
}

export interface SessionSummary {
  id: string;
  job_title?: string | null;
  company?: string | null;
  skill_count: number;
  interview_rounds: number;
  has_plan: boolean;
  repo_count: number;
  updated_at: string;
}

export interface ProviderInfo {
  api_mode: string;
  base_url?: string;
  api_key_env?: string | null;
  timeout: number;
  key_configured: boolean;
}

export interface ConfigInfo {
  providers: Record<string, ProviderInfo>;
  tasks: Record<string, { provider: string; model: string }[]>;
  models_in_use: string[];
  config_path: string;
}

export interface AgentLogEntry {
  ts: string;
  type: "message" | "tool" | "pending";
  role?: "user" | "assistant" | "system" | null;
  kind?: string;
  summary?: string;
  output?: string;
  error?: string;
  content?: string;
}

export interface SessionState {
  session_id: string;
  jd?: JDCard | null;
  profile?: UserSkillProfile | null;
  plan?: LearningPlan | null;
  repos: GitHubRepo[];
  interview_history: [string, string][];
  coach_history: [string, string][];
  agent_logs?: AgentLogEntry[] | null;
}
