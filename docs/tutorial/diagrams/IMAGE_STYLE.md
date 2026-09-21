# Ops Agent Chat 教学图视觉规范

- 画布：16:9 横向教学信息图。
- 背景：白色或极浅灰，不使用渐变、霓虹和装饰性背景。
- 主色：深灰文字、蓝色流程、绿色成功、琥珀色审批、红色阻断。
- 结构：从左到右或从上到下，节点数量适中，箭头方向明确。
- 文字：标题使用中文；代码、类名、状态名和字段名保留英文。
- 图标：统一使用简洁线性图标。
- 禁止：品牌 Logo、水印、3D 装饰、复杂阴影、密集段落和无法辨认的小字。

## 图稿清单

1. 系统总体架构
2. Docker 部署拓扑
3. 项目目录关系
4. 聊天请求完整时序
5. LangGraph 节点流程
6. 结构化 Decision
7. Decision 到 Action
8. Capability 精确绑定
9. Action 不可变快照
10. Hash 与 Approval
11. Action 状态机
12. AgentRun 与 Worker
13. SSH 安全检查链
14. Precheck、Execute、Verifier、Rollback
15. Evidence、Claim、Audit 与数据库关系
16. 主动巡检和自动修复
