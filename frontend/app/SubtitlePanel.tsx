'use client';
import {useEffect,useRef} from 'react';
import type {components} from './api.generated';

export default function SubtitlePanel({view,live,generation}:{view:components['schemas']['SessionView']|null;live:string;generation:string|null}) {
  const region=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(region.current)region.current.scrollTop=region.current.scrollHeight;},[view?.messages,live]);
  return <div ref={region} className="messages" role="log" aria-label="对话字幕" aria-live="polite">
    {!view?.messages?.length&&!live&&<div className="empty"><span className="empty-mark"><img src="/zhiyu-logo.jpg" alt="" width="80" height="80"/></span><h2>准备好开始对话了吗？</h2><p>点击下方按钮开始本次告知。</p></div>}
    {view?.messages?.filter(m=>!(live&&m.status==='generating'&&m.generation_id===generation)).map(m=><article key={m.message_id} className={m.role}><strong>{m.role==='user'?'商户':'AI 助手'}</strong><p>{m.text||'…'}</p>{m.status==='interrupted'&&<small>已打断</small>}{m.status==='failed'&&<small>生成失败</small>}</article>)}
    {live&&!view?.ended&&<article className="assistant"><strong>AI 助手 · 正在回复<span className="dots">•••</span></strong><p>{live}</p></article>}
  </div>;
}

