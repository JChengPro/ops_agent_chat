import assert from "node:assert/strict";
import test from "node:test";

import { validateRegistration } from "../src/authState.ts";
import { applyApprovalBatchResult, chatMessagesRevision, environmentMonitoringStatus, humanCapability, humanEvidenceSummary, isRunPollingTerminal, markApprovalDecision, monitorEventSnapshot, monitorNoticeFor, rollbackDescription, shouldApplySessionResult, shouldNotifyMonitorEvent } from "../src/uiState.ts";
import { modelSettingsSummary } from "../src/modelSettingsState.ts";

test("registration form validates identity, password and optional invite code", () => {
  const valid = {username: "new-user", email: "new@example.test", password: "secure-pass-123", passwordConfirmation: "secure-pass-123", inviteCode: "invite"};
  assert.equal(validateRegistration(valid, false), null);
  assert.match(validateRegistration({...valid, username: "x"}, false), /用户名/);
  assert.match(validateRegistration({...valid, email: "invalid"}, false), /邮箱/);
  assert.match(validateRegistration({...valid, password: "password", passwordConfirmation: "password"}, false), /10/);
  assert.match(validateRegistration({...valid, passwordConfirmation: "secure-pass-456"}, false), /不一致/);
  assert.match(validateRegistration({...valid, inviteCode: ""}, true), /注册码/);
});

test("model settings summary identifies the effective per-account model", () => {
  assert.deepEqual(modelSettingsSummary({
    provider: "dashscope", base_url: "https://example.test/v1", model: "qwen-plus",
    api_key_configured: true, api_key_source: "user", source: "user", allowed_base_urls: [],
    deployment_default: {provider: "deepseek", base_url: "https://api.deepseek.com", model: "deepseek-chat", configured: true, source: "deployment"},
    embedding: {provider: "dashscope", model: "qwen3.7-text-embedding", dimensions: 1536, configured: true, source: "deployment"},
  }), {
    agent: {
      modelLabel: "DashScope · qwen-plus",
      sourceLabel: "当前账号的个人配置",
      statusLabel: "API Key 已配置（个人）",
    },
    deployment: {
      modelLabel: "DeepSeek · deepseek-chat",
      sourceLabel: "个人配置删除后自动使用",
      statusLabel: "服务器默认 API Key 已配置",
    },
    embedding: {
      modelLabel: "DashScope · qwen3.7-text-embedding",
      sourceLabel: "服务器 RAG 配置 · 1536 维",
      statusLabel: "已配置",
    },
  });
  assert.equal(modelSettingsSummary({
    provider: "deepseek", base_url: "https://example.test", model: "deepseek-v4-pro",
    api_key_configured: true, api_key_source: "deployment", source: "deployment", allowed_base_urls: [],
    deployment_default: {provider: "deepseek", base_url: "https://api.deepseek.com", model: "deepseek-v4-pro", configured: true, source: "deployment"},
    embedding: {provider: "openai-compatible", model: "text-embedding", dimensions: 1536, configured: false, source: "deployment"},
  }).agent.sourceLabel, "服务器部署默认配置");
});

test("run polling stops for approval and terminal states", () => {
  assert.equal(isRunPollingTerminal("queued"), false);
  assert.equal(isRunPollingTerminal("running"), false);
  for (const status of ["completed", "failed", "cancelled", "waiting_for_approval"]) {
    assert.equal(isRunPollingTerminal(status), true);
  }
});

test("monitoring status makes disabled and remediation modes explicit", () => {
  assert.deepEqual(environmentMonitoringStatus(null), {
    tone: "disabled", label: "巡检未配置", detail: "当前会话没有关联运行环境。",
  });
  assert.equal(environmentMonitoringStatus({monitoring_enabled: false, auto_remediation_enabled: true}).label, "巡检关 · 自动修复不生效");
  assert.equal(environmentMonitoringStatus({monitoring_enabled: true, auto_remediation_enabled: false}).tone, "warning");
  assert.equal(environmentMonitoringStatus({monitoring_enabled: true, auto_remediation_enabled: true}).label, "巡检开 · 自动修复开");
});

test("optimistic approval update only changes the selected approval", () => {
  const rows = [{
    id: 1, session_id: 1, project_id: 1, role: "assistant", content: "confirm", message_type: "approval",
    metadata_json: {approvals: [{id: "a", decision: "pending"}, {id: "b", decision: "pending"}]},
  }];
  const updated = markApprovalDecision(rows, "b", "approved");
  assert.equal(updated[0].metadata_json.approvals[0].decision, "pending");
  assert.equal(updated[0].metadata_json.approvals[1].decision, "approved");
});

test("batch approval acknowledgement updates every returned approval and the run state", () => {
  const rows = [{
    id: 1, session_id: 1, project_id: 1, role: "assistant", content: "confirm", message_type: "approval",
    metadata_json: {run_status: "waiting_for_approval", approvals: [{id: "a", decision: "pending"}, {id: "b", decision: "pending"}]},
  }, {
    id: 2, session_id: 1, project_id: 1, role: "assistant", content: "older", message_type: "text",
    metadata_json: {approvals: [{id: "other", decision: "pending"}]},
  }];
  const updated = applyApprovalBatchResult(rows, [{id: "a", decision: "approved"}, {id: "b", decision: "rejected", reason_code: "USER_BATCH_NOT_SELECTED"}], "queued");
  assert.equal(updated[0].metadata_json.run_status, "queued");
  assert.deepEqual(updated[0].metadata_json.approvals.map(item => item.decision), ["approved", "rejected"]);
  assert.equal(updated[0].metadata_json.approvals[1].reason_code, "USER_BATCH_NOT_SELECTED");
  assert.equal(updated[1], rows[1]);
});

test("approval recovery text reflects the immutable rollback snapshot", () => {
  assert.equal(rollbackDescription({rollback_spec_json: {kind: "config_backup"}}), "恢复变更前的配置文件");
  assert.equal(rollbackDescription({rollback_spec_json: {kind: "capability", capability: "service.start"}}), "重新启动服务");
});

test("late polling results cannot replace a newly selected session", () => {
  assert.equal(shouldApplySessionResult(12, 12), true);
  assert.equal(shouldApplySessionResult(13, 12), false);
  assert.equal(shouldApplySessionResult(null, 12), false);
});

test("message polling detects final answers and approval state changes", () => {
  const base = {id:1,session_id:1,project_id:null,role:"assistant",content:"",message_type:"approval",metadata_json:{run_status:"running",approvals:[]}};
  const completed = {...base,content:"处理完成",metadata_json:{...base.metadata_json,run_status:"completed"}};
  const approved = {...base,metadata_json:{run_status:"waiting_for_approval",approvals:[{id:"approval-1",decision:"approved",consumed_at:"now"}]}};
  assert.notEqual(chatMessagesRevision([base]),chatMessagesRevision([completed]));
  assert.notEqual(chatMessagesRevision([base]),chatMessagesRevision([approved]));
  assert.equal(chatMessagesRevision([completed]),chatMessagesRevision([{...completed}]));
  const withKnowledge = {...completed, metadata_json: {...completed.metadata_json, system_knowledge_sources: [{id:"ssh_host_key_mismatch",title:"SSH 主机指纹不一致"}]}};
  assert.notEqual(chatMessagesRevision([completed]), chatMessagesRevision([withKnowledge]));
});

test("activity labels localize capabilities and historical English evidence", () => {
  assert.equal(humanCapability("host.memory_usage"), "检查内存使用情况");
  assert.equal(humanCapability("service.list"), "列出服务状态");
  assert.equal(humanEvidenceSummary({capability_name: "service.logs", summary: "Read backend logs"}), "已读取 backend 服务日志");
  assert.equal(humanEvidenceSummary({capability_name: "host.disk_usage", summary: "Read disk usage"}), "已读取主机磁盘使用情况");
  assert.equal(humanEvidenceSummary({capability_name: "service.status", summary: "已检查 worker 服务状态"}), "已检查 worker 服务状态");
});

test("monitor notification replaces a failed remediation with the recovered state", () => {
  const failed = {
    id: "event-1",
    status: "remediation_failed",
    summary: "mysql 已执行自动启动，但最终状态验证未通过",
    occurrence_count: 1,
  };
  const recovered = {
    ...failed,
    status: "resolved",
    summary: "mysql 当前状态已恢复正常",
    occurrence_count: 2,
  };
  const failedSnapshot = monitorEventSnapshot(failed);
  assert.equal(shouldNotifyMonitorEvent(failed, undefined, false), true);
  assert.equal(shouldNotifyMonitorEvent(recovered, failedSnapshot, true), true);
  assert.deepEqual(monitorNoticeFor(recovered), {kind: "success", text: "mysql 当前状态已恢复正常"});
  assert.equal(shouldNotifyMonitorEvent(recovered, monitorEventSnapshot(recovered), true), false);
  assert.equal(shouldNotifyMonitorEvent({...recovered, occurrence_count: 3}, monitorEventSnapshot(recovered), true), false);
});

test("historical resolved monitor events do not open a stale notification on initial load", () => {
  const recovered = {id: "event-2", status: "resolved", summary: "frontend 当前状态已恢复正常"};
  assert.equal(shouldNotifyMonitorEvent(recovered, undefined, false), false);
});
