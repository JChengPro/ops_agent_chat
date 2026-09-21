# Ops Agent Chat 深度教学

本目录用于存放 Ops Agent Chat 的项目教学文档和配套图稿。

> 历史教学基线：`3c8c46c`。正文和图稿未覆盖后续 Redis、RabbitMQ、独立 Maintenance、Profiling 与 Knowledge Path 升级；当前行为请先阅读 [详细设计](../architecture/CURRENT_DESIGN.md)。

## 主教学文档

- [Markdown 版本](OPS_AGENT_CHAT_DEEP_TUTORIAL.md)
- PDF：本地按需生成 `OPS_AGENT_CHAT_DEEP_TUTORIAL.pdf`，不纳入 Git。

建议先完整阅读主教学文档，再按下面的图索引复习单个主题。

## 重新导出 PDF

在项目根目录执行：

导出需要 Python 包 `beautifulsoup4`、`PyMuPDF`、`Markdown`、`Pygments`，以及中文字体 `/usr/share/fonts/truetype/wqy/wqy-microhei.ttc`（Debian/Ubuntu 软件包 `fonts-wqy-microhei`）。

```bash
python docs/tutorial/export_pdf.py
```

导出器会为每张教学图预留完整版面，避免图片因当前页剩余空间不足而被自动缩小。

## 教学图索引

1. [系统总体架构](diagrams/images/01-system-architecture.png)
2. [Docker 部署拓扑](diagrams/images/02-docker-deployment-topology.png)
3. [项目目录与模块关系](diagrams/images/03-project-module-map.png)
4. [一次聊天请求的完整时序](diagrams/images/04-chat-request-sequence.png)
5. [LangGraph 节点流程](diagrams/images/05-langgraph-flow.png)
6. [结构化 AgentDecision](diagrams/images/06-structured-agent-decision.png)
7. [Decision 到 Action](diagrams/images/07-decision-to-action.png)
8. [Capability 精确绑定](diagrams/images/08-capability-binding.png)
9. [Action 不可变执行快照](diagrams/images/09-action-immutable-snapshot.png)
10. [Action Hash 与 Approval](diagrams/images/10-action-hash-approval.png)
11. [Action 状态机](diagrams/images/11-action-state-machine.png)
12. [AgentRun、Worker 与并发控制](diagrams/images/12-agentrun-worker-concurrency.png)
13. [SSH 执行前后安全检查链](diagrams/images/13-ssh-security-checks.png)
14. [Precheck、Execute、Verifier、Rollback](diagrams/images/14-precheck-execute-verify-rollback.png)
15. [Evidence、Claim、Audit 与核心数据关系](diagrams/images/15-evidence-claim-audit-data.png)
16. [主动巡检、诊断与低风险自动修复](diagrams/images/16-monitor-diagnose-remediate.png)

图稿用于辅助理解，具体字段、状态和异常分支以当前代码及后续教学正文中的代码引用为准。

逐图代码对照结果见 [教学图复核记录](diagrams/REVIEW.md)。
