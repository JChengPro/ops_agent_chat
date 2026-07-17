# 端到端测试

| 流程 | 状态 | 结果 |
|---|---|---|
| 前端单元测试 | PASS | 4/4 |
| 前端 production build | PASS | TypeScript 和 Vite 构建成功 |
| Compose 内前端 HTTP smoke | PASS | SPA 根节点可访问，Nginx `/api` 代理对匿名请求返回 401 |
| Docker Compose 构建和启动 | PASS | PostgreSQL、Backend、Worker、Frontend 全部启动；Backend/PostgreSQL healthy |
| 登录态通用聊天 | PASS | 临时令牌 → 创建通用会话 → `你好` → Worker → DeepSeek → completed |
| 登录态项目诊断 | PASS | API 入队 → Worker → LangGraph → `service.list` → SSH → Docker Compose → Evidence；仅执行 1 个 Action |
| VideoHub 状态回答 | PASS | 回答识别 3 个正常容器，并列出 2 个 exited 和 2 个 restarting 组件 |
| 审计链验证 | PASS | 通用聊天结束后 `valid=true` 且唯一链头 |
| 交互式浏览器/Playwright | NOT_TESTED | 当前仓库未安装 Playwright；菜单视觉交互需人工浏览器确认 |
| 真实 VideoHub 变更审批 | NOT_TESTED | 本轮未对外部目标执行变更 |

主机直接运行 Node HTTP smoke 被沙箱以 `EPERM 127.0.0.1:5175` 拒绝；`curl` 已验证前端 200 和匿名 API 401，且完整登录态项目诊断已通过。
