import type { Action, Approval, ChatMessage, Environment, Evidence, MonitorEvent } from "./api/types";

const POLLING_TERMINAL_STATES = new Set(["completed", "failed", "cancelled", "waiting_for_approval"]);

export function isRunPollingTerminal(status: string): boolean {
  return POLLING_TERMINAL_STATES.has(status);
}

export function shouldApplySessionResult(currentSessionId: number | null, targetSessionId: number): boolean {
  return currentSessionId === targetSessionId;
}

export function chatMessagesRevision(rows: ChatMessage[]): string {
  return rows.map(message => {
    const approvals = (message.metadata_json.approvals || [])
      .map(item => `${item.id}:${item.decision}:${item.consumed_at || ""}`)
      .join(",");
    return [
      message.id,
      message.role,
      message.message_type,
      message.content,
      String(message.metadata_json.run_status || ""),
      approvals,
    ].join(":");
  }).join("|");
}

export type MonitorNotice = {kind: "success" | "error" | "info"; text: string};
export type EnvironmentMonitoringStatus = {
  tone: "enabled" | "warning" | "disabled";
  label: string;
  detail: string;
};

export function environmentMonitoringStatus(
  environment: Pick<Environment, "monitoring_enabled" | "auto_remediation_enabled" | "last_monitored_at"> | null | undefined,
): EnvironmentMonitoringStatus {
  if (!environment) {
    return {tone: "disabled", label: "巡检未配置", detail: "当前会话没有关联运行环境。"};
  }
  if (!environment.monitoring_enabled) {
    return {
      tone: "disabled",
      label: environment.auto_remediation_enabled ? "巡检关 · 自动修复不生效" : "巡检关 · 自动修复关",
      detail: "系统不会主动检查当前环境，低风险自动修复也不会运行。请在配置中开启。",
    };
  }
  if (!environment.auto_remediation_enabled) {
    return {
      tone: "warning",
      label: "巡检开 · 自动修复关",
      detail: "系统会主动检查并告警，但不会自动执行低风险修复。",
    };
  }
  return {
    tone: "enabled",
    label: "巡检开 · 自动修复开",
    detail: "系统会主动检查当前环境，并按策略执行允许的低风险自动修复。",
  };
}

export function monitorEventSnapshot(item: MonitorEvent): string {
  return [
    item.status,
    item.summary,
    item.diagnostic_run_status || "",
    item.diagnosed_at || "",
  ].join(":");
}

export function shouldNotifyMonitorEvent(item: MonitorEvent, previousSnapshot: string | undefined, initialized: boolean): boolean {
  const snapshot = monitorEventSnapshot(item);
  if (previousSnapshot === snapshot) return false;
  if (initialized) return true;
  return item.status !== "resolved";
}

export function monitorNoticeFor(item: MonitorEvent): MonitorNotice {
  if (item.diagnosis_summary) {
    return item.diagnostic_run_status === "completed"
      ? {kind: "info", text: `自动只读诊断已完成：${item.summary}`}
      : {kind: "error", text: `自动只读诊断未成功：${item.summary}`};
  }
  if (item.diagnostic_run_status === "queued" || item.diagnostic_run_status === "running") {
    return {kind: "info", text: `${item.summary}；自动只读诊断正在收集状态和日志`};
  }
  if (item.status === "resolved") return {kind: "success", text: item.summary};
  if (item.status === "remediated") return {kind: "success", text: item.summary};
  if (item.status === "remediating") return {kind: "info", text: item.summary};
  return {kind: "error", text: item.summary};
}

export function markApprovalDecision(rows: ChatMessage[], approvalId: string, decision: string): ChatMessage[] {
  return rows.map(message => {
    const approvals = message.metadata_json.approvals;
    if (!approvals?.some(item => item.id === approvalId)) return message;
    return {
      ...message,
      metadata_json: {
        ...message.metadata_json,
        approvals: approvals.map(item => item.id === approvalId ? {...item, decision} : item),
      },
    };
  });
}

export function applyApprovalBatchResult(rows: ChatMessage[], approvals: Approval[], runStatus: string): ChatMessage[] {
  const updates = new Map(approvals.map(item => [item.id, item]));
  return rows.map(message => {
    const current = message.metadata_json.approvals;
    if (!current?.some(item => updates.has(item.id))) return message;
    return {
      ...message,
      metadata_json: {
        ...message.metadata_json,
        run_status: runStatus,
        approvals: current.map(item => updates.get(item.id) || item),
      },
    };
  });
}

export function rollbackDescription(action?: Action): string {
  const spec = action?.rollback_spec_json || {};
  if (spec.kind === "no_op") return "目标原状态无需恢复";
  if (spec.kind === "config_backup") return "恢复变更前的配置文件";
  if (spec.kind === "deployment") return "按已登记的部署恢复方案处理";
  if (spec.kind === "capability") {
    const labels: Record<string, string> = {
      "service.start": "重新启动服务",
      "service.stop": "停止本次启动的服务",
      "service.scale": "恢复原有副本数量",
    };
    return labels[String(spec.capability || "")] || "执行预设恢复动作";
  }
  return action?.rollback ? "执行已登记的恢复能力" : "无可自动执行的恢复步骤";
}

const CAPABILITY_LABELS: Record<string, string> = {
  "project.context.get": "读取项目上下文",
  "relationship.dependencies": "查询服务依赖",
  "relationship.impact": "分析故障影响",
  "experience.search": "检索项目经验",
  "service.list": "列出服务状态",
  "service.status": "检查服务状态",
  "service.inspect": "查看服务详情",
  "service.logs": "查看服务日志",
  "service.restart": "重启服务",
  "service.start": "启动服务",
  "service.stop": "停止服务",
  "service.scale": "调整服务副本",
  "http.health_check": "检查健康接口",
  "host.disk_usage": "检查磁盘使用情况",
  "host.memory_usage": "检查内存使用情况",
  "host.listening_ports": "检查监听端口",
  "config.update_registered": "修改已登记配置",
  "config.precheck_registered": "检查配置变更前提",
  "config.verify_registered": "验证配置内容",
  "deployment.apply_registered": "执行已登记部署",
  "deployment.precheck_registered": "检查部署配方",
  "deployment.verify_registered": "验证部署状态",
};

export function humanCapability(value?: string | null): string {
  return value ? CAPABILITY_LABELS[value] || value : "";
}

export function humanEvidenceSummary(evidence: Pick<Evidence, "summary" | "capability_name">): string {
  const summary = String(evidence.summary || "").trim();
  if (!summary) return humanCapability(evidence.capability_name) || "工具执行记录";
  if (/[㐀-鿿]/.test(summary)) return summary;
  const patterns: Array<[RegExp, (match: RegExpMatchArray) => string]> = [
    [/^Read (.+) logs$/i, match => `已读取 ${match[1]} 服务日志`],
    [/^Read (.+) status$/i, match => `已检查 ${match[1]} 服务状态`],
    [/^Restarted (.+)$/i, match => `已重启 ${match[1]} 服务`],
    [/^Started (.+)$/i, match => `已启动 ${match[1]} 服务`],
    [/^Stop(?:p)?ed (.+)$/i, match => `已停止 ${match[1]} 服务`],
    [/^Scaled (.+) to (\d+)$/i, match => `已将 ${match[1]} 调整为 ${match[2]} 个副本`],
    [/^Scaled (.+)$/i, match => `已调整 ${match[1]} 服务副本`],
  ];
  for (const [pattern, format] of patterns) {
    const match = summary.match(pattern);
    if (match) return format(match);
  }
  const exact: Record<string, string> = {
    "Listed services": "已列出服务状态",
    "Listed systemd services": "已列出 systemd 服务状态",
    "Listed deployments": "已列出 Kubernetes Deployment 状态",
    "Read disk usage": "已读取主机磁盘使用情况",
    "Read memory usage": "已读取主机内存使用情况",
    "Read listening TCP ports": "已读取主机监听端口",
    "Project context retrieved": "已读取项目上下文",
    "Verified experience searched": "已检索已验证的项目经验",
  };
  return exact[summary] || humanCapability(evidence.capability_name) || "工具执行记录";
}
