# VideoHub 项目经验

本目录是默认 VideoHub 项目的经验种子，不是通用百科，也不是所有问答的必经检索路径。系统会将这些资料导入 Experience Store；项目配置以 Environment 和 Context Collector 的结果为准，当前运行状态以本轮 Runtime Evidence 为准。

## 已知范围

- 项目运行时：Docker Compose
- 连接方式：Ops Agent 后端通过受限 SSH Transport 访问宿主环境
- 工作目录、Compose 文件和健康端点：由环境配置注册，模型不能自行指定
- 服务名称：由 Context Collector 采集，不能根据本文猜测

资料出现冲突或过期时，应保留来源并降低可信度，由用户重新验证。
