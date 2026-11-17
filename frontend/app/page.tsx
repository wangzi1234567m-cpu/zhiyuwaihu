'use client';
import {useCallback, useEffect, useRef, useState} from 'react';
import {api, BackendError, type ApplicationView, type Capabilities, type SessionEvent, type SessionView} from '../lib/api';
import {
  contactLabel,
  explanationLabel,
  modelLabel,
  needsFailureBanner,
  operationLabel,
  operationPhase,
  phaseLabel,
  recordTitle,
  routeLabel,
  stateLabel,
  statePhase,
  streamLabel,
  voiceLabel,
  type StreamState,
} from '../lib/status';

const SESSION_KEY = 'zhiyu.session';

export default function Home() {
  const [logged, setLogged] = useState(false);
  const [password, setPassword] = useState('');
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [applications, setApplications] = useState<ApplicationView[]>([]);
  const [selected, setSelected] = useState('');
  const [view, setView] = useState<SessionView | null>(null);
  const [sid, setSid] = useState('');
  const [input, setInput] = useState('');
  const [error, setError] = useState('');
  // 首次读取后端事实完成前不渲染「登录表单」，避免把「还没问到」误显示成「未登录」。
  const [booted, setBooted] = useState(false);
  const [stream, setStream] = useState<StreamState>('idle');
  const [submitting, setSubmitting] = useState(false);
  const [live, setLive] = useState('');
  const generation = useRef<string | null>(null);
  const cursor = useRef(0);
  const activeSid = useRef('');

  const describeError = useCallback((e: unknown) => {
    if (e instanceof BackendError) {
      // 只展示后端给的错误码与消息，不猜测成功。
      return `${e.message}${e.code ? `（${e.code}）` : ''}`;
    }
    return e instanceof Error ? e.message : String(e);
  }, []);

  const initialize = useCallback(async (options: {restoreSession?: boolean} = {}) => {
    // 应用列表与能力都来自真实后端；任一失败即显式错误态。
    let items: ApplicationView[];
    try {
      items = await api.applications();
    } catch (e) {
      // 未登录（401）是预期状态：只展示登录表单，不报错。
      if (e instanceof BackendError && e.code === 'UNAUTHENTICATED') {
        setLogged(false);
        setBooted(true);
        return;
      }
      setBooted(true);
      throw e;
    }
    const caps = await api.capabilities().catch(() => null);
    setApplications(items);
    setSelected((current) => current || items[0]?.application_id || '');
    setCapabilities(caps);
    setLogged(true);
    setBooted(true);
    if (options.restoreSession) {
      const saved = window.localStorage.getItem(SESSION_KEY);
      if (saved) setSid(saved);
    }
  }, []);

  useEffect(() => {
    initialize({restoreSession: true}).catch((e) => setError(describeError(e)));
  }, [initialize, describeError]);

  useEffect(() => {
    if (!sid || !logged) return;
    let closed = false;
    let source: EventSource | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    activeSid.current = sid;
    setLive('');
    setView(null);
    setStream('connecting');

    const refresh = async () => {
      const next = await api.session(sid);
      if (closed || activeSid.current !== sid) return;
      if (next.last_event_seq < cursor.current) return;
      setView(next);
      generation.current = next.generation_id ?? null;
      cursor.current = Math.max(cursor.current, next.last_event_seq);
      setLive('');
    };

    const connect = async () => {
      try {
        const next = await api.session(sid);
        if (closed) return;
        setView(next);
        generation.current = next.generation_id ?? null;
        cursor.current = next.last_event_seq;
        source = new EventSource(api.eventStreamUrl(sid, cursor.current));
        source.onopen = () => { attempts = 0; setStream('open'); };
        const receive = (raw: MessageEvent<string>) => {
          if (closed) return;
          let event: SessionEvent;
          try {
            event = JSON.parse(raw.data) as SessionEvent;
          } catch {
            return; // 坏帧不猜语义，等下一次快照。
          }
          if (event.seq <= cursor.current) return;
          cursor.current = event.seq;
          const payload = event.payload;
          if (payload.type === 'turn.started') {
            generation.current = event.generation_id ?? null;
            setLive('');
          } else if (payload.type === 'text.delta') {
            if (event.generation_id === generation.current) setLive((x) => x + payload.text);
          } else if (payload.type === 'turn.interrupted') {
            if (payload.invalidated_generation_id === generation.current) setLive('');
          } else if (payload.type === 'operation.updated') {
            setError('');
          }
          // 业务事实（状态、操作结果、回写记录）一律以快照为准，不从文本推断。
          if (['generation.finished', 'operation.updated', 'session.ended', 'state.changed'].includes(payload.type)) {
            refresh().catch((e) => setError(describeError(e)));
          }
        };
        ['turn.started', 'text.delta', 'state.changed', 'generation.finished', 'turn.interrupted', 'operation.updated', 'session.ended', 'knowledge.used']
          .forEach((t) => source!.addEventListener(t, receive as EventListener));
        source.onerror = () => {
          source?.close();
          setStream('connecting');
          if (closed) return;
          attempts += 1;
          if (attempts > 8) {
            // 有界重连：放弃后显示明确错误态，界面不假装“已连接”。
            setStream('failed');
            setError('事件流连接失败：后端或数据库可能已停止，业务状态无法继续同步。请按 RUNBOOK 检查后重试。');
            return;
          }
          retry = setTimeout(connect, Math.min(1000 * attempts, 5000));
        };
      } catch (e) {
        if (!closed) {
          // 本地保存的会话在后端不存在（数据库重置/换了 owner）：清掉本地指针，不反复重连。
          if (e instanceof BackendError && (e.code === 'NOT_FOUND' || e.status === 404)) {
            window.localStorage.removeItem(SESSION_KEY);
            setSid('');
            setStream('idle');
            setError('本地保存的会话在后端已不存在（可能数据库已重建），请重新创建会话。');
            return;
          }
          setError(describeError(e));
          setStream('failed');
        }
      }
    };
    connect();
    return () => { closed = true; source?.close(); if (retry) clearTimeout(retry); };
  }, [sid, logged, describeError]);

  async function run(fn: () => Promise<void>) {
    // 重试期间保留上一次失败信息（不清空）：界面不能因为「正在重试」就假装已经好了。
    setSubmitting(true);
    try {
      await fn();
      setError('');
    } catch (e) {
      setError(describeError(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function create() {
    const next = await api.createSession(selected, crypto.randomUUID());
    window.localStorage.setItem(SESSION_KEY, next.session_id);
    setSid(next.session_id);
  }

  async function start() {
    if (!view) return;
    const turn = await api.start(sid, view.state_revision, crypto.randomUUID());
    generation.current = turn.generation_id;
    setView({...view, busy: true, generation_id: turn.generation_id});
  }

  async function send() {
    if (!input.trim() || !view) return;
    const turn = await api.turn(sid, input, crypto.randomUUID());
    generation.current = turn.generation_id;
    setInput('');
    setLive('');
    setView(await api.session(sid));
  }

  async function interrupt() {
    if (!view?.generation_id) return;
    await api.interrupt(sid, view.generation_id, crypto.randomUUID());
    setView(await api.session(sid));
    setLive('');
  }

  async function end() {
    setView(await api.end(sid));
    setLive('');
  }

  const phase = view ? statePhase(view.state) : null;
  const operationStatuses = view?.operations.map((o) => o.status) ?? [];
  const showFailure = view ? needsFailureBanner(view.state, operationStatuses) : false;
  // 未核验不披露业务详情：reason/remedy 只在后端判定 verified 后展示。
  const verified = view?.verified === true;

  return (
    <main>
      <header>
        <div>
          <p className="eyebrow">智语外呼 / 开发联调</p>
          <h1>商户资质告知助手</h1>
          <p>合成数据 · 文本模式 · 不执行真实外呼</p>
        </div>
        <span className="badge" data-testid="voice-badge">{voiceLabel(capabilities ? capabilities.voice : null)}</span>
      </header>

      {error && (
        <div role="alert" className="error" data-testid="error-banner">
          <strong>请求失败</strong>
          <p>{error}</p>
          <p className="hint">请确认后端与独立 PostgreSQL 已启动（见 docs/agent/RUNBOOK.md）；失败不会被显示为成功。</p>
          <button onClick={() => run(() => initialize({restoreSession: true}))}>重试连接</button>
        </div>
      )}

      {!logged ? (
        booted ? (
        <section className="login">
          <h2>进入演示环境</h2>
          <p>使用本地配置的演示密码。默认值见启动说明。</p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              run(async () => {
                await api.login('demo', password);
                setPassword('');
                await initialize({restoreSession: true});
              });
            }}
          >
            <label>
              演示密码
              <input aria-label="演示密码" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <button disabled={submitting}>登录</button>
          </form>
        </section>
        ) : (
          // 还没拿到后端结论时显示「读取中」，既不冒充已登录，也不谎称未登录。
          <section className="login" data-testid="boot-loading" aria-busy="true">
            <h2>正在读取后端状态</h2>
            <p>正在向后端查询会话与能力信息，结果未返回前不展示任何业务结论。</p>
          </section>
        )
      ) : (
        <div className="grid">
          <aside>
            <h2>合成任务</h2>
            <label>
              选择申请单
              <select value={selected} onChange={(e) => setSelected(e.target.value)}>
                {applications.length === 0 && <option value="">后端没有返回可用的合成申请单</option>}
                {applications.map((a) => (
                  <option key={a.application_id} value={a.application_id}>{a.label} · {a.company_hint}</option>
                ))}
              </select>
            </label>
            <button disabled={submitting || !selected || !!view?.busy} onClick={() => run(create)}>创建会话</button>
            <p className="hint">身份输入：我是演示商家1（按所选商家编号调整）</p>
            <p className="hint">之后可问“补齐材料一定必过吗”，再说“明白了”。</p>
            <hr />
            <h2>业务事实（后端权威）</h2>
            {!view ? (
              <p className="hint">尚未创建会话，没有可以展示的业务结论。</p>
            ) : (
              <>
                <p data-testid="state-line">状态：{stateLabel(view.state)}</p>
                <p data-testid="phase-line">阶段：{phaseLabel(phase!)}</p>
                <p data-testid="route-line">路由：{routeLabel(view.route)}</p>
                <p data-testid="identity-line">身份：{verified ? '已核对' : '尚未核对'}</p>
                {verified && view.reason && <p>原因：{view.reason}</p>}
                {verified && view.remedy && <p>整改：{view.remedy}</p>}
                {!verified && <p className="hint">未完成服务端身份核验，不展示驳回原因与整改细节。</p>}
                {view.operations.length === 0 && <p className="hint">暂无业务操作。</p>}
                {view.operations.map((o) => (
                  <p key={o.operation_id} data-testid="operation-status">
                    {operationLabel(o.status)} · 阶段 {phaseLabel(operationPhase(o.status))}
                    {o.resource_id && <small>资源：{o.resource_id}</small>}
                    {o.error && <small>错误：{o.error.message}（{o.error.code}）</small>}
                  </p>
                ))}
                {view.record && (
                  <div data-testid="writeback">
                    <p>{recordTitle(view.operations.map((o) => o.status))}</p>
                    <small>接触结果：{contactLabel(view.record.contact_status)}</small>
                    <small>告知结果：{explanationLabel(view.record.explanation_status)}</small>
                    <small>记录版本：{view.record.record_revision} · 规则版本：{view.record.rule_version}</small>
                    {view.record.reason_codes.length > 0 && <small>原因码：{view.record.reason_codes.join('、')}</small>}
                  </div>
                )}
              </>
            )}
            <hr />
            <h2>运行能力</h2>
            <p data-testid="model-line">模型：{modelLabel(capabilities?.model ?? '')}</p>
            <p className="hint">模型标识由后端 /capabilities 上报；页面不展示任何密钥或启动令牌。</p>
          </aside>

          <section className="conversation">
            <div className="toolbar">
              <h2>文本会话</h2>
              <span data-testid="stream-status">{sid ? streamLabel(stream) : '待连接'}</span>
            </div>
            {showFailure && (
              <div className="warn" data-testid="failure-banner">
                后端结果为失败或需人工处理：请按业务事实处理，不视为成功。
              </div>
            )}
            <div className="actions">
              <button disabled={!view || view.state !== 'CASE_READY' || submitting} onClick={() => run(start)}>开始</button>
              <button disabled={!view?.busy || !view.generation_id || submitting} onClick={() => run(interrupt)}>打断</button>
              <button disabled={!view || view.ended || submitting} onClick={() => run(end)}>结束</button>
            </div>
            <div className="messages" aria-live="polite">
              {!view?.messages?.length && <p className="empty">创建会话后点击开始，AI 会主动开场。</p>}
              {view?.messages?.map((m) => (
                <article key={m.message_id} className={m.role}>
                  <strong>{m.role === 'user' ? '商户' : 'AI'}</strong>
                  <p>{m.text || '…'}</p>
                  {m.status === 'generating' && <small>生成中</small>}
                  {m.status === 'interrupted' && <small>已打断</small>}
                  {m.status === 'failed' && <small>生成失败，可重试</small>}
                </article>
              ))}
              {live && (
                <article className="assistant">
                  <strong>AI · 正在回复（未完成，不代表业务结果）</strong>
                  <p>{live}</p>
                </article>
              )}
            </div>
            <form
              className="composer"
              onSubmit={(e) => {
                e.preventDefault();
                run(send);
              }}
            >
              <input
                aria-label="商户回复"
                placeholder="输入商户回复；新输入会打断旧生成"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={!view || view.ended || view.state === 'CASE_READY'}
              />
              <button disabled={!view || view.ended || view.state === 'CASE_READY' || submitting || !input.trim()}>发送</button>
            </form>
          </section>
        </div>
      )}
      <footer>当前为文本 Agent 与模拟业务端口。文字响应不代表语音性能，也不代表审核结论变更。</footer>
    </main>
  );
}
