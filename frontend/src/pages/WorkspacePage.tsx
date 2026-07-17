import { FormEvent, MouseEvent as ReactMouseEvent, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Activity, BadgeCheck, BookOpenText, ChevronRight, CircleAlert, CircleCheck, CircleHelp, Code2, Edit3, Folder, FolderOpen, LogOut, MessageSquare, MoreHorizontal, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Pin, PinOff, Plus, RefreshCw, Send, Server, Settings, StopCircle, ThumbsDown, ThumbsUp, Trash2, UserCircle, XCircle } from "lucide-react";
import { cancelCollectorRun, cancelRun, collectContext, createConnection, createEnvironment, createExperience, createProject, createSession, decideApprovalBatch, deleteConnection, deleteEnvironment, deleteExperience, deleteProject, deleteSession, getRun, listActions, listCollectorRuns, listConnections, listEntities, listEnvironments, listEvidence, listExperience, listGeneralRuns, listGeneralSessions, listMessages, listMonitorEvents, listProjects, listRuns, listSessions, listSteps, queueMessage, sendFeedback, testEnvironmentConnection, updateConnection, updateEnvironment, updateExperience, updateProject, updateSession } from "../api/ops";
import type { Action, AgentRun, AgentStep, Approval, ChatMessage, ChatSession, CollectorRun, Connection, Entity, Environment, Evidence, ExperienceItem, MonitorEvent, Project, User } from "../api/types";
import { ProjectConfigDialog, type ProjectConfigurationValue } from "../components/ProjectConfigDialog";
import { applyApprovalBatchResult, humanCapability, humanEvidenceSummary, isRunPollingTerminal, rollbackDescription, shouldApplySessionResult } from "../uiState";

type Tab = "activity" | "experience" | "config";
type Menu = {type:"project"|"session";id:number;x:number;y:number}|null;
type ApprovalSubmission = {runId:string;decision:"approve"|"reject"}|null;
type ProjectConfigTarget = {project:Project|null;environment:Environment|null;connection:Connection|null}|null;
type TextDialogState = {title:string;label:string;value:string;submitLabel:string;onSubmit:(value:string)=>Promise<void>}|null;
type ConfirmDialogState = {title:string;message:string;confirmLabel:string;onConfirm:()=>Promise<void>}|null;

export function WorkspacePage({user,onLogout}:{user:User;onLogout:()=>void|Promise<void>}) {
  const [projects,setProjects]=useState<Project[]>([]), [sessions,setSessions]=useState<ChatSession[]>([]), [messages,setMessages]=useState<ChatMessage[]>([]);
  const [environments,setEnvironments]=useState<Environment[]>([]), [connections,setConnections]=useState<Connection[]>([]), [runs,setRuns]=useState<AgentRun[]>([]), [experience,setExperience]=useState<ExperienceItem[]>([]), [entities,setEntities]=useState<Entity[]>([]), [collectorRuns,setCollectorRuns]=useState<CollectorRun[]>([]), [monitorEvents,setMonitorEvents]=useState<MonitorEvent[]>([]);
  const [projectId,setProjectId]=useState<number|null>(null), [sessionId,setSessionId]=useState<number|null>(null), [input,setInput]=useState(""), [sending,setSending]=useState(false);
  const [projectsReady,setProjectsReady]=useState(false);
  const [activeRunId,setActiveRunId]=useState<string|null>(null), [cancelling,setCancelling]=useState(false);
  const [tab,setTab]=useState<Tab>("activity"), [leftCollapsed,setLeftCollapsed]=useState(false), [rightCollapsed,setRightCollapsed]=useState(false), [navOpen,setNavOpen]=useState(false), [menu,setMenu]=useState<Menu>(null);
  const [notice,setNotice]=useState<{kind:"success"|"error"|"info";text:string}|null>(null), [approvalBusy,setApprovalBusy]=useState<ApprovalSubmission>(null), [collectorRefreshKey,setCollectorRefreshKey]=useState(0);
  const [projectConfigTarget,setProjectConfigTarget]=useState<ProjectConfigTarget>(null), [textDialog,setTextDialog]=useState<TextDialogState>(null), [confirmDialog,setConfirmDialog]=useState<ConfirmDialogState>(null);
  const endRef=useRef<HTMLDivElement|null>(null), messageRefs=useRef<Record<number,HTMLElement|null>>({}), currentSessionRef=useRef<number|null>(null);
  const seenMonitorEvents=useRef(new Set<string>());
  const project=useMemo(()=>projects.find(x=>x.id===projectId)||null,[projects,projectId]);
  const session=useMemo(()=>sessions.find(x=>x.id===sessionId)||null,[sessions,sessionId]);
  const menuProject=useMemo(()=>menu?.type==="project"?projects.find(item=>item.id===menu.id)||null:null,[menu,projects]);
  const menuSession=useMemo(()=>menu?.type==="session"?sessions.find(item=>item.id===menu.id)||null:null,[menu,sessions]);
  const activeEnvironment=useMemo(()=>environments.find(item=>item.id===session?.environment_id)||environments.find(item=>item.is_default)||environments[0]||null,[environments,session]);

  useEffect(()=>{void refreshProjects();},[]);
  useEffect(()=>{
    if(!projectsReady)return;
    let disposed=false;
    currentSessionRef.current=null;
    setSessionId(null);
    setMessages([]);
    setCollectorRuns([]);
    setEnvironments([]);
    setConnections([]);
    setExperience([]);
    setEntities([]);
    setMonitorEvents([]);
    async function load(){
      try{
        if(projectId===null){
          const [s,r]=await Promise.all([listGeneralSessions(),listGeneralRuns()]);
          if(disposed)return;
          setSessions(s);setRuns(r);
          const selected=s[0]||await createSession(null);
          if(disposed)return;
          if(!s[0])setSessions([selected]);
          setSessionId(selected.id);
          return;
        }
        const [s,e,r,x,c]=await Promise.all([listSessions(projectId),listEnvironments(projectId),listRuns(projectId),listExperience(projectId),listConnections(projectId)]);
        const environmentId=s[0]?.environment_id||e.find(item=>item.is_default)?.id||e[0]?.id;
        const n=await listEntities(projectId,environmentId||undefined);
        if(disposed)return;
        setSessions(s);setEnvironments(e);setConnections(c);setRuns(r);setExperience(x.filter(item=>item.trust_status!=="archived"));setEntities(n);
        const selected=s[0]||await createSession(projectId);
        if(disposed)return;
        if(!s[0])setSessions([selected]);
        setSessionId(selected.id);
      }catch(error){if(!disposed)showNotice("error",error instanceof Error?error.message:"项目数据加载失败");}
    }
    void load();
    return()=>{disposed=true;};
  },[projectId,projectsReady]);
  useEffect(()=>{if(!sessionId)return;let disposed=false;Promise.all([listMessages(sessionId),projectId===null?listGeneralRuns(sessionId):listRuns(projectId,sessionId)]).then(([m,r])=>{if(!disposed){setMessages(m);setRuns(r);}}).catch(error=>{if(!disposed)showNotice("error",error instanceof Error?error.message:"聊天记录加载失败");});return()=>{disposed=true;};},[sessionId,projectId]);
  useEffect(()=>{currentSessionRef.current=sessionId;},[sessionId]);
  useEffect(()=>{endRef.current?.scrollIntoView({behavior:"smooth",block:"end"});},[messages,sending]);
  useEffect(()=>{if(!menu)return;const close=()=>setMenu(null);window.addEventListener("resize",close);document.addEventListener("scroll",close,true);return()=>{window.removeEventListener("resize",close);document.removeEventListener("scroll",close,true);};},[menu]);
  useEffect(()=>{const environmentId=activeEnvironment?.id;setCollectorRuns([]);if(!projectId||!environmentId)return;let disposed=false,timer:number|undefined;async function refresh(){try{const rows=await listCollectorRuns(environmentId);if(disposed)return;setCollectorRuns(rows);if(rows.some(item=>item.status==="queued"||item.status==="running")){timer=window.setTimeout(refresh,1200);}else{setEntities(await listEntities(projectId!,environmentId));}}catch(error){if(!disposed)showNotice("error",error instanceof Error?error.message:"采集状态加载失败");}}void refresh();return()=>{disposed=true;if(timer)window.clearTimeout(timer);};},[projectId,activeEnvironment?.id,collectorRefreshKey]);
  useEffect(()=>{const environmentId=activeEnvironment?.id;if(!projectId||!environmentId){setMonitorEvents([]);return;}let disposed=false,timer:number|undefined;async function refresh(){try{const [events,latestEnvironments]=await Promise.all([listMonitorEvents(projectId!,environmentId),listEnvironments(projectId!)]);if(disposed)return;setMonitorEvents(events);setEnvironments(latestEnvironments);const visible=events.find(item=>!["resolved"].includes(item.status));if(visible){const token=`${visible.id}:${visible.occurrence_count}:${visible.status}`;if(!seenMonitorEvents.current.has(token)){seenMonitorEvents.current.add(token);showNotice(visible.status==="remediated"?"success":"error",visible.summary);}}}catch(error){if(!disposed)showNotice("error",error instanceof Error?error.message:"主动巡检状态加载失败");}finally{if(!disposed)timer=window.setTimeout(refresh,5000);}}void refresh();return()=>{disposed=true;if(timer)window.clearTimeout(timer);};},[projectId,activeEnvironment?.id]);

  async function refreshProjects(){const rows=await listProjects();setProjects(rows);setProjectId(value=>value&&rows.some(x=>x.id===value)?value:rows[0]?.id??null);setProjectsReady(true);}
  function showNotice(kind:"success"|"error"|"info",text:string){setNotice({kind,text});}
  async function refreshSession(targetSessionId:number,targetProjectId:number|null){const [m,r]=await Promise.all([listMessages(targetSessionId),targetProjectId===null?listGeneralRuns(targetSessionId):listRuns(targetProjectId,targetSessionId)]);if(shouldApplySessionResult(currentSessionRef.current,targetSessionId)){setMessages(m);setRuns(r);}}
  async function refreshCurrentSession(){if(!sessionId)return;await refreshSession(sessionId,projectId);}
  function newProject(){setProjectConfigTarget({project:null,environment:null,connection:null});}
  async function newSession(){try{const row=await createSession(projectId,"新会话",activeEnvironment?.id);setSessions(x=>[row,...x]);setSessionId(row.id);setMessages([]);showNotice("success","新对话已创建");}catch(error){showNotice("error",error instanceof Error?error.message:"创建对话失败");}}
  async function selectEnvironment(environmentId:number){const existing=sessions.find(item=>item.environment_id===environmentId);if(existing){setSessionId(existing.id);return;}try{const row=await createSession(projectId,"新会话",environmentId);setSessions(items=>[row,...items]);setSessionId(row.id);setMessages([]);showNotice("success","已切换运行环境并创建新对话");}catch(error){showNotice("error",error instanceof Error?error.message:"切换运行环境失败");}}
  async function saveProjectConfiguration(value:ProjectConfigurationValue){const target=projectConfigTarget;if(!target)return;if(!target.project){let createdProject:Project|null=null,createdConnection:Connection|null=null;try{createdProject=await createProject(value.project);if(value.connection)createdConnection=await createConnection(value.connection);const defaultEnvironment=(await listEnvironments(createdProject.id))[0];if(!defaultEnvironment)throw new Error("项目默认环境创建失败");await updateEnvironment(defaultEnvironment.id,{...value.environment,connection_id:createdConnection?.id??null});const rows=await listProjects();setProjects(rows);setProjectId(createdProject.id);setTab("config");setRightCollapsed(false);showNotice("success","项目和运行配置已创建");}catch(error){if(createdConnection)try{await deleteConnection(createdConnection.id);}catch{}if(createdProject)try{await deleteProject(createdProject.id);}catch{}throw error;}return;}const currentProject=target.project;const updatedProject=await updateProject(currentProject.id,value.project);let connectionId:number|null=null;if(value.connection){if(target.connection){const changes={...value.connection};delete changes.connection_type;const updated=await updateConnection(target.connection.id,changes);connectionId=updated.id;}else{connectionId=(await createConnection(value.connection)).id;}}let environment:Environment;if(target.environment)environment=await updateEnvironment(target.environment.id,{...value.environment,connection_id:connectionId});else environment=await createEnvironment(currentProject.id,{...value.environment,connection_id:connectionId});setProjects(items=>sortProjects(items.map(item=>item.id===updatedProject.id?updatedProject:item)));setEnvironments(await listEnvironments(currentProject.id));setConnections(await listConnections(currentProject.id));if(!target.environment)await selectEnvironment(environment.id);showNotice("success",target.environment?"项目配置已更新":"运行环境已创建");}
  function configureEnvironment(item:Environment|null){const linked=item?connections.find(value=>value.id===item.connection_id)||null:null;setProjectConfigTarget({project,environment:item,connection:linked});}
  function requestConfirm(title:string,message:string,confirmLabel:string,onConfirm:()=>Promise<void>){setConfirmDialog({title,message,confirmLabel,onConfirm});}
  async function removeEnvironment(item:Environment){if(!projectId)return;requestConfirm("删除运行环境",`运行环境“${item.name}”将被停用，已有治理记录会保留。`,"删除环境",async()=>{await deleteEnvironment(item.id);const updated=await listEnvironments(projectId);setEnvironments(updated);if(session?.environment_id===item.id&&updated[0])await selectEnvironment(updated[0].id);showNotice("success","运行环境已停用");});}
  async function testConnection(environmentId:number){showNotice("info","正在测试 SSH 连接");try{const result=await testEnvironmentConnection(environmentId);if(projectId)setConnections(await listConnections(projectId));showNotice(result.ok?"success":"error",result.ok?"SSH 连接成功":result.message);}catch(error){showNotice("error",error instanceof Error?error.message:"SSH 连接测试失败");}}
  async function waitForRun(runId:string,targetSessionId:number,targetProjectId:number|null){const deadline=Date.now()+5*60*1000;while(Date.now()<deadline){const run=await getRun(runId);await refreshSession(targetSessionId,targetProjectId);if(isRunPollingTerminal(run.status))return run;await new Promise(resolve=>window.setTimeout(resolve,800));}throw new Error("任务仍在后台运行，请稍后查看活动记录");}
  async function submit(e:FormEvent){e.preventDefault();if(!sessionId||!input.trim()||sending)return;const targetSessionId=sessionId,targetProjectId=projectId,content=input.trim(),tempId=Date.now(),clientRequestId=crypto.randomUUID();setInput("");setSending(true);setMessages(x=>[...x,{id:tempId,session_id:targetSessionId,project_id:targetProjectId,role:"user",content,message_type:"text",metadata_json:{}}]);try{const queued=await queueMessage(targetSessionId,content,clientRequestId);setActiveRunId(queued.run_summary.id);setMessages(x=>x.map(m=>m.id===tempId?queued.user_message:m));await waitForRun(queued.run_summary.id,targetSessionId,targetProjectId);}catch(error){showNotice("error",error instanceof Error?error.message:"请求失败");await refreshSession(targetSessionId,targetProjectId);}finally{setActiveRunId(null);setCancelling(false);setSending(false);}}
  async function stopRun(){if(!activeRunId||cancelling)return;setCancelling(true);try{await cancelRun(activeRunId);}finally{setCancelling(false);}}
  function toggleMenu(event:ReactMouseEvent<HTMLButtonElement>,type:"project"|"session",id:number){event.stopPropagation();if(menu?.type===type&&menu.id===id){setMenu(null);return;}const rect=event.currentTarget.getBoundingClientRect(),width=168,height=132,gap=6,padding=8;const x=Math.max(padding,Math.min(rect.right-width,window.innerWidth-width-padding));const y=rect.bottom+gap+height<=window.innerHeight-padding?rect.bottom+gap:Math.max(padding,rect.top-height-gap);setMenu({type,id,x,y});}
  async function mutateProject(item:Project,action:"rename"|"pin"|"delete"){setMenu(null);if(action==="rename")setTextDialog({title:"重命名项目",label:"项目名称",value:item.name,submitLabel:"保存",onSubmit:async name=>{const row=await updateProject(item.id,{name});setProjects(x=>sortProjects(x.map(v=>v.id===row.id?row:v)));}});else if(action==="pin"){const row=await updateProject(item.id,{is_pinned:!item.is_pinned});setProjects(x=>sortProjects(x.map(v=>v.id===row.id?row:v)));}else requestConfirm("删除项目",`项目“${item.name}”将从项目列表中移除。已有审计记录不会被删除。`,"删除项目",async()=>{await deleteProject(item.id);await refreshProjects();});}
  async function mutateSession(item:ChatSession,action:"rename"|"pin"|"delete"){setMenu(null);if(action==="rename")setTextDialog({title:"重命名聊天",label:"聊天名称",value:item.title,submitLabel:"保存",onSubmit:async title=>{const row=await updateSession(item.id,{title});setSessions(x=>sortSessions(x.map(v=>v.id===row.id?row:v)));}});else if(action==="pin"){const row=await updateSession(item.id,{is_pinned:!item.is_pinned});setSessions(x=>sortSessions(x.map(v=>v.id===row.id?row:v)));}else requestConfirm("删除聊天",`聊天“${item.title}”将被删除，之后不会再显示在聊天列表中。`,"删除聊天",async()=>{await deleteSession(item.id);const next=sessions.filter(v=>v.id!==item.id);setSessions(next);setSessionId(next[0]?.id??null);});}
  async function monitorApprovedRun(run:AgentRun,targetSessionId:number,targetProjectId:number|null,decision:"approve"|"reject"){
    setSending(true);setActiveRunId(run.id);
    try{const finalRun=await waitForRun(run.id,targetSessionId,targetProjectId);if(decision==="reject")showNotice("success","审批批次已拒绝，本次变更未执行");else if(finalRun.status==="completed")showNotice("success","变更执行和验证已经完成");else showNotice("error","审批已记录，但变更流程未成功完成，请查看 Agent 活动");}
    catch(error){showNotice("error",error instanceof Error?error.message:"审批已记录，但执行状态刷新失败");await refreshSession(targetSessionId,targetProjectId);}
    finally{setActiveRunId(null);setSending(false);}
  }
  async function approve(items:Approval[],decision:"approve"|"reject",selectedApprovalIds?:string[]){
    if(approvalBusy||!sessionId)return;
    const pending=items.filter(item=>item.decision==="pending"),runId=String(pending[0]?.action?.run_id||"");
    if(!pending.length||!runId){showNotice("error","审批批次信息不完整，请刷新页面后重试");return;}
    if(decision==="approve"&&!selectedApprovalIds?.length){showNotice("info","请先勾选至少一项要执行的变更");return;}
    const targetSessionId=sessionId,targetProjectId=projectId;
    setApprovalBusy({runId,decision});
    showNotice("info",decision==="approve"?`正在提交 ${selectedApprovalIds!.length} 项批准…`:`正在拒绝 ${pending.length} 项变更…`);
    try{
      const result=await decideApprovalBatch(runId,pending,decision,decision==="approve"?selectedApprovalIds:undefined);
      setMessages(rows=>applyApprovalBatchResult(rows,result.approvals,result.run_summary.status));
      setApprovalBusy(null);
      const approvedCount=result.approvals.filter(item=>item.decision==="approved").length,skippedCount=result.approvals.filter(item=>item.reason_code==="USER_BATCH_NOT_SELECTED").length;
      showNotice("success",decision==="approve"?`已批准 ${approvedCount} 项变更${skippedCount?`，${skippedCount} 项未选择`:""}，任务已进入执行队列`:`已拒绝 ${result.approvals.length} 项变更`);
      void monitorApprovedRun(result.run_summary,targetSessionId,targetProjectId,decision);
    }catch(error){setApprovalBusy(null);showNotice("error",error instanceof Error?error.message:"审批提交失败");await refreshSession(targetSessionId,targetProjectId);}
  }

  return <main className={`workspace ${leftCollapsed?"left-collapsed":""} ${rightCollapsed?"right-collapsed":""}`} onClick={()=>setMenu(null)}>
    {notice&&<div className={`notice ${notice.kind}`}><span>{notice.text}</span><button onClick={e=>{e.stopPropagation();setNotice(null);}}>×</button></div>}
    {projectConfigTarget&&<ProjectConfigDialog project={projectConfigTarget.project} environment={projectConfigTarget.environment} connection={projectConfigTarget.connection} onClose={()=>setProjectConfigTarget(null)} onSave={saveProjectConfiguration}/>}
    {textDialog&&<TextInputDialog {...textDialog} onClose={()=>setTextDialog(null)}/>}
    {confirmDialog&&<ConfirmDialog {...confirmDialog} onClose={()=>setConfirmDialog(null)}/>}
    {menuProject&&menu&&createPortal(<ActionMenu pinned={menuProject.is_pinned} x={menu.x} y={menu.y} onAction={action=>mutateProject(menuProject,action)}/>,document.body)}
    {menuSession&&menu&&createPortal(<ActionMenu pinned={menuSession.is_pinned} x={menu.x} y={menu.y} onAction={action=>mutateSession(menuSession,action)}/>,document.body)}
    {leftCollapsed&&<button className="collapsed-open left-open" onClick={()=>setLeftCollapsed(false)} title="打开左侧栏"><PanelLeftOpen size={18}/></button>}
    {rightCollapsed&&<button className="collapsed-open right-open" onClick={()=>setRightCollapsed(false)} title="打开右侧栏"><PanelRightOpen size={18}/></button>}
    <aside className="glass-panel left-pane">
      <button className="pane-toggle" onClick={()=>setLeftCollapsed(true)} title="关闭左侧栏"><PanelLeftClose size={17}/></button>
      <div className="workspace-brand"><div className="brand-chip"><span>&gt;_</span></div><strong>Ops Agent Chat</strong></div>
      <button className="new-chat-primary" onClick={newSession}><Plus size={17}/>新增对话</button>
      <section className={`left-block project-block ${projects.length>3?"compact":""}`}><div className="block-title"><span>项目</span><button className="mini-create" onClick={newProject} title="新建项目"><Plus size={15}/>新建</button></div><div className="project-scroll">
        <div className={`nav-row ${projectId===null?"selected":""}`}><button className="project-item" onClick={()=>setProjectId(null)}><MessageSquare size={19}/><span>通用聊天</span></button></div>
        {projects.map(item=><div className="nav-entry" key={item.id}><div className={`nav-row ${item.id===projectId?"selected":""}`}><button className="project-item" onClick={()=>setProjectId(item.id)} title={item.name}>{item.id===projectId?<FolderOpen size={19}/>:<Folder size={19}/>}<span>{item.name}</span>{item.is_pinned&&<Pin size={13}/>}</button><MoreButton onClick={event=>toggleMenu(event,"project",item.id)}/></div></div>)}
      </div></section>
      <section className="left-block session-block"><div className="block-title"><span>聊天记录</span><button className="mini-create" onClick={newSession} title="新建聊天"><Plus size={15}/>新聊天</button></div><div className="session-scroll">
        {sessions.map(item=><div className="nav-entry" key={item.id}><div className={`nav-row ${item.id===sessionId?"selected":""}`}><button className="session-item" onClick={()=>setSessionId(item.id)} title={item.title}><MessageSquare size={16}/><span>{item.title}</span>{item.is_pinned&&<Pin size={13}/>}</button><MoreButton onClick={event=>toggleMenu(event,"session",item.id)}/></div></div>)}
      </div></section>
      <button className="logout" onClick={onLogout}><UserCircle size={25}/><span>{user.username}</span><LogOut size={17}/></button>
    </aside>
    <section className="glass-panel chat-pane"><header className="chat-header"><div><strong>{project?.name??"通用聊天"}</strong><span>{session?.title??"新会话"}</span></div><div className="chat-header-actions">{project&&activeEnvironment&&<select className="environment-select" value={activeEnvironment.id} onChange={event=>void selectEnvironment(Number(event.target.value))} title="当前运行环境">{environments.map(item=><option value={item.id} key={item.id}>{item.name}</option>)}</select>}<button className={`outline-toggle ${navOpen?"active":""}`} onClick={()=>setNavOpen(x=>!x)} title="消息导航"><MessageSquare size={17}/></button><span className="mode-badge">受控运维</span></div></header>
      <MessageNav open={navOpen} messages={messages} jump={id=>messageRefs.current[id]?.scrollIntoView({behavior:"smooth",block:"center"})}/>
      <div className="message-list">{messages.length===0&&!sending&&<div className="empty-chat"><div className="empty-mark">&gt;_</div><h2>我能帮你处理什么？</h2><p>可以问通用问题，也可以调查当前项目或提出受控变更。</p></div>}
        {messages.map(m=><MessageView key={m.id} message={m} setRef={el=>{messageRefs.current[m.id]=el;}} onApproval={approve} approvalBusy={approvalBusy}/>) }{sending&&<div className="message assistant"><div className="avatar bot-avatar"><Code2 size={18}/></div><div className="assistant-card"><div className="typing-line"><span/><span/><span/></div></div></div>}<div ref={endRef}/></div>
      <form className="composer" onSubmit={submit}><input value={input} onChange={e=>setInput(e.target.value)} placeholder="输入问题或描述要完成的任务" disabled={sending}/>{activeRunId?<button type="button" className="stop-run" onClick={stopRun} disabled={cancelling}><StopCircle size={18}/>{cancelling?"停止中":"停止"}</button>:<button disabled={sending}><Send size={18}/>发送</button>}</form>
    </section>
    <aside className="glass-panel right-pane"><button className="pane-toggle" onClick={()=>setRightCollapsed(true)} title="关闭右侧栏"><PanelRightClose size={17}/></button><div className="right-pane-head"><div className="tabs"><button className={tab==="activity"?"active":""} onClick={()=>setTab("activity")}><Activity size={16}/>活动</button><button className={tab==="experience"?"active":""} onClick={()=>setTab("experience")}><BookOpenText size={16}/>经验</button><button className={tab==="config"?"active":""} onClick={()=>setTab("config")}><Settings size={16}/>配置</button></div></div>
      {tab==="activity"&&<ActivityPanel runs={runs} monitorEvents={monitorEvents}/>}
      {tab==="experience"&&(projectId?<ExperiencePanel items={experience} projectId={projectId} onChange={setExperience} onRequestDelete={(item,onDelete)=>requestConfirm("删除项目经验",`经验“${item.title}”将被删除。`,"删除经验",onDelete)}/>:<div className="side-card"><p className="empty-note">通用聊天不使用项目经验。</p></div>)}
      {tab==="config"&&<ConfigPanel
        project={project}
        environment={activeEnvironment}
        environments={environments}
        connections={connections}
        canManage={Boolean(project&&project.owner_id===user.id)}
        entities={entities}
        collectorRuns={collectorRuns}
        onCollect={async()=>{
          if(!activeEnvironment)return;
          try{
            const rows=await collectContext(activeEnvironment.id);
            setCollectorRuns(rows);
            setCollectorRefreshKey(value=>value+1);
            showNotice("info","上下文采集已排队，完成后会自动刷新");
          }catch(error){showNotice("error",error instanceof Error?error.message:"上下文采集失败");}
        }}
        onCancel={async id=>{
          try{
            const row=await cancelCollectorRun(id);
            setCollectorRuns(items=>items.map(item=>item.id===row.id?row:item));
            showNotice("success",row.status==="cancelled"?"采集任务已取消":"取消请求已记录，正在等待采集器安全停止");
          }catch(error){showNotice("error",error instanceof Error?error.message:"取消采集失败");}
        }}
        onDeleteEnvironment={removeEnvironment}
        onTestConnection={testConnection}
        onConfigure={configureEnvironment}
      />}
    </aside>
  </main>;
}

function MoreButton({onClick}:{onClick:(event:ReactMouseEvent<HTMLButtonElement>)=>void}){return <button className="row-menu-trigger" onClick={onClick} title="更多"><MoreHorizontal size={17}/></button>}
function ActionMenu({pinned,x,y,onAction}:{pinned:boolean;x:number;y:number;onAction:(a:"rename"|"pin"|"delete")=>void}){return <div className="action-menu" style={{left:x,top:y}} onClick={e=>e.stopPropagation()}><button onClick={()=>onAction("rename")}><Edit3 size={16}/>重命名</button><button onClick={()=>onAction("pin")}>{pinned?<PinOff size={16}/>:<Pin size={16}/>} {pinned?"取消置顶":"置顶"}</button><button className="danger" onClick={()=>onAction("delete")}><Trash2 size={16}/>删除</button></div>}

function TextInputDialog({title,label,value,submitLabel,onSubmit,onClose}:{title:string;label:string;value:string;submitLabel:string;onSubmit:(value:string)=>Promise<void>;onClose:()=>void}){const [text,setText]=useState(value),[saving,setSaving]=useState(false),[error,setError]=useState("");const inputRef=useRef<HTMLInputElement|null>(null);useEffect(()=>{inputRef.current?.focus();inputRef.current?.select();},[]);async function submit(event:FormEvent){event.preventDefault();const next=text.trim();if(!next||saving)return;setSaving(true);setError("");try{await onSubmit(next);onClose();}catch(value){setError(value instanceof Error?value.message:"保存失败");setSaving(false);}}return createPortal(<div className="dialog-backdrop" onMouseDown={event=>event.target===event.currentTarget&&!saving&&onClose()}><section className="small-dialog" role="dialog" aria-modal="true" aria-labelledby="text-dialog-title"><header className="dialog-header"><h2 id="text-dialog-title">{title}</h2><button className="dialog-close" onClick={onClose} disabled={saving} aria-label="关闭"><XCircle size={18}/></button></header><form onSubmit={submit}><label><span>{label}</span><input ref={inputRef} value={text} onChange={event=>setText(event.target.value)} maxLength={200} required/></label>{error&&<p className="dialog-error">{error}</p>}<footer className="dialog-actions"><button type="button" onClick={onClose} disabled={saving}>取消</button><button className="primary" disabled={saving||!text.trim()}>{saving?"保存中...":submitLabel}</button></footer></form></section></div>,document.body)}

function ConfirmDialog({title,message,confirmLabel,onConfirm,onClose}:{title:string;message:string;confirmLabel:string;onConfirm:()=>Promise<void>;onClose:()=>void}){const [saving,setSaving]=useState(false),[error,setError]=useState("");async function confirm(){if(saving)return;setSaving(true);setError("");try{await onConfirm();onClose();}catch(value){setError(value instanceof Error?value.message:"操作失败");setSaving(false);}}return createPortal(<div className="dialog-backdrop" onMouseDown={event=>event.target===event.currentTarget&&!saving&&onClose()}><section className="small-dialog confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-dialog-title"><header className="dialog-header"><h2 id="confirm-dialog-title">{title}</h2><button className="dialog-close" onClick={onClose} disabled={saving} aria-label="关闭"><XCircle size={18}/></button></header><p>{message}</p>{error&&<p className="dialog-error">{error}</p>}<footer className="dialog-actions"><button onClick={onClose} disabled={saving}>取消</button><button className="danger-confirm" onClick={()=>void confirm()} disabled={saving}>{saving?"处理中...":confirmLabel}</button></footer></section></div>,document.body)}

function MessageView({message,setRef,onApproval,approvalBusy}:{message:ChatMessage;setRef:(e:HTMLElement|null)=>void;onApproval:(a:Approval[],d:"approve"|"reject",selected?:string[])=>void;approvalBusy:ApprovalSubmission}){
  if(message.role==="user")return <article className="message user" ref={setRef}><div className="user-bubble">{message.content}</div></article>;
  const approvals=message.metadata_json.approvals||[];
  return <article className="message assistant" ref={setRef}><div className="avatar bot-avatar"><Code2 size={18}/></div><div className="assistant-card"><div className="answer-header"><span>Ops Agent</span><small>{String(message.metadata_json.run_status||"")}</small></div><RichText text={message.content}/>{approvals.length>0&&<ApprovalBatchCard items={approvals} busy={approvalBusy} onDecision={onApproval}/>} {Boolean(message.metadata_json.evidence_ids?.length)&&<div className="source-strip"><span>依据</span><em>{message.metadata_json.evidence_ids!.length} 条可追踪证据</em></div>}<Feedback messageId={message.id}/></div></article>;
}
function RichText({text}:{text:string}){return <div className="natural-answer">{text.split(/\n{2,}/).map((p,i)=>{const lines=p.split("\n");return <div key={i} className="answer-paragraph">{lines.map((line,j)=>{const value=line.replace(/^#{1,6}\s*/,"");if(/^#{1,6}\s/.test(line))return <h4 key={j}>{formatInline(value)}</h4>;if(/^[-*]\s+/.test(line))return <p className="answer-bullet" key={j}>{formatInline(value.replace(/^[-*]\s+/,""))}</p>;if(/^\d+\.\s+/.test(line))return <p className="answer-number" key={j}>{formatInline(line)}</p>;return <p key={j}>{formatInline(value)}</p>})}</div>})}</div>}
function formatInline(text:string){return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean).map((part,i)=>part.startsWith("`")&&part.endsWith("`")?<code key={i}>{part.slice(1,-1)}</code>:part.startsWith("**")&&part.endsWith("**")?<strong key={i}>{part.slice(2,-2)}</strong>:part)}
function ApprovalBatchCard({items,busy,onDecision}:{items:Approval[];busy:ApprovalSubmission;onDecision:(items:Approval[],decision:"approve"|"reject",selected?:string[])=>void}){
  const pending=items.filter(item=>item.decision==="pending"),runId=String(items[0]?.action?.run_id||"");
  const pendingKey=pending.map(item=>item.id).join("|"),[selectedIds,setSelectedIds]=useState<string[]>([]);
  useEffect(()=>setSelectedIds(current=>current.filter(id=>pending.some(item=>item.id===id))),[pendingKey]);
  const submitting=busy?.runId===runId,locked=Boolean(busy),multiple=items.length>1;
  const allSelected=pending.length>0&&selectedIds.length===pending.length,approvedCount=items.filter(item=>item.decision==="approved").length;
  const title=submitting?(busy?.decision==="approve"?"正在提交所选变更":"正在拒绝整批变更"):pending.length?`选择要执行的变更${multiple?`（共 ${pending.length} 项）`:""}`:"审批已处理";
  const state=submitting?"正在提交":pending.length?`已选 ${selectedIds.length}/${pending.length}`:items.every(item=>item.decision==="approved")?"全部已批准":approvedCount?`已批准 ${approvedCount} 项`:"未执行";
  function toggle(id:string,checked:boolean){setSelectedIds(current=>checked?[...current,id]:current.filter(value=>value!==id));}
  function toggleAll(){setSelectedIds(allSelected?[]:pending.map(item=>item.id));}
  return <section className="approval-card approval-batch"><div className="approval-title">{submitting?<RefreshCw className="spinning" size={18}/>:<CircleCheck size={18}/>}<strong>{title}</strong><span className={`approval-state ${submitting?"submitting":pending.length?"pending":"decided"}`}>{state}</span></div>
    <div className="approval-batch-guidance"><p className="approval-batch-note">{pending.length?"只会执行已勾选的变更；未勾选项会记录为未执行。每个已执行操作仍会分别验证最终状态。":"该批次已经完成审批处理，执行结果请查看当前回答和 Agent 活动。"}</p>{pending.length>1&&<button disabled={locked} onClick={toggleAll}>{allSelected?"清空":"全选"}</button>}</div>
    <div className="approval-batch-items">{items.map(item=>{const view=approvalView(item),selectable=item.decision==="pending",selected=selectedIds.includes(item.id),decisionClass=item.reason_code==="USER_BATCH_NOT_SELECTED"?"skipped":item.decision;return <details className={`approval-item ${selected?"selected":""}`} key={item.id}><summary>{selectable?<label className="approval-select" onClick={event=>event.stopPropagation()}><input type="checkbox" checked={selected} disabled={locked} onChange={event=>toggle(item.id,event.target.checked)} aria-label={`选择${view.title}`}/></label>:<span className="approval-select-placeholder"/>}<span><strong>{view.title}</strong><small>{item.action?.risk_level||"L2"} · {humanCapability(item.action?.capability_name)}</small></span><em className={decisionClass}>{approvalDecisionText(item)}</em></summary><div className="approval-item-body"><p><b>影响</b><span>{view.impact}</span></p><p><b>风险</b><span>{view.risk}</span></p><div className="approval-item-technical"><p><b>目标</b><code>{view.target}</code></p><p><b>前置检查</b><span>{humanCapability(item.action?.precheck)||"无"}</span></p><p><b>执行验证</b><span>{humanCapability(item.action?.verifier)||"无"}</span></p><p><b>异常恢复</b><span>{rollbackDescription(item.action)}</span></p><p><b>Action Hash</b><code>{item.action_hash.slice(0,16)}…</code></p><small>有效期至 {new Date(item.expires_at).toLocaleString()}</small></div></div></details>})}</div>
    {pending.length>0&&<div className="approval-actions"><button disabled={locked} onClick={()=>onDecision(pending,"reject")}>{submitting&&busy?.decision==="reject"?<RefreshCw className="spinning" size={16}/>:<XCircle size={16}/>} {submitting&&busy?.decision==="reject"?"正在拒绝":multiple?"全部不执行":"不执行"}</button><button disabled={locked||selectedIds.length===0} className="approve" onClick={()=>onDecision(pending,"approve",selectedIds)}>{submitting&&busy?.decision==="approve"?<RefreshCw className="spinning" size={16}/>:<CircleCheck size={16}/>} {submitting&&busy?.decision==="approve"?"正在提交":`批准并执行（${selectedIds.length}）`}</button></div>}
  </section>
}
function approvalView(item:Approval){const capability=item.action?.capability_name||"";const target=String(item.action?.target_json?.name||item.action?.arguments_json?.service||"当前目标");const verb=capabilityVerb(capability);return {title:`确认${verb}${target}`,impact:naturalImpact(item,verb,target),risk:naturalRisk(item,verb,target),target};}
function capabilityVerb(value:string){return ({"service.restart":"重启服务 ","service.start":"启动服务 ","service.stop":"停止服务 ","service.scale":"调整服务副本 ","config.update_registered":"修改已登记配置 ","deployment.apply_registered":"执行已登记部署 "} as Record<string,string>)[value]||"执行变更 ";}
function naturalImpact(item:Approval,verb:string,target:string){if(item.impact_summary&&!/[a-z]+\.[a-z]+|\{.*\}/i.test(item.impact_summary))return item.impact_summary;return `Agent 准备${verb}${target}。批准后会执行这次变更，并在执行后再次检查服务状态。`;}
function naturalRisk(item:Approval,verb:string,target:string){if(item.risk_summary&&!/[a-z]+\.[a-z]+|requires explicit approval|rollback/i.test(item.risk_summary))return item.risk_summary;const action=verb.trim();return `${action}可能让 ${target} 短暂不可用，正在处理的请求可能中断。当前没有自动回滚步骤，如果执行后状态异常，需要继续人工确认或再次让 Agent 诊断。`;}
function approvalDecisionText(item:Approval){if(item.reason_code==="USER_BATCH_NOT_SELECTED")return "未选择 · 未执行";if(item.decision==="approved")return "已批准";if(item.decision==="rejected")return "已拒绝";if(item.decision==="expired")return "已过期";if(item.decision==="cancelled")return "已取消";if(item.decision==="invalidated")return "已失效";return "待确认";}
function Feedback({messageId}:{messageId:number}){const [done,setDone]=useState("");async function rate(r:string){await sendFeedback(messageId,r);setDone(r)}return <div className="feedback-row"><span>{done?"已记录":"评价回答"}</span>{!done&&<><button onClick={()=>rate("helpful")} title="有帮助"><ThumbsUp size={15}/></button><button onClick={()=>rate("incomplete")} title="不完整"><ThumbsDown size={15}/></button><button onClick={()=>rate("inaccurate")} title="不准确"><CircleAlert size={15}/></button><button onClick={()=>rate("unresolved")} title="未解决"><CircleHelp size={15}/></button></>}</div>}
function MessageNav({open,messages,jump}:{open:boolean;messages:ChatMessage[];jump:(id:number)=>void}){return <aside className={`message-outline ${open?"open":""}`}><h3>消息导航</h3>{messages.map(m=><button key={m.id} className={`message-nav-row ${m.role}`} onClick={()=>jump(m.id)}><span>{m.role==="user"?"你":"Agent"}</span><strong>{m.content.replace(/[#*`]/g,"").slice(0,48)}</strong></button>)}</aside>}

function ActivityPanel({runs,monitorEvents}:{runs:AgentRun[];monitorEvents:MonitorEvent[]}){
  const [selected,setSelected]=useState<string|null>(null), [steps,setSteps]=useState<AgentStep[]>([]), [actions,setActions]=useState<Action[]>([]), [evidence,setEvidence]=useState<Evidence[]>([]);
  const [loading,setLoading]=useState(false), [loadError,setLoadError]=useState("");
  const requestSequence=useRef(0);
  async function expand(id:string){
    const sequence=++requestSequence.current;
    if(selected===id){setSelected(null);return;}
    setSelected(id);setSteps([]);setActions([]);setEvidence([]);setLoadError("");setLoading(true);
    try{const [s,a,e]=await Promise.all([listSteps(id),listActions(id),listEvidence(id)]);if(requestSequence.current===sequence){setSteps(s);setActions(a);setEvidence(e);}}
    catch(error){if(requestSequence.current===sequence)setLoadError(error instanceof Error?error.message:"活动详情加载失败");}
    finally{if(requestSequence.current===sequence)setLoading(false);}
  }
  return <div className="side-card"><h3>Agent 活动</h3>{monitorEvents.length>0&&<section className="monitor-event-list" aria-label="主动巡检事件"><h4>主动巡检</h4>{monitorEvents.slice(0,8).map(item=><div className={`monitor-event ${item.status}`} key={item.id}><StatusDot status={item.status}/><span><strong>{item.summary}</strong><small>{monitorStatusLabel(item.status)} · {new Date(item.last_seen_at).toLocaleString()}{item.occurrence_count>1?` · ${item.occurrence_count} 次`:""}</small></span></div>)}</section>}{runs.length===0&&monitorEvents.length===0&&<p className="empty-note">当前会话暂无活动。</p>}{runs.map(run=><div className={`activity-row ${selected===run.id?"expanded":""}`} key={run.id}><button onClick={()=>expand(run.id)} aria-expanded={selected===run.id}><StatusDot status={run.status}/><span><strong>{goalOf(run)}</strong><small>{runStepLabel(run.current_step||run.status)} · {run.step_count} 步</small></span><ChevronRight className="activity-chevron" size={16}/></button>{selected===run.id&&<div className="activity-detail">{loading&&<p className="activity-note">正在加载活动详情...</p>}{loadError&&<p className="activity-error">{loadError}</p>}{!loading&&!loadError&&<>{run.status==="failed"&&<p className="activity-error">运行失败：{runErrorLabel(run.error_code)}</p>}<div className="step-list">{steps.map(step=><p key={step.id}><b>{step.sequence}. {stepLabel(step.step_type)}</b><span className={step.status}>{stepStatusLabel(step.status)}</span></p>)}</div>{actions.map(a=><p key={a.id}><code>{humanCapability(a.capability_name)||a.capability_name}</code><span>{actionStatusLabel(a.status)}</span></p>)}{evidence.map(e=><details key={e.id} className="evidence-detail"><summary><b>{humanEvidenceSummary(e)}</b><small>{new Date(e.observed_at).toLocaleTimeString()}</small></summary><pre>{JSON.stringify(e.data_json,null,2)}</pre><em>{e.fresh_until?`有效至 ${new Date(e.fresh_until).toLocaleTimeString()}`:"静态上下文"}</em></details>)}{actions.length===0&&evidence.length===0&&<p className="activity-note">本次请求未产生工具调用或运行证据。</p>}</>}</div>}</div>)}</div>;
}
function StatusDot({status}:{status:string}){return <i className={`status-dot ${status}`}/>}function goalOf(run:AgentRun){return String(run.request_json?.summary||run.request_json?.goal||"Agent 请求")}
function stepLabel(value:string){return ({resolve_capabilities:"解析可用能力",decision:"模型决策",policy:"策略检查",execute:"工具执行",await_approval:"等待审批",finish:"生成结果"} as Record<string,string>)[value]||value;}
function runErrorLabel(value?:string){return ({DECISION_FAILED:"模型决策失败",DECISION_INVALID:"模型执行计划未通过安全校验",MODEL_CALL_FAILED:"模型服务调用失败",RUN_TIMEOUT:"处理超时",WORKER_LEASE_EXPIRED:"Worker 心跳超时"} as Record<string,string>)[value||""]||value||"未知错误";}
function monitorStatusLabel(value:string){return ({open:"需要处理",remediating:"正在自动修复",remediated:"已自动修复",resolved:"已恢复",remediation_failed:"自动修复失败"} as Record<string,string>)[value]||value;}
function runStepLabel(value:string){return ({queued_new:"等待处理",queued_resume:"等待恢复",starting:"开始处理",resuming:"恢复执行",finish:"已结束",queued:"等待处理",running:"处理中",waiting_for_approval:"等待审批",completed:"已完成",failed:"失败",cancelled:"已取消"} as Record<string,string>)[value]||value}function stepStatusLabel(value:string){return ({running:"处理中",success:"完成",failed:"失败",cancelled:"已取消"} as Record<string,string>)[value]||value}function actionStatusLabel(value:string){return ({proposed:"已提出",ready:"待执行",waiting_for_approval:"等待审批",approved:"已批准",executing:"执行中",succeeded:"执行成功",verified:"验证通过",failed:"执行失败",denied:"已拒绝",needs_clarification:"需要补充信息",precheck_failed:"前置检查失败",precheck_changed:"执行前状态已变化",rejected:"审批已拒绝",expired:"审批已过期",cancelled:"已取消",approval_invalid:"审批已失效",verification_failed:"验证失败",rolled_back:"已恢复",rollback_failed:"恢复失败",execution_unknown:"执行结果未知"} as Record<string,string>)[value]||value}
function ExperiencePanel({items,projectId,onChange,onRequestDelete}:{items:ExperienceItem[];projectId:number;onChange:(x:ExperienceItem[])=>void;onRequestDelete:(item:ExperienceItem,onDelete:()=>Promise<void>)=>void}){const [saving,setSaving]=useState(false);async function upload(file?:File){if(!file)return;setSaving(true);try{const content=await file.text();const row=await createExperience(projectId,{title:file.name,content,trust_status:"draft",tags:["uploaded"]});onChange([row,...items]);}finally{setSaving(false)}}async function verify(item:ExperienceItem){const row=await updateExperience(item.id,{trust_status:"verified"});onChange(items.map(x=>x.id===row.id?row:x))}function remove(item:ExperienceItem){onRequestDelete(item,async()=>{await deleteExperience(item.id);onChange(items.filter(x=>x.id!==item.id));});}return <div className="side-card"><h3>项目经验</h3><label className="upload-line"><input type="file" accept=".md,.txt" onChange={e=>upload(e.target.files?.[0])}/><span>{saving?"保存中":"添加文档"}</span></label>{items.map(x=><div className="doc-row" key={x.id}><span><strong>{x.title}</strong><small>{x.trust_status==="verified"?"已验证":"草稿"} · {x.item_type}</small></span><div>{x.trust_status!=="verified"&&<button className="icon-action" onClick={()=>verify(x)} title="标记为已验证"><BadgeCheck size={15}/></button>}<button className="icon-action" onClick={()=>remove(x)} title="删除经验"><Trash2 size={15}/></button></div></div>)}</div>}
function ConfigPanel({project,environment,environments,connections,canManage,entities,collectorRuns,onCollect,onCancel,onDeleteEnvironment,onTestConnection,onConfigure}:{project:Project|null;environment:Environment|null;environments:Environment[];connections:Connection[];canManage:boolean;entities:Entity[];collectorRuns:CollectorRun[];onCollect:()=>Promise<void>;onCancel:(id:number)=>Promise<void>;onDeleteEnvironment:(item:Environment)=>Promise<void>;onTestConnection:(environmentId:number)=>Promise<void>;onConfigure:(item:Environment|null)=>void}){
  const active=collectorRuns.some(item=>item.status==="queued"||item.status==="running"),connection=connections.find(item=>item.id===environment?.connection_id)||null;
  if(!project)return <div className="side-card"><p className="empty-note">请选择项目。</p></div>;
  return <div className="side-card config-list">
    <div className="config-heading"><div><h3>项目配置</h3><small>当前项目和运行环境摘要</small></div><div>{canManage&&<button className="config-edit-button" onClick={()=>onConfigure(environment)}><Edit3 size={15}/>编辑配置</button>}<button className="icon-action" disabled={!environment?.connection_id} onClick={()=>environment&&void onTestConnection(environment.id)} title={environment?.connection_id?"测试当前环境的 SSH 连接":"当前环境未关联 SSH 连接"}><Server size={16}/></button><button className={`icon-action ${active?"spinning":""}`} disabled={active||!environment} onClick={()=>void onCollect()} title={active?"上下文采集中":"采集上下文"}><RefreshCw size={16}/></button></div></div>
    <p><span>项目</span><strong>{project.name}</strong></p><p><span>环境</span><strong>{environment?.name||"未配置"}</strong></p><p><span>运行时</span><strong>{runtimeLabel(environment?.runtime_type)}</strong></p><p><span>工作目录</span><strong>{environment?.workdir||"未配置"}</strong></p><p><span>策略</span><strong>{policyLabel(environment?.policy_profile)}</strong></p><p><span>SSH 连接</span><strong>{connection?`${connection.name} · ${connection.username?`${connection.username}@`:""}${connection.host||"未配置主机"}:${connection.port||22}`:"未配置"}</strong></p><p><span>主机校验</span><strong>{connection?.host_fingerprint_configured?"已配置 Host Key 指纹":"未配置"}</strong></p><p><span>主动巡检</span><strong>{environment?.monitoring_enabled?"已开启":"已关闭"}</strong></p><p><span>自动修复</span><strong>{environment?.auto_remediation_enabled?"已开启":"已关闭"}</strong></p><p><span>最近巡检</span><strong>{environment?.last_monitored_at?new Date(environment.last_monitored_at).toLocaleString():"尚未执行"}</strong></p><p><span>上下文实体</span><strong>{entities.length}</strong></p>
    {canManage&&<section className="environment-admin"><div className="config-subheading"><h4>运行环境</h4><button className="config-add-button" onClick={()=>onConfigure(null)}><Plus size={14}/>新增</button></div>{environments.map(item=><div className="environment-row" key={item.id}><span><strong>{item.name}{item.is_default&&<em>默认</em>}</strong><small>{runtimeLabel(item.runtime_type)} · {policyLabel(item.policy_profile)}</small></span><div><button className="icon-action" onClick={()=>onConfigure(item)} title="编辑环境配置"><Edit3 size={15}/></button><button className="icon-action danger" onClick={()=>void onDeleteEnvironment(item)} title="删除环境"><Trash2 size={15}/></button></div></div>)}</section>}
    {collectorRuns.length>0&&<section className="collector-list"><h4>最近采集</h4>{collectorRuns.slice(0,6).map(item=><div className="collector-row" key={item.id}><StatusDot status={item.status}/><span><strong>{collectorLabel(item.collector_name)}</strong><small>{collectorStatus(item.cancel_requested_at&&item.status==="running"?"cancelling":item.status)}{item.error_message?` · ${item.error_message}`:""}</small></span>{(item.status==="queued"||item.status==="running")&&!item.cancel_requested_at&&<button className="icon-action" onClick={()=>void onCancel(item.id)} title="取消采集"><XCircle size={15}/></button>}</div>)}</section>}
  </div>;
}

function runtimeLabel(value?:string){return ({docker_compose:"Docker Compose",kubernetes:"Kubernetes",systemd:"systemd",manual:"手动配置",mixed:"混合运行时"} as Record<string,string>)[value||""]||value||"未配置"}function policyLabel(value?:string){return ({development:"开发",test:"测试",staging:"预发布",production:"生产"} as Record<string,string>)[value||""]||value||"未配置"}function connectionLabel(id:number|null|undefined,items:Connection[]){if(!id)return "未配置";const item=items.find(value=>value.id===id);return item?`${item.name} · ${item.host||"未配置主机"}`:`已登记连接 #${id}`}function collectorLabel(value:string){return ({manual:"手动配置",docker_compose:"Docker Compose",kubernetes:"Kubernetes",systemd:"systemd",nginx:"Nginx",project_file:"项目文件"} as Record<string,string>)[value]||value}function collectorStatus(value:string){return ({queued:"等待执行",running:"采集中",cancelling:"正在取消",completed:"已完成",failed:"失败",cancelled:"已取消"} as Record<string,string>)[value]||value}
function sortProjects(rows:Project[]){return [...rows].sort((a,b)=>Number(b.is_pinned)-Number(a.is_pinned)||a.name.localeCompare(b.name))}function sortSessions(rows:ChatSession[]){return [...rows].sort((a,b)=>Number(b.is_pinned)-Number(a.is_pinned)||(b.updated_at||"").localeCompare(a.updated_at||""))}
