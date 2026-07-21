import { FormEvent, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { KeyRound, RotateCcw, X } from "lucide-react";

import { getLLMSettings, resetLLMSettings, updateLLMSettings } from "../api/ops";
import type { LLMSettings } from "../api/types";


export function ModelSettingsDialog({onClose,onSaved}:{onClose:()=>void;onSaved:(message:string)=>void}) {
  const [settings,setSettings]=useState<LLMSettings|null>(null);
  const [provider,setProvider]=useState("deepseek"),[baseUrl,setBaseUrl]=useState(""),[model,setModel]=useState(""),[apiKey,setApiKey]=useState("");
  const [saving,setSaving]=useState(false),[error,setError]=useState("");

  useEffect(()=>{let active=true;getLLMSettings().then(value=>{if(!active)return;setSettings(value);setProvider(value.provider);setBaseUrl(value.base_url);setModel(value.model);}).catch(value=>active&&setError(value instanceof Error?value.message:"模型配置加载失败"));return()=>{active=false;};},[]);

  function selectProvider(value:string){setProvider(value);if(value==="deepseek")setBaseUrl("https://api.deepseek.com");if(value==="openai")setBaseUrl("https://api.openai.com/v1");}
  async function submit(event:FormEvent){event.preventDefault();if(saving)return;setSaving(true);setError("");try{await updateLLMSettings({provider,base_url:baseUrl.trim(),model:model.trim(),...(apiKey.trim()?{api_key:apiKey.trim()}:{})});onSaved("模型配置已保存，后续新任务将使用该配置");onClose();}catch(value){setError(value instanceof Error?value.message:"模型配置保存失败");setSaving(false);}}
  async function reset(){if(saving)return;setSaving(true);setError("");try{await resetLLMSettings();onSaved("已恢复部署默认模型配置");onClose();}catch(value){setError(value instanceof Error?value.message:"恢复默认配置失败");setSaving(false);}}

  return createPortal(<div className="dialog-backdrop" onMouseDown={event=>event.target===event.currentTarget&&!saving&&onClose()}>
    <section className="model-settings-dialog" role="dialog" aria-modal="true" aria-labelledby="model-settings-title">
      <header className="dialog-header"><div><h2 id="model-settings-title">模型设置</h2><p>为当前账号配置 Agent 使用的 OpenAI-compatible 模型。API Key 只会加密保存在服务端。</p></div><button type="button" className="dialog-close" onClick={onClose} disabled={saving} aria-label="关闭"><X size={19}/></button></header>
      {!settings&&!error?<div className="model-settings-loading">正在读取模型配置...</div>:<form onSubmit={submit} className="model-settings-form">
        <div className="model-source"><KeyRound size={16}/><span>当前来源：{settings?.source==="user"?"个人配置":"部署默认配置"}</span><em>{settings?.api_key_configured?`API Key 已配置（${settings.api_key_source==="user"?"个人":"部署"}）`:"尚未配置 API Key"}</em></div>
        <label><span>模型供应商</span><select value={provider} onChange={event=>selectProvider(event.target.value)}>{!["deepseek","openai","openai-compatible"].includes(provider)&&<option value={provider}>{provider}</option>}<option value="deepseek">DeepSeek</option><option value="openai">OpenAI</option><option value="openai-compatible">OpenAI-compatible</option></select></label>
        <label><span>Base URL</span><input value={baseUrl} onChange={event=>setBaseUrl(event.target.value)} list="allowed-model-base-urls" placeholder="https://api.example.com/v1" required/><datalist id="allowed-model-base-urls">{settings?.allowed_base_urls.map(value=><option value={value} key={value}/>)}</datalist><small>出于服务端安全限制，只能使用部署允许列表中的地址。</small></label>
        <label><span>模型名称</span><input value={model} onChange={event=>setModel(event.target.value)} placeholder="填写供应商实际提供的模型 ID" required/></label>
        <label><span>API Key</span><input type="password" value={apiKey} onChange={event=>setApiKey(event.target.value)} autoComplete="new-password" placeholder={settings&&baseUrl.replace(/\/$/,"")!==settings.base_url.replace(/\/$/,"")?"切换服务地址，请填写对应的 API Key":settings?.api_key_configured?"已配置；留空表示不修改":"请输入 API Key"}/><small>保存后无法从网页读取原始 Key；切换服务地址时必须填写该服务对应的 Key。</small></label>
        {error&&<p className="dialog-error">{error}</p>}
        <footer className="dialog-actions"><button type="button" className="reset-model-settings" onClick={()=>void reset()} disabled={saving||settings?.source!=="user"}><RotateCcw size={15}/>恢复部署默认</button><span/><button type="button" onClick={onClose} disabled={saving}>取消</button><button className="primary" disabled={saving||!baseUrl.trim()||!model.trim()}>{saving?"保存中...":"保存设置"}</button></footer>
      </form>}
    </section>
  </div>,document.body);
}
