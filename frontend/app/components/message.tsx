import type {ReactNode} from 'react';
import {cx} from '../../lib/cx';

type Props = {role: 'user' | 'assistant'; label: ReactNode; children: ReactNode; note?: ReactNode; streaming?: boolean};

export function Message({role, label, children, note, streaming}: Props) {
  const user = role === 'user';
  return <article className={cx('flex animate-fade-in gap-3', user && 'flex-row-reverse')}>
    <span aria-hidden className={cx('mt-0.5 grid size-7 shrink-0 place-items-center rounded-full text-[11px] font-semibold',
      user ? 'bg-ink text-white' : 'bg-brand-soft text-brand')}>{user ? '商' : 'AI'}</span>
    <div className={cx('flex max-w-[85%] min-w-0 flex-col gap-1 sm:max-w-[75%]', user && 'items-end')}>
      <strong className="text-[11px] font-medium text-ink-faint">{label}</strong>
      <div className={cx('rounded-2xl px-3.5 py-2.5 text-sm leading-6 break-words whitespace-pre-wrap',
        user ? 'rounded-tr-md bg-brand text-white' : 'rounded-tl-md border border-line bg-subtle text-ink')}>
        <p>{children}{streaming && <span aria-hidden className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse bg-brand"/>}</p>
      </div>
      {note}
    </div>
  </article>;
}

export function TypingIndicator() {
  return <div className="flex items-center gap-3" role="status">
    <span aria-hidden className="grid size-7 place-items-center rounded-full bg-brand-soft text-[11px] font-semibold text-brand">AI</span>
    <span className="flex items-center gap-1 rounded-2xl rounded-tl-md border border-line bg-subtle px-3.5 py-3">
      {[0, 150, 300].map(delay => <span key={delay} aria-hidden className="size-1.5 animate-bounce rounded-full bg-ink-faint" style={{animationDelay: `${delay}ms`}}/>)}
      <span className="sr-only">AI 正在思考</span>
    </span>
  </div>;
}

export function EmptyConversation({icon, children}: {icon: ReactNode; children: ReactNode}) {
  return <div className="flex h-full min-h-56 flex-col items-center justify-center gap-3 text-center">
    <span aria-hidden className="grid size-10 place-items-center rounded-xl border border-line bg-subtle text-ink-faint">{icon}</span>
    <p className="max-w-xs text-sm text-ink-faint">{children}</p>
  </div>;
}
