import { FormEvent, useState } from "react";
import { Eye, Lock, User as UserIcon } from "lucide-react";
import { login } from "../api/ops";
import type { User } from "../api/types";

export function LoginPage({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      onLogin(await login(username, password));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-shell">
      <form className="login-panel" onSubmit={submit}>
        <div className="brand-mark"><span>&gt;_</span></div>
        <h1>Ops Agent Chat</h1>
        <p>V1 read-only ops</p>
        <label className="sr-only" htmlFor="login-username">用户名 / 邮箱</label>
        <div className="input-line">
          <UserIcon size={21} strokeWidth={2.15} />
          <input id="login-username" value={username} placeholder="用户名 / 邮箱" onChange={(e) => setUsername(e.target.value)} />
        </div>
        <label className="sr-only" htmlFor="login-password">密码</label>
        <div className="input-line">
          <Lock size={21} strokeWidth={2.15} />
          <input
            id="login-password"
            type={showPassword ? "text" : "password"}
            value={password}
            placeholder="密码"
            onChange={(e) => setPassword(e.target.value)}
          />
          <button className="ghost-icon" type="button" onClick={() => setShowPassword((value) => !value)} title="显示或隐藏密码">
            <Eye size={21} strokeWidth={2.15} />
          </button>
        </div>
        {error && <div className="error-box">{error}</div>}
        <div className="login-options">
          <label className="remember-line">
            <input type="checkbox" />
            <span>记住我</span>
          </label>
          <button type="button">忘记密码?</button>
        </div>
        <button className="primary-button" disabled={loading}>{loading ? "登录中..." : "登录"}</button>
      </form>
    </main>
  );
}
