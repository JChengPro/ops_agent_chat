export type User = {
  id: number;
  username: string;
  email: string;
  role: string;
};

export type Project = {
  id: number;
  name: string;
  description?: string;
  deploy_type: string;
  workdir: string;
  compose_file?: string;
  health_url?: string;
  server_id: number;
  allowed_container_prefixes: string[];
  known_services: string[];
  settings_json: Record<string, unknown>;
};

export type ChatSession = {
  id: number;
  project_id: number;
  user_id: number;
  title: string;
  status: string;
};

export type ChatMessage = {
  id: number;
  session_id: number;
  project_id: number;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  message_type: string;
  metadata_json: Record<string, unknown>;
};

export type CommandRun = {
  id: number;
  command: string;
  cwd: string;
  purpose?: string;
  risk_level: string;
  status: string;
  exit_code?: number;
  stdout_excerpt?: string;
  stderr_excerpt?: string;
  duration_ms?: number;
  created_at?: string;
  started_at?: string;
  finished_at?: string;
  ruleguard_result?: Record<string, unknown>;
};

export type RagDocument = {
  id: number;
  project_id: number;
  title: string;
  file_name: string;
  file_type: string;
  doc_type: string;
  status: string;
  chunk_count: number;
};
