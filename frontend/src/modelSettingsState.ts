import type { LLMSettings } from "./api/types";

const PROVIDER_LABELS: Record<string, string> = {
  dashscope: "DashScope",
  deepseek: "DeepSeek",
  openai: "OpenAI",
  "openai-compatible": "OpenAI-compatible",
};

export function modelSettingsSummary(settings: LLMSettings) {
  const provider = PROVIDER_LABELS[settings.provider] ?? settings.provider;
  const sourceLabel = settings.source === "user" ? "当前账号的个人配置" : "服务器部署默认配置";
  const keySource = settings.api_key_source === "user" ? "个人" : settings.api_key_source === "deployment" ? "部署" : "无";
  return {
    agent: {
      modelLabel: `${provider} · ${settings.model}`,
      sourceLabel,
      statusLabel: settings.api_key_configured ? `API Key 已配置（${keySource}）` : "尚未配置 API Key",
    },
    deployment: {
      modelLabel: `${PROVIDER_LABELS[settings.deployment_default.provider] ?? settings.deployment_default.provider} · ${settings.deployment_default.model}`,
      sourceLabel: settings.source === "user" ? "个人配置删除后自动使用" : "当前正在使用",
      statusLabel: settings.deployment_default.configured ? "服务器默认 API Key 已配置" : "服务器默认 API Key 未配置",
    },
    embedding: {
      modelLabel: `${PROVIDER_LABELS[settings.embedding.provider] ?? settings.embedding.provider} · ${settings.embedding.model}`,
      sourceLabel: `服务器 RAG 配置 · ${settings.embedding.dimensions} 维`,
      statusLabel: settings.embedding.configured ? "已配置" : "未配置，将降级为词法检索",
    },
  };
}
