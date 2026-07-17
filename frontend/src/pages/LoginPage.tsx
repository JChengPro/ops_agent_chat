import { FormEvent, useEffect, useState } from "react";
import { Eye, KeyRound, Lock, Mail, User as UserIcon } from "lucide-react";
import { login, registerAccount, registrationConfig } from "../api/ops";
import type { User } from "../api/types";
import { validateRegistration } from "../authState";

export function LoginPage({ onLogin }: { onLogin: (user: User) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("admin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [registration, setRegistration] = useState({ enabled: true, invite_code_required: false });

  useEffect(() => {
    registrationConfig().then(setRegistration).catch(() => undefined);
  }, []);

  function changeMode(next: "login" | "register") {
    setMode(next);
    setError("");
    setPassword("");
    setPasswordConfirmation("");
    if (next === "register" && username === "admin") setUsername("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      if (mode === "register") {
        const validationError = validateRegistration(
          { username, email, password, passwordConfirmation, inviteCode },
          registration.invite_code_required,
        );
        if (validationError) {
          setError(validationError);
          return;
        }
        onLogin(await registerAccount({
          username,
          email,
          password,
          password_confirmation: passwordConfirmation,
          invite_code: inviteCode || undefined,
        }));
      } else {
        onLogin(await login(username, password));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : mode === "register" ? "注册失败" : "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-shell">
      <form className={`login-panel ${mode === "register" ? "registration-panel" : ""}`} onSubmit={submit}>
        <div className="brand-mark"><span>&gt;_</span></div>
        <h1>Ops Agent Chat</h1>
        <p>{mode === "register" ? "创建你的独立运维工作区" : "安全、可追踪的智能运维工作台"}</p>
        {registration.enabled && <div className="auth-mode-switch" aria-label="认证方式">
          <button type="button" className={mode === "login" ? "active" : ""} onClick={() => changeMode("login")}>登录</button>
          <button type="button" className={mode === "register" ? "active" : ""} onClick={() => changeMode("register")}>注册</button>
        </div>}
        <label className="sr-only" htmlFor="login-username">{mode === "register" ? "用户名" : "用户名 / 邮箱"}</label>
        <div className="input-line">
          <UserIcon size={21} strokeWidth={2.15} />
          <input id="login-username" value={username} placeholder={mode === "register" ? "用户名" : "用户名 / 邮箱"} autoComplete="username" required onChange={(e) => setUsername(e.target.value)} />
        </div>
        {mode === "register" && <>
          <label className="sr-only" htmlFor="register-email">邮箱</label>
          <div className="input-line">
            <Mail size={21} strokeWidth={2.15} />
            <input id="register-email" type="email" value={email} placeholder="邮箱" autoComplete="email" required onChange={(e) => setEmail(e.target.value)} />
          </div>
        </>}
        <label className="sr-only" htmlFor="login-password">密码</label>
        <div className="input-line">
          <Lock size={21} strokeWidth={2.15} />
          <input
            id="login-password"
            type={showPassword ? "text" : "password"}
            value={password}
            placeholder={mode === "register" ? "密码（至少 10 位，包含字母和数字）" : "密码"}
            autoComplete={mode === "register" ? "new-password" : "current-password"}
            required
            onChange={(e) => setPassword(e.target.value)}
          />
          <button className="ghost-icon" type="button" onClick={() => setShowPassword((value) => !value)} title="显示或隐藏密码">
            <Eye size={21} strokeWidth={2.15} />
          </button>
        </div>
        {mode === "register" && <>
          <label className="sr-only" htmlFor="register-password-confirmation">确认密码</label>
          <div className="input-line">
            <Lock size={21} strokeWidth={2.15} />
            <input id="register-password-confirmation" type={showPassword ? "text" : "password"} value={passwordConfirmation} placeholder="再次输入密码" autoComplete="new-password" required onChange={(e) => setPasswordConfirmation(e.target.value)} />
          </div>
          {registration.invite_code_required && <>
            <label className="sr-only" htmlFor="register-invite-code">注册码</label>
            <div className="input-line">
              <KeyRound size={21} strokeWidth={2.15} />
              <input id="register-invite-code" value={inviteCode} placeholder="管理员提供的注册码" autoComplete="off" required onChange={(e) => setInviteCode(e.target.value)} />
            </div>
          </>}
        </>}
        {error && <div className="error-box">{error}</div>}
        {mode === "login" && <div className="login-options">
          <label className="remember-line">
            <input type="checkbox" />
            <span>记住我</span>
          </label>
          <button type="button">忘记密码?</button>
        </div>}
        {mode === "register" && <div className="registration-note">注册后只拥有普通用户权限，不会自动获得其他项目的访问权。</div>}
        <button className="primary-button" disabled={loading}>{loading ? (mode === "register" ? "注册中..." : "登录中...") : (mode === "register" ? "创建账号" : "登录")}</button>
      </form>
    </main>
  );
}
