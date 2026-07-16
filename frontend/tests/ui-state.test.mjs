import assert from "node:assert/strict";
import test from "node:test";

import { isRunPollingTerminal, markApprovalDecision, rollbackDescription, shouldApplySessionResult } from "../src/uiState.ts";

test("run polling stops for approval and terminal states", () => {
  assert.equal(isRunPollingTerminal("queued"), false);
  assert.equal(isRunPollingTerminal("running"), false);
  for (const status of ["completed", "failed", "cancelled", "waiting_for_approval"]) {
    assert.equal(isRunPollingTerminal(status), true);
  }
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

test("approval recovery text reflects the immutable rollback snapshot", () => {
  assert.equal(rollbackDescription({rollback_spec_json: {kind: "config_backup"}}), "恢复变更前的配置文件");
  assert.equal(rollbackDescription({rollback_spec_json: {kind: "capability", capability: "service.start"}}), "重新启动服务");
});

test("late polling results cannot replace a newly selected session", () => {
  assert.equal(shouldApplySessionResult(12, 12), true);
  assert.equal(shouldApplySessionResult(13, 12), false);
  assert.equal(shouldApplySessionResult(null, 12), false);
});
