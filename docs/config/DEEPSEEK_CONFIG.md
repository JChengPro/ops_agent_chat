# LLM Provider 配置

后端通过 OpenAI 兼容接口调用结构化 Decision 模型，默认配置示例使用 DeepSeek，但业务代码不固定模型名称。

```env
DEEPSEEK_API_KEY=replace-with-your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_PROVIDER=deepseek
LLM_MODEL=填写账号实际可用的模型名称
LLM_TIMEOUT_SECONDS=90
```

Decision 使用 JSON Schema 输出目标、范围、时效、副作用和下一步工具调用。模型不能直接执行命令；输出还必须经过 Capability Schema 和 Policy Engine。格式修复最多一次，再次失败会安全降级并要求用户澄清，不会回退到关键词分类。

每条用户消息创建一个独立 Run。系统不设置固定的工具调用次数或 Graph 步数上限；用户取消、运行总时长、单次模型请求和 Runtime 命令超时仍作为安全边界。

当前接口使用非流式响应。模型调用会记录 provider、model、用途、耗时、token 数（Provider 返回时）和请求哈希，但不保存 API key。
