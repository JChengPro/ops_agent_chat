import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  CircleCheck,
  BookOpenText,
  Code2,
  Edit3,
  FileText,
  FileSearch,
  Folder,
  FolderOpen,
  Lightbulb,
  LogOut,
  MessageSquare,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Pin,
  PinOff,
  Plus,
  Send,
  Settings,
  ShieldAlert,
  Target,
  TerminalSquare,
  Trash2,
  UserCircle,
} from "lucide-react";
import {
  createSession,
  deleteProject,
  deleteSession,
  listCommandRuns,
  listMessages,
  listProjects,
  listRagDocuments,
  listSessions,
  sendMessage,
  updateProject,
  updateSession,
  uploadRagDocument,
} from "../api/ops";
import type { ChatMessage, ChatSession, CommandRun, Project, RagDocument, User } from "../api/types";

type TabKey = "commands" | "runbook" | "config";
type EntityMenu = { type: "project" | "session"; id: number } | null;

export function WorkspacePage({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [runs, setRuns] = useState<CommandRun[]>([]);
  const [docs, setDocs] = useState<RagDocument[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [rightTab, setRightTab] = useState<TabKey>("commands");
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [messageNavOpen, setMessageNavOpen] = useState(false);
  const [openMenu, setOpenMenu] = useState<EntityMenu>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const messageRefs = useRef<Record<number, HTMLElement | null>>({});

  const currentProject = useMemo(() => projects.find((item) => item.id === projectId) ?? null, [projects, projectId]);
  const currentSession = useMemo(() => sessions.find((item) => item.id === sessionId) ?? null, [sessions, sessionId]);

  useEffect(() => {
    listProjects().then((items) => {
      setProjects(items);
      setProjectId(items[0]?.id ?? null);
    });
  }, []);

  useEffect(() => {
    if (!projectId) return;
    setMessages([]);
    Promise.all([listSessions(projectId), listCommandRuns(projectId), listRagDocuments(projectId)]).then(
      async ([sessionItems, runItems, docItems]) => {
        setSessions(sessionItems);
        setRuns(runItems);
        setDocs(docItems);
        if (sessionItems[0]) {
          setSessionId(sessionItems[0].id);
        } else {
          const created = await createSession(projectId);
          setSessions([created]);
          setSessionId(created.id);
        }
      }
    );
  }, [projectId]);

  useEffect(() => {
    if (!sessionId) return;
    Promise.all([listMessages(sessionId), projectId ? listCommandRuns(projectId, sessionId) : Promise.resolve([])]).then(
      ([messageItems, runItems]) => {
        setMessages(messageItems);
        setRuns(runItems);
      }
    );
  }, [sessionId, projectId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, runs, sending]);

  useEffect(() => {
    setOpenMenu(null);
  }, [projectId, sessionId]);

  async function newSession() {
    if (!projectId) return;
    const created = await createSession(projectId);
    setSessions((items) => [created, ...items]);
    setSessionId(created.id);
    setMessages([]);
    setRuns([]);
  }

  async function renameProject(project: Project) {
    setOpenMenu(null);
    const name = window.prompt("重命名项目", project.name)?.trim();
    if (!name || name === project.name) return;
    const updated = await updateProject(project.id, { name });
    setProjects((items) => sortProjects(items.map((item) => (item.id === updated.id ? updated : item))));
  }

  async function toggleProjectPin(project: Project) {
    setOpenMenu(null);
    const updated = await updateProject(project.id, { is_pinned: !project.is_pinned });
    setProjects((items) => sortProjects(items.map((item) => (item.id === updated.id ? updated : item))));
  }

  async function removeProject(project: Project) {
    setOpenMenu(null);
    if (!window.confirm(`删除项目“${project.name}”？聊天和命令记录不会物理删除，但项目会从列表隐藏。`)) return;
    await deleteProject(project.id);
    setProjects((items) => {
      const next = items.filter((item) => item.id !== project.id);
      if (project.id === projectId) setProjectId(next[0]?.id ?? null);
      return next;
    });
    if (project.id === projectId) {
      setSessions([]);
      setMessages([]);
      setRuns([]);
      setDocs([]);
      setSessionId(null);
    }
  }

  async function renameSession(session: ChatSession) {
    setOpenMenu(null);
    const title = window.prompt("重命名聊天", session.title)?.trim();
    if (!title || title === session.title) return;
    const updated = await updateSession(session.id, { title });
    setSessions((items) => sortSessions(items.map((item) => (item.id === updated.id ? updated : item))));
  }

  async function toggleSessionPin(session: ChatSession) {
    setOpenMenu(null);
    const updated = await updateSession(session.id, { is_pinned: !session.is_pinned });
    setSessions((items) => sortSessions(items.map((item) => (item.id === updated.id ? updated : item))));
  }

  async function removeSession(session: ChatSession) {
    setOpenMenu(null);
    if (!window.confirm(`删除聊天“${session.title}”？`)) return;
    await deleteSession(session.id);
    setSessions((items) => {
      const next = items.filter((item) => item.id !== session.id);
      if (session.id === sessionId) setSessionId(next[0]?.id ?? null);
      return next;
    });
    if (session.id === sessionId) {
      setMessages([]);
      setRuns([]);
    }
  }

  function jumpToMessage(messageId: number) {
    messageRefs.current[messageId]?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!sessionId || !input.trim() || sending) return;
    const content = input.trim();
    setInput("");
    setSending(true);
    const optimistic: ChatMessage = {
      id: Date.now(),
      session_id: sessionId,
      project_id: projectId!,
      role: "user",
      content,
      message_type: "text",
      metadata_json: {},
    };
    setMessages((items) => [...items, optimistic]);
    try {
      const response = await sendMessage(sessionId, content);
      setMessages((items) => [...items, response.assistant_message]);
      setRuns(response.command_runs.length ? response.command_runs : await listCommandRuns(projectId!, sessionId));
    } catch (err) {
      setMessages((items) => [
        ...items,
        {
          id: Date.now() + 1,
          session_id: sessionId,
          project_id: projectId!,
          role: "assistant",
          content: err instanceof Error ? err.message : "请求失败",
          message_type: "text",
          metadata_json: { intent: "error" },
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  return (
    <main
      className={`workspace ${leftCollapsed ? "left-collapsed" : ""} ${rightCollapsed ? "right-collapsed" : ""}`}
      onClick={() => setOpenMenu(null)}
    >
      {leftCollapsed && (
        <button
          className="collapsed-open left-open"
          onClick={(event) => {
            event.stopPropagation();
            setLeftCollapsed(false);
          }}
          title="打开边栏"
        >
          <PanelLeftOpen size={18} />
        </button>
      )}
      {rightCollapsed && (
        <button
          className="collapsed-open right-open"
          onClick={(event) => {
            event.stopPropagation();
            setRightCollapsed(false);
          }}
          title="打开边栏"
        >
          <PanelRightOpen size={18} />
        </button>
      )}
      <aside className="glass-panel left-pane">
        <button
          className="pane-toggle"
          onClick={(event) => {
            event.stopPropagation();
            setLeftCollapsed(true);
          }}
          title="关闭边栏"
        >
          <PanelLeftClose size={17} />
        </button>
        <div className="workspace-brand">
          <div className="brand-chip"><span>&gt;_</span></div>
          <strong>Ops Agent Chat</strong>
        </div>

        <section className={`left-block project-block ${projects.length > 3 ? "compact" : ""}`}>
          <div className="block-title">
            <span>项目</span>
            <small>{projects.length}</small>
          </div>
          <div className="project-scroll">
            {projects.map((project) => {
              const active = project.id === projectId;
              return (
                <div key={project.id} className={active ? "nav-row selected" : "nav-row"}>
                  <button className="project-item" onClick={() => setProjectId(project.id)}>
                    {active ? <FolderOpen size={19} /> : <Folder size={19} />}
                    <span>{project.name}</span>
                    {project.is_pinned ? <Pin size={14} className="pinned-mark" /> : <i aria-label={active ? "当前项目" : "项目可用"} />}
                  </button>
                  <button
                    className="row-menu-trigger"
                    onClick={(event) => {
                      event.stopPropagation();
                      setOpenMenu((value) => value?.type === "project" && value.id === project.id ? null : { type: "project", id: project.id });
                    }}
                    title="项目操作"
                  >
                    <MoreHorizontal size={17} />
                  </button>
                  {openMenu?.type === "project" && openMenu.id === project.id && (
                    <ActionMenu
                      pinned={project.is_pinned}
                      onRename={() => renameProject(project)}
                      onTogglePin={() => toggleProjectPin(project)}
                      onDelete={() => removeProject(project)}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </section>

        <section className="left-block session-block">
          <div className="block-title">
            <span>聊天记录</span>
            <button className="icon-action" onClick={newSession} title="新建会话"><Plus size={16} /></button>
          </div>
          <div className="session-scroll">
            {sessions.map((session) => (
              <div key={session.id} className={session.id === sessionId ? "nav-row selected" : "nav-row"}>
                <button className="session-item" onClick={() => setSessionId(session.id)}>
                  <MessageSquare size={16} />
                  <span>{session.title}</span>
                  {session.is_pinned && <Pin size={14} className="pinned-mark" />}
                </button>
                <button
                  className="row-menu-trigger"
                  onClick={(event) => {
                    event.stopPropagation();
                    setOpenMenu((value) => value?.type === "session" && value.id === session.id ? null : { type: "session", id: session.id });
                  }}
                  title="聊天操作"
                >
                  <MoreHorizontal size={17} />
                </button>
                {openMenu?.type === "session" && openMenu.id === session.id && (
                  <ActionMenu
                    pinned={session.is_pinned}
                    onRename={() => renameSession(session)}
                    onTogglePin={() => toggleSessionPin(session)}
                    onDelete={() => removeSession(session)}
                  />
                )}
              </div>
            ))}
          </div>
        </section>

        <button className="logout" onClick={onLogout}><UserCircle size={25} /> <span>{user.username}</span><LogOut size={17} /></button>
      </aside>

      <section className="glass-panel chat-pane">
        <header className="chat-header">
          <div>
            <strong>{currentProject?.name ?? "Project"}</strong>
            <span>{currentSession?.title ?? "新会话"}</span>
          </div>
          <div className="chat-header-actions">
            <button
              className={messageNavOpen ? "outline-toggle active" : "outline-toggle"}
              onClick={(event) => {
                event.stopPropagation();
                setMessageNavOpen((value) => !value);
              }}
              title={messageNavOpen ? "关闭消息导航" : "打开消息导航"}
            >
              <MessageSquare size={17} />
            </button>
            <span className="v1-badge">只读诊断</span>
          </div>
        </header>

        <MessageNavigatorFloating open={messageNavOpen} messages={messages} onJump={jumpToMessage} />

        <div className="message-list">
          {messages.length === 0 && !sending && (
            <div className="empty-chat">
              <div className="empty-mark">&gt;_</div>
              <h2>Ready when you are.</h2>
              <p>选择一个项目，直接描述你要检查的运行状态。</p>
            </div>
          )}
          {messages.map((message) => (
            <MessageCard
              key={message.id}
              message={message}
              runs={message.role === "assistant" ? runsForMessage(message, runs) : []}
              refCallback={(element) => {
                messageRefs.current[message.id] = element;
              }}
            />
          ))}
          {sending && <ThinkingCard />}
          <div ref={messagesEndRef} />
        </div>

        <form className="composer" onSubmit={submit}>
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="输入你的问题，例如：告诉我 Redis 的状态" />
          <button disabled={sending}><Send size={18} /> 发送</button>
        </form>
      </section>

      <aside className="glass-panel right-pane">
        <div className="right-pane-head">
          <button
            className="pane-toggle"
            onClick={(event) => {
              event.stopPropagation();
              setRightCollapsed(true);
            }}
            title="关闭边栏"
          >
            <PanelRightClose size={17} />
          </button>
          <div className="tabs">
            <button className={rightTab === "commands" ? "active" : ""} onClick={() => setRightTab("commands")}><TerminalSquare size={16} />命令</button>
            <button className={rightTab === "runbook" ? "active" : ""} onClick={() => setRightTab("runbook")}><BookOpenText size={16} />经验库</button>
            <button className={rightTab === "config" ? "active" : ""} onClick={() => setRightTab("config")}><Settings size={16} />配置</button>
          </div>
        </div>
        {rightTab === "commands" && <CommandHistory runs={runs} />}
        {rightTab === "runbook" && <Runbook docs={docs} projectId={projectId} onUploaded={(doc) => setDocs((items) => [...items, doc])} />}
        {rightTab === "config" && currentProject && <ProjectConfig project={currentProject} />}
      </aside>
    </main>
  );
}

function MessageCard({
  message,
  runs,
  refCallback,
}: {
  message: ChatMessage;
  runs: CommandRun[];
  refCallback: (element: HTMLElement | null) => void;
}) {
  if (message.role === "user") {
    return (
      <article className="message user" ref={refCallback}>
        <div className="user-bubble">{message.content}</div>
        <div className="avatar user-avatar"><UserCircle size={24} /></div>
      </article>
    );
  }

  const intent = String(message.metadata_json?.intent ?? "text");
  const sources = sourceFiles(message);
  const sections = splitAnswer(message.content);

  return (
    <article className="message assistant" ref={refCallback}>
      <div className="avatar bot-avatar"><Code2 size={18} /></div>
      <div className={`assistant-card ${intent}`}>
        <div className="answer-header">
          <span>{intentLabel(intent)}</span>
          <small>Ops Agent</small>
        </div>
        <AnswerSections sections={sections} />
        {runs.length > 0 && <CommandResultPanel runs={runs} />}
        {sources.length > 0 && <SourceStrip sources={sources} />}
      </div>
    </article>
  );
}

function ThinkingCard() {
  return (
    <article className="message assistant">
      <div className="avatar bot-avatar"><Code2 size={18} /></div>
      <div className="assistant-card thinking">
        <div className="answer-header"><span>处理中</span><small>Ops Agent</small></div>
        <div className="typing-line"><span /> <span /> <span /></div>
      </div>
    </article>
  );
}

function ActionMenu({
  pinned,
  onRename,
  onTogglePin,
  onDelete,
}: {
  pinned: boolean;
  onRename: () => void;
  onTogglePin: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="action-menu" onClick={(event) => event.stopPropagation()}>
      <button onClick={onRename}><Edit3 size={16} />重命名</button>
      <button onClick={onTogglePin}>{pinned ? <PinOff size={16} /> : <Pin size={16} />}{pinned ? "取消置顶" : "置顶"}</button>
      <button className="danger" onClick={onDelete}><Trash2 size={16} />删除</button>
    </div>
  );
}

function MessageNavigatorFloating({ open, messages, onJump }: { open: boolean; messages: ChatMessage[]; onJump: (messageId: number) => void }) {
  const navigable = messages.filter((message) => message.role === "user" || message.role === "assistant");
  return (
    <aside className={open ? "message-outline open" : "message-outline"}>
      <h3>消息导航</h3>
      {navigable.length === 0 && <p className="empty-note">当前会话还没有消息。</p>}
      {navigable.map((message) => (
        <button key={message.id} className={`message-nav-row ${message.role}`} onClick={() => onJump(message.id)}>
          <span>{message.role === "user" ? "你" : "Agent"}</span>
          <strong>{messagePreview(message.content)}</strong>
        </button>
      ))}
    </aside>
  );
}

function AnswerSections({ sections }: { sections: { title: string; body: string[] }[] }) {
  return (
    <div className="answer-sections">
      {sections.map((section) => (
        <section key={section.title} className="answer-section">
          <h4>{sectionIcon(section.title)}{section.title}</h4>
          {section.body.map((line, index) => renderLine(line, index))}
        </section>
      ))}
    </div>
  );
}

function sectionIcon(title: string) {
  if (title === "诊断结论") return <CircleCheck size={17} strokeWidth={2.15} />;
  if (["证据", "项目依据"].includes(title)) return <FileSearch size={17} strokeWidth={2.15} />;
  if (["下一步建议", "补充建议", "适用场景", "可替代建议"].includes(title)) return <Lightbulb size={17} strokeWidth={2.15} />;
  if (title === "执行命令") return <TerminalSquare size={17} strokeWidth={2.15} />;
  if (title === "风险提示") return <ShieldAlert size={17} strokeWidth={2.15} />;
  if (["概念", "回答"].includes(title)) return <BookOpenText size={17} strokeWidth={2.15} />;
  return <Target size={17} strokeWidth={2.15} />;
}

function renderLine(line: string, index: number) {
  const trimmed = line.trim();
  if (!trimmed) return null;
  if (trimmed.startsWith("- ")) return <p key={index} className="answer-bullet">{formatInline(trimmed.slice(2))}</p>;
  return <p key={index}>{formatInline(trimmed)}</p>;
}

function formatInline(text: string) {
  const parts = text.split(/(`[^`]+`)/g);
  return parts.map((part, index) => {
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    return <span key={index}>{part}</span>;
  });
}

function splitAnswer(content: string): { title: string; body: string[] }[] {
  const cleaned = content.replace(/\r/g, "").trim();
  const lines = cleaned.split("\n").map((line) => line.trim()).filter(Boolean);
  const sections: { title: string; body: string[] }[] = [];
  let current: { title: string; body: string[] } | null = null;

  for (const line of lines) {
    const title = canonicalSectionTitle(line);
    if (title) {
      current = { title, body: [] };
      sections.push(current);
    } else {
      if (!current) {
        current = { title: inferTitle(cleaned), body: [] };
        sections.push(current);
      }
      current.body.push(line);
    }
  }
  return sections.length ? sections : [{ title: "回答", body: [content] }];
}

function canonicalSectionTitle(line: string) {
  const normalized = normalizeSectionTitle(line);
  const titleMap: Record<string, string> = {
    "诊断结论": "诊断结论",
    "结论": "诊断结论",
    "结果": "诊断结论",
    "诊断结果": "诊断结论",
    "证据": "证据",
    "依据": "证据",
    "项目依据": "项目依据",
    "项目证据": "项目依据",
    "回答": "回答",
    "概念": "概念",
    "要点": "要点",
    "例子": "例子",
    "示例": "例子",
    "适用场景": "适用场景",
    "补充建议": "补充建议",
    "不确定项": "不确定项",
    "执行命令": "执行命令",
    "命令": "执行命令",
    "下一步建议": "下一步建议",
    "后续建议": "下一步建议",
    "建议": "下一步建议",
    "下一步": "下一步建议",
    "后续处理": "下一步建议",
    "处理建议": "下一步建议",
    "引用来源": "引用来源",
    "来源": "引用来源",
    "风险提示": "风险提示",
    "可替代建议": "可替代建议",
    "替代建议": "可替代建议",
  };
  return titleMap[normalized] ?? null;
}

function normalizeSectionTitle(line: string) {
  let value = line.trim();
  for (let i = 0; i < 3; i += 1) {
    value = value
      .replace(/^#{1,6}\s*/, "")
      .replace(/[:：]\s*$/, "")
      .replace(/^\*\*(.*)\*\*$/, "$1")
      .replace(/^__([^_].*[^_])__$/, "$1")
      .replace(/^["'“”‘’`]+/, "")
      .replace(/["'“”‘’`]+$/, "")
      .trim();
  }
  return value;
}

function inferTitle(content: string) {
  if (content.includes("V1 只支持")) return "风险提示";
  if (content.includes("命令") || content.includes("exit_code")) return "诊断结论";
  if (content.includes("项目依据") || content.includes("项目证据") || content.includes("来源")) return "项目依据";
  return "回答";
}

function intentLabel(intent: string) {
  if (intent === "diagnosis") return "诊断结果";
  if (intent === "project_knowledge" || intent === "knowledge") return "项目回答";
  if (intent === "general_chat") return "通用聊天";
  if (intent === "general_tech") return "通用技术";
  if (intent === "operation") return "风险提示";
  if (intent === "mixed") return "综合分析";
  return "回答";
}

function messagePreview(content: string) {
  const normalized = content.replace(/\s+/g, " ").trim();
  return normalized.length > 42 ? `${normalized.slice(0, 42)}...` : normalized || "空消息";
}

function sourceFiles(message: ChatMessage) {
  const metadataSources = Array.isArray(message.metadata_json?.experience_sources)
    ? message.metadata_json.experience_sources
    : Array.isArray(message.metadata_json?.rag_sources)
      ? message.metadata_json.rag_sources
      : [];
  const names = metadataSources
    .map((item) => (typeof item === "object" && item && "file_name" in item ? String((item as { file_name?: unknown }).file_name) : ""))
    .filter(Boolean);
  if (names.length) return Array.from(new Set(names));
  const sourceNames = metadataSources
    .map((item) => (typeof item === "object" && item && "source" in item ? String((item as { source?: unknown }).source) : ""))
    .filter(Boolean);
  if (sourceNames.length) return Array.from(new Set(sourceNames));
  return [];
}

function runsForMessage(message: ChatMessage, runs: CommandRun[]) {
  const ids = Array.isArray(message.metadata_json?.command_run_ids) ? message.metadata_json.command_run_ids : [];
  const idSet = new Set(ids.map((id) => Number(id)).filter(Number.isFinite));
  if (!idSet.size) return [];
  return runs.filter((run) => idSet.has(run.id));
}

function SourceStrip({ sources }: { sources: string[] }) {
  return (
    <div className="source-strip">
      <span>引用来源</span>
      {sources.map((source) => <em key={source}><FileText size={13} /> {source}</em>)}
    </div>
  );
}

function CommandResultPanel({ runs }: { runs: CommandRun[] }) {
  const [open, setOpen] = useState(false);
  const successCount = runs.filter((run) => run.status === "success").length;
  return (
    <section className="command-card">
      <button className="command-summary" onClick={() => setOpen((value) => !value)}>
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <span>执行命令 {runs.length} 条</span>
        <small>{successCount} 成功</small>
      </button>
      {open && (
        <div className="command-list">
          {runs.map((run) => (
            <details key={run.id} className="command-row">
              <summary>
                <span className={`status-dot ${run.status}`} />
                <code>{run.command}</code>
                <small>exit {run.exit_code ?? "-"}</small>
              </summary>
              <div className="command-detail">
                <p>{run.purpose}</p>
                <pre>{run.stderr_excerpt || run.stdout_excerpt || "无输出摘要"}</pre>
              </div>
            </details>
          ))}
        </div>
      )}
    </section>
  );
}

function CommandHistory({ runs }: { runs: CommandRun[] }) {
  return (
    <div className="side-card">
      <h3>命令历史</h3>
      {runs.length === 0 && <p className="empty-note">当前会话还没有命令执行记录。</p>}
      {runs.map((run) => (
        <div key={run.id} className="run-row">
          <div className="run-meta">
            <span className={`status ${run.status}`}>{run.status}</span>
            <time>{formatRunTime(run)}</time>
          </div>
          <code>{run.command}</code>
          <small>{run.purpose}</small>
        </div>
      ))}
    </div>
  );
}

function formatRunTime(run: CommandRun) {
  const raw = run.started_at ?? run.created_at ?? run.finished_at;
  if (!raw) return run.duration_ms ? `${run.duration_ms}ms` : "-";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function sortProjects(items: Project[]) {
  return [...items].sort((a, b) => Number(b.is_pinned) - Number(a.is_pinned) || b.id - a.id);
}

function sortSessions(items: ChatSession[]) {
  return [...items].sort((a, b) => Number(b.is_pinned) - Number(a.is_pinned) || b.id - a.id);
}

function Runbook({ docs, projectId, onUploaded }: { docs: RagDocument[]; projectId: number | null; onUploaded: (doc: RagDocument) => void }) {
  const [uploading, setUploading] = useState(false);
  async function upload(file: File | undefined) {
    if (!file || !projectId || uploading) return;
    setUploading(true);
    try {
      onUploaded(await uploadRagDocument(projectId, file));
    } finally {
      setUploading(false);
    }
  }
  return (
    <div className="side-card">
      <h3>项目经验</h3>
      <label className="upload-line">
        <input type="file" accept=".md,.txt" onChange={(event) => upload(event.target.files?.[0])} />
        <span>{uploading ? "上传中..." : "上传记录"}</span>
      </label>
      {docs.length === 0 && <p className="empty-note">暂无项目 FAQ、历史故障或处理记录。</p>}
      {docs.map((doc) => <div key={doc.id} className="doc-row"><strong>{doc.file_name}</strong><span>{doc.doc_type} · {doc.chunk_count} chunks</span></div>)}
    </div>
  );
}

function ProjectConfig({ project }: { project: Project }) {
  return (
    <div className="side-card config-list">
      <h3>项目配置摘要</h3>
      <p><span>项目名</span>{project.name}</p>
      <p><span>部署目录</span>{project.workdir}</p>
      <p><span>部署方式</span>{project.deploy_type}</p>
      <p><span>Compose</span>{project.compose_file}</p>
      <p><span>健康检查</span>{project.health_url}</p>
      <p><span>容器范围</span>{project.allowed_container_prefixes.join(", ")}</p>
    </div>
  );
}
