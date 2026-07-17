import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Check, Copy, KeyRound, X } from "lucide-react";
import { createPortal } from "react-dom";

import type { Connection, ConnectionPayload, Environment, EnvironmentPayload, Project } from "../api/types";

export type ProjectConfigurationValue = {
  project: { name: string; description?: string };
  environment: EnvironmentPayload;
  connection: ConnectionPayload | null;
};

export function ProjectConfigDialog({
  project,
  environment,
  connection,
  onClose,
  onSave,
}: {
  project: Project | null;
  environment: Environment | null;
  connection: Connection | null;
  onClose: () => void;
  onSave: (value: ProjectConfigurationValue) => Promise<void>;
}) {
  const firstInput = useRef<HTMLInputElement | null>(null);
  const [projectName, setProjectName] = useState(project?.name || "");
  const [description, setDescription] = useState(project?.description || "");
  const [environmentName, setEnvironmentName] = useState(environment?.name || "default");
  const [runtime, setRuntime] = useState(environment?.runtime_type || "docker_compose");
  const [workdir, setWorkdir] = useState(environment?.workdir || "");
  const [namespace, setNamespace] = useState(environment?.namespace || "");
  const [composeFile, setComposeFile] = useState(String(environment?.config_json?.compose_file || "docker-compose.yml"));
  const [policy, setPolicy] = useState(environment?.policy_profile || "development");
  const [monitoring, setMonitoring] = useState(environment?.monitoring_enabled ?? false);
  const [autoRemediation, setAutoRemediation] = useState(environment?.auto_remediation_enabled ?? false);
  const [isDefault, setIsDefault] = useState(environment?.is_default ?? true);
  const [connectionName, setConnectionName] = useState(connection?.name || `${slug(project?.name || "project")}-server`);
  const [host, setHost] = useState(connection?.host || "");
  const [port, setPort] = useState(String(connection?.port || 22));
  const [username, setUsername] = useState(connection?.username || "opsagent");
  const [credentialRef, setCredentialRef] = useState("");
  const [fingerprint, setFingerprint] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const closeRef = useRef(onClose);
  const savingRef = useRef(saving);
  closeRef.current = onClose;
  savingRef.current = saving;
  const needsConnection = runtime !== "manual";
  const needsWorkdir = runtime === "docker_compose" || runtime === "mixed";
  const title = project ? (environment ? "编辑项目配置" : "新增运行环境") : "新建项目";

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    firstInput.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !savingRef.current) closeRef.current();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, []);

  const existingConfig = useMemo(() => ({ ...(environment?.config_json || {}) }), [environment]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    setError("");
    const config = { ...existingConfig };
    if (runtime === "docker_compose" || runtime === "mixed") config.compose_file = composeFile.trim() || "docker-compose.yml";
    else delete config.compose_file;
    const connectionValue: ConnectionPayload | null = needsConnection ? {
      name: connectionName.trim(),
      connection_type: "ssh",
      host: host.trim(),
      port: Number(port) || 22,
      username: username.trim(),
      config_json: connection?.config_json || {},
      ...(credentialRef.trim() ? { credential_ref: credentialRef.trim() } : {}),
      ...(fingerprint.trim() ? { host_fingerprint: fingerprint.trim() } : {}),
    } : null;
    setSaving(true);
    try {
      await onSave({
        project: { name: projectName.trim(), description: description.trim() || undefined },
        environment: {
          name: environmentName.trim(),
          runtime_type: runtime,
          connection_id: environment?.connection_id ?? null,
          workdir: workdir.trim() || null,
          namespace: namespace.trim() || null,
          config_json: config,
          policy_profile: policy,
          monitoring_enabled: monitoring,
          auto_remediation_enabled: autoRemediation,
          is_default: isDefault,
        },
        connection: connectionValue,
      });
      onClose();
    } catch (value) {
      setError(value instanceof Error ? value.message : "配置保存失败");
    } finally {
      setSaving(false);
    }
  }

  return createPortal(
    <div className="dialog-backdrop" onMouseDown={event => event.target === event.currentTarget && !saving && onClose()}>
      <section className="project-config-dialog" role="dialog" aria-modal="true" aria-labelledby="project-config-title">
        <header className="dialog-header">
          <div><h2 id="project-config-title">{title}</h2><p>填写项目连接和运行环境。保存后 Agent 只会在这个范围内读取状态或执行受控操作。</p></div>
          <button type="button" className="dialog-close" onClick={onClose} disabled={saving} aria-label="关闭配置窗口"><X size={19}/></button>
        </header>
        <form onSubmit={submit} className="project-config-form">
          <div className="project-config-grid">
            <fieldset className="config-form-section">
              <legend>项目信息</legend>
              <label><FieldLabel text="项目名称" required help="左侧项目列表中显示的名称，例如 VideoHub。"/><input ref={firstInput} value={projectName} onChange={event => setProjectName(event.target.value)} maxLength={120} required/></label>
              <label><FieldLabel text="项目说明" help="简要描述项目用途，帮助 Agent 理解项目目标；可以留空。"/><textarea value={description} onChange={event => setDescription(event.target.value)} maxLength={1000} rows={3}/></label>
            </fieldset>

            <fieldset className="config-form-section">
              <legend>运行环境</legend>
              <div className="form-two-columns">
                <label><FieldLabel text="环境名称" required help="项目内的环境名称，例如 local、staging 或 production。"/><input value={environmentName} onChange={event => setEnvironmentName(event.target.value)} maxLength={80} required/></label>
                <label><FieldLabel text="运行时" required help="选择目标采用的真实运行方式，Agent 会据此构造受控命令。"/><select value={runtime} onChange={event => setRuntime(event.target.value)} required><option value="docker_compose">Docker Compose</option><option value="kubernetes">Kubernetes</option><option value="systemd">systemd</option><option value="manual">手动配置</option><option value="mixed">混合运行时</option></select></label>
                <label><FieldLabel text="工作目录" required={needsWorkdir} help="目标服务器上的项目绝对目录，例如 /srv/my-project。Docker Compose 文件应位于该目录中。"/><input value={workdir} onChange={event => setWorkdir(event.target.value)} placeholder="/srv/my-project" required={needsWorkdir}/></label>
                {(runtime === "docker_compose" || runtime === "mixed") && <label><FieldLabel text="Compose 文件" required help="相对于工作目录的路径，例如 docker-compose.yml 或 deploy/compose.prod.yml。"/><input value={composeFile} onChange={event => setComposeFile(event.target.value)} required/></label>}
                {runtime === "kubernetes" && <label><FieldLabel text="命名空间" help="Kubernetes Namespace，例如 default 或 production。"/><input value={namespace} onChange={event => setNamespace(event.target.value)} placeholder="default"/></label>}
                <label><FieldLabel text="策略环境" required help="生产环境会采用更严格的审批策略；低风险自动修复仅在开发和测试环境生效。"/><select value={policy} onChange={event => setPolicy(event.target.value)} required><option value="development">开发</option><option value="test">测试</option><option value="staging">预发布</option><option value="production">生产</option></select></label>
              </div>
              <label className="default-check"><input type="checkbox" checked={isDefault} onChange={event => setIsDefault(event.target.checked)}/><span>设为默认环境</span></label>
            </fieldset>

            {needsConnection && <fieldset className="config-form-section config-form-wide">
              <legend>SSH 连接</legend>
              <SSHSetupGuide name={connectionName} host={host} port={port} username={username} credentialRef={credentialRef}/>
              <div className="form-three-columns">
                <label><FieldLabel text="连接名称" required help="当前项目内用于识别服务器的名称，例如 videohub-local。"/><input value={connectionName} onChange={event => setConnectionName(event.target.value)} maxLength={120} required/></label>
                <label><FieldLabel text="主机" required help="Backend 和 Worker 容器可访问的 SSH 地址。当前 WSL 宿主通常填写 host.docker.internal。"/><input value={host} onChange={event => setHost(event.target.value)} placeholder="10.0.0.12" required/></label>
                <label><FieldLabel text="端口" required help="目标服务器 SSH 端口，通常为 22。"/><input type="number" min="1" max="65535" value={port} onChange={event => setPort(event.target.value)} required/></label>
                <label><FieldLabel text="用户名" required help="目标服务器上已有的低权限账号，例如 opsagent。系统不会自动创建该用户。"/><input value={username} onChange={event => setUsername(event.target.value)} placeholder="opsagent" required/></label>
                <label><FieldLabel text="私钥引用" required={!connection?.credential_configured} help="填写容器内私钥路径，不是密钥内容。例如 /run/secrets/videohub_ed25519。私钥需由部署者挂载。"/><input value={credentialRef} onChange={event => setCredentialRef(event.target.value)} placeholder={connection?.credential_configured ? "已配置；留空表示不修改" : "/run/secrets/project_ed25519"} required={!connection?.credential_configured}/></label>
                <label><FieldLabel text="Host Key 指纹" required={!connection?.host_fingerprint_configured} help="填写目标 SSH 主机公钥的 SHA256 指纹，例如 SHA256:AbCd...，用于防止连接到伪造服务器。"/><input value={fingerprint} onChange={event => setFingerprint(event.target.value)} placeholder={connection?.host_fingerprint_configured ? "已配置；留空表示不修改" : "SHA256:AbCdEf..."} required={!connection?.host_fingerprint_configured}/></label>
              </div>
            </fieldset>}

            <fieldset className="config-form-section config-form-wide">
              <legend>主动运维</legend>
              <div className="config-switch-grid">
                <ControlSwitch label="主动巡检" description="Worker 定时读取真实运行状态，发现异常后在活动区生成事件。" checked={monitoring} disabled={saving} onChange={setMonitoring}/>
                <ControlSwitch label="低风险自动修复" description={monitoring ? "开发或测试环境中，可自动启动已停止的已登记服务并验证结果。" : "该偏好会保存，但只有开启主动巡检后才会生效。"} checked={autoRemediation} disabled={saving} onChange={setAutoRemediation}/>
              </div>
            </fieldset>
          </div>
          {error && <p className="dialog-error" role="alert">{error}</p>}
          <footer className="dialog-actions"><button type="button" onClick={onClose} disabled={saving}>取消</button><button className="primary" disabled={saving}>{saving ? "保存中..." : project ? "保存配置" : "创建项目"}</button></footer>
        </form>
      </section>
    </div>,
    document.body,
  );
}

function FieldLabel({ text, required = false }: { text: string; required?: boolean; help?: string }) {
  return <span className="field-label"><span>{text}{required && <em aria-hidden="true">*</em>}</span></span>;
}

function ControlSwitch({ label, description, checked, disabled, onChange }: { label: string; description: string; checked: boolean; disabled: boolean; onChange: (value: boolean) => void }) {
  return <label className={`setting-toggle${disabled ? " disabled" : ""}`}><span className="setting-toggle-copy"><strong>{label}</strong><small>{description}</small></span><input type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} aria-label={label}/><span className="toggle-track" aria-hidden="true"><span/></span></label>;
}

function SSHSetupGuide({ name, host, port, username, credentialRef }: { name: string; host: string; port: string; username: string; credentialRef: string }) {
  const [copied, setCopied] = useState<number | null>(null);
  const safeName = slug(name || "project-server");
  const containerKeyPath = credentialRef.trim() || `/run/secrets/${safeName}_ed25519`;
  const keyFile = containerKeyPath.split("/").filter(Boolean).pop() || `${safeName}_ed25519`;
  const hostKeyPath = `secrets/${keyFile}`;
  const targetHost = host.trim() || "10.0.0.12";
  const shellHost = targetHost === "host.docker.internal" ? "127.0.0.1" : targetHost;
  const targetPort = String(Number(port) || 22);
  const targetUser = username.trim() || "opsagent";
  const destination = `${targetUser}@${shellHost}`;
  const steps = [
    { title: "准备目标服务器用户", where: "目标服务器", command: `id -u -- ${shellQuote(targetUser)} >/dev/null 2>&1 || sudo useradd -m -s /bin/bash -- ${shellQuote(targetUser)}` },
    { title: "生成 Agent 专用密钥", where: "Ops Agent 项目根目录", command: `mkdir -p secrets\nssh-keygen -t ed25519 -f ${shellQuote(hostKeyPath)} -N '' -C ${shellQuote(`ops-agent-${safeName}`)}\nchmod 600 ${shellQuote(hostKeyPath)}` },
    { title: "安装公钥", where: "Ops Agent 所在主机", command: `ssh-copy-id -i ${shellQuote(`${hostKeyPath}.pub`)} -p ${shellQuote(targetPort)} ${shellQuote(destination)}` },
    { title: "获取 Host Key 指纹", where: "目标服务器可信终端", command: "sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256" },
    { title: "验证密钥登录", where: "Ops Agent 所在主机", command: `ssh -i ${shellQuote(hostKeyPath)} -p ${shellQuote(targetPort)} ${shellQuote(destination)}` },
  ];
  async function copyCommand(command: string, index: number) {
    try { await navigator.clipboard.writeText(command); setCopied(index); window.setTimeout(() => setCopied(value => value === index ? null : value), 1600); } catch { setCopied(null); }
  }
  return <details className="ssh-guide"><summary><span><KeyRound size={16}/><strong>SSH 配置指南</strong></span><small>生成密钥、安装公钥并获取指纹</small></summary><div className="ssh-guide-body"><ol>{steps.map((step, index) => <li key={step.title}><div className="ssh-step-heading"><span>{index + 1}</span><div><strong>{step.title}</strong><small>{step.where}</small></div></div><div className="ssh-command"><code>{step.command}</code><button type="button" onClick={() => void copyCommand(step.command, index)} aria-label={`复制${step.title}命令`}>{copied === index ? <Check size={14}/> : <Copy size={14}/>}</button></div></li>)}</ol><div className="ssh-guide-values"><span>私钥引用填写</span><code>{containerKeyPath}</code><span>Host Key 指纹填写命令输出中的</span><code>SHA256:...</code></div></div></details>;
}

function slug(value: string) { return value.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "project"; }
function shellQuote(value: string) { return `'${value.replace(/'/g, "'\\\"'\\\"'")}'`; }
