const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export function getToken(): string | null {
  return localStorage.getItem("ops_token");
}

export function setToken(token: string): void {
  localStorage.setItem("ops_token", token);
}

export function clearToken(): void {
  localStorage.removeItem("ops_token");
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", headers.get("Content-Type") ?? "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(formatApiError(response.status, text));
  }
  return (await response.json()) as T;
}

function formatApiError(status: number, text: string): string {
  const trimmed = text.trim();
  if (status === 504) return "后端响应超时，请稍后刷新聊天记录；如果结果已生成，会自动保存在当前会话中。";
  if (status === 502 || status === 503) return "后端服务暂时不可用，请检查 ops-agent-backend 是否正常运行。";
  if (trimmed.startsWith("<!DOCTYPE html") || trimmed.startsWith("<html")) {
    return `请求失败，网关返回了 HTML 错误页（HTTP ${status}）。`;
  }
  return trimmed || `Request failed: ${status}`;
}
