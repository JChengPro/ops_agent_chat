# 前端测试

| 命令 | 状态 | 结果 |
|---|---|---|
| `npm test` | PASS | 4 tests，4 pass，0 fail |
| `npm run build` | PASS | TypeScript 检查和 Vite production build 成功 |
| HTTP smoke | BLOCKED | 当前沙箱不允许本地 preview 监听端口 |
| Playwright E2E | BLOCKED | 仓库未安装 Playwright，当前网络不能安装浏览器依赖 |

已通过的 4 个状态测试覆盖：审批/终态停止轮询、审批乐观状态隔离、恢复文案、切换会话后的晚到结果隔离。

最终生产构建产物：CSS 26.89 kB，JavaScript 242.49 kB；包含 Environment/Connection 配置界面。仅记录构建成功，不把体积视为性能验收。
