import { useEffect, useState } from "react";
import { clearToken, getToken } from "../api/client";
import { logout, me } from "../api/ops";
import type { User } from "../api/types";
import { LoginPage } from "./LoginPage";
import { WorkspacePage } from "./WorkspacePage";

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(Boolean(getToken()));

  useEffect(() => {
    if (!getToken()) return;
    me()
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="boot">Loading Ops Agent Chat...</div>;
  if (!user) return <LoginPage onLogin={setUser} />;
  return <WorkspacePage user={user} onLogout={async () => {
    try { await logout(); } finally { clearToken(); setUser(null); }
  }} />;
}
