import { FormEvent, useEffect, useState } from "react";
import { Laptop, LogOut, Settings, ShieldCheck, UserRound, X } from "lucide-react";
import { createPortal } from "react-dom";
import {
  changePassword,
  listUserSessions,
  revokeOtherUserSessions,
  revokeUserSession,
  updateProfile,
} from "../api/ops";
import type { User, UserSession } from "../api/types";

type Section = "profile" | "security";

export function AccountSettingsDialog({
  user,
  onClose,
  onUserUpdated,
  onLogout,
  onOpenModelSettings,
}: {
  user: User;
  onClose: () => void;
  onUserUpdated: (user: User) => void;
  onLogout: () => void | Promise<void>;
  onOpenModelSettings: () => void;
}) {
  const [section, setSection] = useState<Section>("profile");
  const [username, setUsername] = useState(user.username);
  const [email, setEmail] = useState(user.email);
  const [profilePassword, setProfilePassword] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [sessions, setSessions] = useState<UserSession[]>([]);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    listUserSessions()
      .then(value => active && setSessions(value))
      .catch(value => active && setError(value instanceof Error ? value.message : "登录设备加载失败"))
      .finally(() => active && setLoadingSessions(false));
    return () => { active = false; };
  }, []);

  function resetMessages() {
    setError("");
    setNotice("");
  }

  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    resetMessages();
    const normalizedUsername = username.trim();
    const normalizedEmail = email.trim();
    if (!/^[a-z0-9][a-z0-9_.-]{2,31}$/i.test(normalizedUsername)) {
      setError("用户名需要 3-32 个字符，只能使用字母、数字、点、下划线或连字符");
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail)) {
      setError("请输入有效的邮箱地址");
      return;
    }
    setSaving(true);
    try {
      const updated = await updateProfile({
        username: normalizedUsername,
        email: normalizedEmail,
        current_password: profilePassword,
      });
      onUserUpdated(updated);
      setUsername(updated.username);
      setEmail(updated.email);
      setProfilePassword("");
      setNotice("账号资料已更新");
    } catch (value) {
      setError(value instanceof Error ? value.message : "账号资料保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function savePassword(event: FormEvent) {
    event.preventDefault();
    resetMessages();
    if (newPassword.length < 10 || !/[A-Za-z]/.test(newPassword) || !/\d/.test(newPassword)) {
      setError("新密码至少 10 位，并且必须同时包含字母和数字");
      return;
    }
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致");
      return;
    }
    setSaving(true);
    try {
      await changePassword({
        current_password: currentPassword,
        new_password: newPassword,
        new_password_confirmation: confirmation,
      });
      await onLogout();
    } catch (value) {
      setError(value instanceof Error ? value.message : "密码修改失败");
      setSaving(false);
    }
  }

  async function revoke(session: UserSession) {
    resetMessages();
    setSaving(true);
    try {
      if (session.current) {
        await onLogout();
        return;
      }
      await revokeUserSession(session.id);
      setSessions(items => items.filter(item => item.id !== session.id));
      setNotice("该设备的登录已撤销");
    } catch (value) {
      setError(value instanceof Error ? value.message : "撤销登录失败");
    } finally {
      setSaving(false);
    }
  }

  async function revokeOthers() {
    resetMessages();
    setSaving(true);
    try {
      await revokeOtherUserSessions();
      setSessions(items => items.filter(item => item.current));
      setNotice("其他设备已全部退出");
    } catch (value) {
      setError(value instanceof Error ? value.message : "其他设备退出失败");
    } finally {
      setSaving(false);
    }
  }

  return createPortal(
    <div className="dialog-backdrop" onMouseDown={event => event.target === event.currentTarget && !saving && onClose()}>
      <section className="account-settings-dialog" role="dialog" aria-modal="true" aria-labelledby="account-settings-title">
        <header className="dialog-header">
          <div><h2 id="account-settings-title">账号设置</h2><p>管理当前账号资料、密码和已登录设备。</p></div>
          <button type="button" className="dialog-close" onClick={onClose} disabled={saving} aria-label="关闭"><X size={19}/></button>
        </header>
        <div className="account-settings-layout">
          <nav aria-label="账号设置分类">
            <button className={section === "profile" ? "active" : ""} onClick={() => { setSection("profile"); resetMessages(); }}><UserRound size={17}/>账号资料</button>
            <button className={section === "security" ? "active" : ""} onClick={() => { setSection("security"); resetMessages(); }}><ShieldCheck size={17}/>密码与设备</button>
            <button onClick={onOpenModelSettings}><Settings size={17}/>模型设置</button>
          </nav>
          <div className="account-settings-content">
            {section === "profile" ? (
              <form className="account-form" onSubmit={saveProfile}>
                <div><h3>账号资料</h3><p>用户名和邮箱都可以用于登录。修改前需要验证当前密码。</p></div>
                <label><span>用户名</span><input value={username} onChange={event => setUsername(event.target.value)} autoComplete="username" required/></label>
                <label><span>邮箱</span><input type="email" value={email} onChange={event => setEmail(event.target.value)} autoComplete="email" required/></label>
                <label><span>当前密码</span><input type="password" value={profilePassword} onChange={event => setProfilePassword(event.target.value)} autoComplete="current-password" required/></label>
                {error && <p className="dialog-error inline">{error}</p>}
                {notice && <p className="dialog-success">{notice}</p>}
                <div className="account-form-actions"><button className="primary" disabled={saving || !profilePassword}>{saving ? "保存中..." : "保存资料"}</button></div>
              </form>
            ) : (
              <div className="security-settings">
                <form className="account-form password-form" onSubmit={savePassword}>
                  <div><h3>修改密码</h3><p>修改成功后，所有设备都需要重新登录。</p></div>
                  <label><span>当前密码</span><input type="password" value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} autoComplete="current-password" required/></label>
                  <label><span>新密码</span><input type="password" value={newPassword} onChange={event => setNewPassword(event.target.value)} autoComplete="new-password" required/></label>
                  <label><span>确认新密码</span><input type="password" value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="new-password" required/></label>
                  <div className="account-form-actions"><button className="primary" disabled={saving}>{saving ? "处理中..." : "修改密码"}</button></div>
                </form>
                <section className="session-settings">
                  <div className="session-settings-heading">
                    <div><h3>登录设备</h3><p>撤销不再使用或无法识别的登录。</p></div>
                    {sessions.length > 1 && <button onClick={() => void revokeOthers()} disabled={saving}>退出其他设备</button>}
                  </div>
                  {loadingSessions ? <p className="account-muted">正在加载...</p> : sessions.length === 0 ? <p className="account-muted">没有可管理的登录会话。</p> : sessions.map(item => (
                    <div className="user-session-row" key={item.id}>
                      <Laptop size={18}/>
                      <span><strong>{sessionName(item.user_agent)}</strong><small>{item.ip_address || "未知地址"} · {new Date(item.last_seen_at).toLocaleString()}{item.current ? " · 当前设备" : ""}</small></span>
                      <button title={item.current ? "退出当前账号" : "撤销该设备登录"} onClick={() => void revoke(item)} disabled={saving}><LogOut size={16}/></button>
                    </div>
                  ))}
                </section>
                {error && <p className="dialog-error inline">{error}</p>}
                {notice && <p className="dialog-success">{notice}</p>}
              </div>
            )}
          </div>
        </div>
      </section>
    </div>,
    document.body,
  );
}

function sessionName(userAgent?: string | null): string {
  if (!userAgent) return "未知设备";
  if (/Edg\//.test(userAgent)) return "Microsoft Edge";
  if (/Chrome\//.test(userAgent)) return "Google Chrome";
  if (/Firefox\//.test(userAgent)) return "Mozilla Firefox";
  if (/Safari\//.test(userAgent)) return "Safari";
  return "浏览器会话";
}
