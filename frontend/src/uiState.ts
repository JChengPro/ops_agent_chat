import type { Action, ChatMessage } from "./api/types";

const POLLING_TERMINAL_STATES = new Set(["completed", "failed", "cancelled", "waiting_for_approval"]);

export function isRunPollingTerminal(status: string): boolean {
  return POLLING_TERMINAL_STATES.has(status);
}

export function shouldApplySessionResult(currentSessionId: number | null, targetSessionId: number): boolean {
  return currentSessionId === targetSessionId;
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
