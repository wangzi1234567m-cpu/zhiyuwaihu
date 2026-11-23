import {useEffect, useRef} from 'react';
import {MessagesSquare, Play, Send, Square, Hand} from 'lucide-react';
import type {View} from '../../lib/session';
import {Badge, Button, Card, inputClass} from './ui';
import {EmptyConversation, Message, TypingIndicator} from './message';
import {cx} from '../../lib/cx';

type Props = {
  view: View | null; sid: string; connecting: boolean; submitting: boolean; pending: string;
  live: string; generationId: string | null;
  input: string; onInputChange: (value: string) => void;
  onStart: () => void; onInterrupt: () => void; onEnd: () => void; onSend: () => void;
};

export default function TextConversation(props: Props) {
  const {view, sid, connecting, submitting, pending, live, generationId, input, onInputChange, onStart, onInterrupt, onEnd, onSend} = props;
  const scroller = useRef<HTMLDivElement>(null);
  const messages = view?.messages?.filter(m => !(live && m.status === 'generating' && m.generation_id === generationId)) ?? [];
  const composerDisabled = !view || view.ended || view.state === 'CASE_READY';
  const thinking = !!view?.busy && !live && !messages.some(m => m.status === 'generating');

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTo({top: el.scrollHeight, behavior: 'smooth'});
  }, [messages.length, live, thinking]);

  const connection = sid ? (connecting ? '事件连接中' : '已连接') : '待连接';
  return <Card className="flex min-h-[560px] flex-col overflow-hidden">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
      <div className="flex items-center gap-2.5">
        <h2 className="text-sm font-semibold text-ink">文本会话</h2>
        <Badge tone={!sid ? 'neutral' : connecting ? 'warning' : 'success'} dot pulse={!!sid && connecting}>{connection}</Badge>
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={!view||view.state!=='CASE_READY'||submitting} loading={pending==='start'} onClick={onStart}>
          {pending!=='start' && <Play aria-hidden className="size-3.5"/>}开始
        </Button>
        <Button size="sm" variant="secondary" disabled={!view?.busy||!view.generation_id||submitting} onClick={onInterrupt}>
          <Hand aria-hidden className="size-3.5"/>打断
        </Button>
        <Button size="sm" variant="danger" disabled={!view||view.ended||submitting} onClick={onEnd}>
          <Square aria-hidden className="size-3"/>结束
        </Button>
      </div>
    </div>

    <div ref={scroller} className="h-[440px] flex-1 space-y-5 overflow-y-auto px-4 py-6 sm:px-6" aria-live="polite">
      {!view?.messages?.length && !live && !thinking
        ? <EmptyConversation icon={<MessagesSquare className="size-5"/>}>创建会话后点击开始，AI 会主动开场。</EmptyConversation>
        : <>
          {messages.map(m => <Message key={m.message_id} role={m.role === 'user' ? 'user' : 'assistant'} label={m.role === 'user' ? '商户' : 'AI'}
            note={m.status === 'interrupted' ? <small className="text-[11px] text-ink-faint">已打断</small>
              : m.status === 'failed' ? <small className="text-[11px] text-rose-700">生成失败，可重试</small> : undefined}>
            {m.text || '…'}
          </Message>)}
          {live && <Message role="assistant" label="AI · 正在回复" streaming>{live}</Message>}
          {thinking && <TypingIndicator/>}
        </>}
    </div>

    <form className="border-t border-line bg-surface p-3" onSubmit={e => {e.preventDefault(); onSend();}}>
      <div className="flex items-center gap-2">
        <input aria-label="商户回复" placeholder="输入商户回复；新输入会打断旧生成" className={cx(inputClass, 'h-10')}
          value={input} onChange={e => onInputChange(e.target.value)} disabled={composerDisabled}
          onKeyDown={e => {if (e.key === 'Enter' && (e.nativeEvent.isComposing || e.keyCode === 229)) e.preventDefault();}}/>
        <Button className="h-10" disabled={composerDisabled||submitting||!input.trim()} loading={pending==='send'}>
          {pending!=='send' && <Send aria-hidden className="size-3.5"/>}发送
        </Button>
      </div>
    </form>
  </Card>;
}
