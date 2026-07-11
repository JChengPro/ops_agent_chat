import { apiFetch, setToken } from "./client";
import { getToken } from "./client";
import type { ChatMessage, ChatSession, CommandRun, Project, RagDocument, User } from "./types";

export async function login(username: string, password: string): Promise<User> {
  const response = await apiFetch<{ access_token: string; user: User }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password })
  });
  setToken(response.access_token);
  return response.user;
}

export function me(): Promise<User> {
  return apiFetch<User>("/api/auth/me");
}

export function listProjects(): Promise<Project[]> {
  return apiFetch<Project[]>("/api/projects");
}

export function updateProject(projectId: number, payload: { name?: string; is_pinned?: boolean }): Promise<Project> {
  return apiFetch<Project>(`/api/projects/${projectId}`, {
    method: "PATCH",
    body: JSON.stringify(payload)
  });
}

export function deleteProject(projectId: number): Promise<Project> {
  return apiFetch<Project>(`/api/projects/${projectId}`, {
    method: "DELETE"
  });
}

export function listSessions(projectId: number): Promise<ChatSession[]> {
  return apiFetch<ChatSession[]>(`/api/projects/${projectId}/chat-sessions`);
}

export function createSession(projectId: number, title = "新会话"): Promise<ChatSession> {
  return apiFetch<ChatSession>(`/api/projects/${projectId}/chat-sessions`, {
    method: "POST",
    body: JSON.stringify({ title })
  });
}

export function updateSession(sessionId: number, payload: { title?: string; is_pinned?: boolean }): Promise<ChatSession> {
  return apiFetch<ChatSession>(`/api/chat-sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(payload)
  });
}

export function deleteSession(sessionId: number): Promise<ChatSession> {
  return apiFetch<ChatSession>(`/api/chat-sessions/${sessionId}`, {
    method: "DELETE"
  });
}

export function listMessages(sessionId: number): Promise<ChatMessage[]> {
  return apiFetch<ChatMessage[]>(`/api/chat-sessions/${sessionId}/messages`);
}

export function sendMessage(sessionId: number, content: string) {
  return apiFetch<{
    assistant_message: ChatMessage;
    command_runs: CommandRun[];
    command_plan?: Record<string, unknown>;
    experience_sources: unknown[];
    rag_sources: unknown[];
  }>(`/api/chat-sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content })
  });
}

export function listCommandRuns(projectId: number, sessionId?: number): Promise<CommandRun[]> {
  const suffix = sessionId ? `?session_id=${sessionId}` : "";
  return apiFetch<CommandRun[]>(`/api/projects/${projectId}/command-runs${suffix}`);
}

export function listRagDocuments(projectId: number): Promise<RagDocument[]> {
  return apiFetch<RagDocument[]>(`/api/projects/${projectId}/rag-documents`);
}

export async function uploadRagDocument(projectId: number, file: File, docType = "normal"): Promise<RagDocument> {
  const form = new FormData();
  form.append("file", file);
  form.append("doc_type", docType);
  const headers = new Headers();
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`/api/projects/${projectId}/rag-documents`, {
    method: "POST",
    headers,
    body: form
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return (await response.json()) as RagDocument;
}
