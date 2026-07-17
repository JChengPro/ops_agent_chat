# 阻塞与未执行测试

## B-01 交互式浏览器

- 状态：`NOT_TESTED`。
- 原因：仓库未安装 Playwright，当前工具没有可复用的浏览器会话。
- 影响：三个点浮层已通过 TypeScript 构建，但最终视觉位置仍建议在 `http://localhost:5175` 人工确认。

## B-02 宿主 Docker Adapter

- 状态：`BLOCKED`。
- 原因：后端测试容器没有 Docker CLI 和宿主 Docker Socket；这是正常安全边界。
- 已替代验证：主项目镜像构建、Compose up 和四容器运行均实际通过。

## B-03 外部 Runtime

- Kubernetes/systemd：没有隔离目标环境，真实集成未执行。
- VideoHub 自动变更：本轮未执行，避免在修复聊天故障时改变外部项目状态。
