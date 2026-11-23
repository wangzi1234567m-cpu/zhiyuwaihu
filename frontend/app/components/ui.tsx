import type {ButtonHTMLAttributes, HTMLAttributes, ReactNode, SelectHTMLAttributes} from 'react';
import {ChevronDown, Loader2} from 'lucide-react';
import {cx, type Tone} from '../../lib/cx';

export function Card({className, ...props}: HTMLAttributes<HTMLElement>) {
  return <section className={cx('rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(16,16,20,0.04)]', className)} {...props}/>;
}

export function CardHeader({title, description, action, className}: {title: ReactNode; description?: ReactNode; action?: ReactNode; className?: string}) {
  return <div className={cx('flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4', className)}>
    <div className="min-w-0">
      <h2 className="text-sm font-semibold text-ink">{title}</h2>
      {description && <p className="mt-1 text-xs leading-5 text-ink-faint">{description}</p>}
    </div>
    {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
  </div>;
}

const buttonVariants = {
  primary: 'bg-brand text-white shadow-sm hover:bg-brand-hover',
  secondary: 'border border-line-strong bg-surface text-ink hover:bg-subtle',
  ghost: 'text-ink-muted hover:bg-subtle hover:text-ink',
  danger: 'border border-line-strong bg-surface text-rose-700 hover:border-rose-200 hover:bg-rose-50',
};

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof buttonVariants;
  size?: 'sm' | 'md';
  loading?: boolean;
};

export function Button({variant = 'primary', size = 'md', loading, className, children, ...props}: ButtonProps) {
  return <button
    className={cx(
      'inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap transition-colors duration-150',
      'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand',
      'disabled:cursor-not-allowed disabled:opacity-45 disabled:shadow-none',
      size === 'sm' ? 'h-8 px-3 text-[13px]' : 'h-9 px-4 text-sm',
      buttonVariants[variant], className,
    )}
    aria-busy={loading || undefined}
    {...props}
  >
    {loading && <Loader2 aria-hidden className="size-3.5 animate-spin"/>}
    {children}
  </button>;
}

const badgeTones: Record<Tone, string> = {
  neutral: 'bg-subtle text-ink-muted ring-line-strong/70',
  brand: 'bg-brand-soft text-brand ring-brand/15',
  success: 'bg-emerald-50 text-emerald-700 ring-emerald-600/15',
  warning: 'bg-amber-50 text-amber-800 ring-amber-600/20',
  danger: 'bg-rose-50 text-rose-700 ring-rose-600/15',
};

export const dotTones: Record<Tone, string> = {
  neutral: 'bg-zinc-400',
  brand: 'bg-brand',
  success: 'bg-emerald-500',
  warning: 'bg-amber-500',
  danger: 'bg-rose-500',
};

export function Badge({tone = 'neutral', dot, pulse, className, children}: {tone?: Tone; dot?: boolean; pulse?: boolean; className?: string; children: ReactNode}) {
  return <span className={cx('inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-inset', badgeTones[tone], className)}>
    {dot && <span aria-hidden className={cx('size-1.5 rounded-full', dotTones[tone], pulse && 'animate-pulse')}/>}
    {children}
  </span>;
}

export const inputClass = cx(
  'h-9 w-full rounded-lg border border-line-strong bg-surface px-3 text-sm text-ink shadow-[0_1px_1px_rgba(0,0,0,0.02)]',
  'transition-[border-color,box-shadow] duration-150 placeholder:text-ink-faint hover:border-zinc-400',
  'focus:border-brand focus:ring-3 focus:ring-brand/15 focus:outline-none',
  'disabled:cursor-not-allowed disabled:bg-subtle disabled:text-ink-faint',
);

export function Field({label, children, className}: {label: ReactNode; children: ReactNode; className?: string}) {
  return <label className={cx('block', className)}>
    <span className="mb-1.5 block text-xs font-medium text-ink-muted">{label}</span>
    {children}
  </label>;
}

export function Select({className, ...props}: SelectHTMLAttributes<HTMLSelectElement>) {
  return <span className="relative block">
    <select className={cx(inputClass, 'cursor-pointer appearance-none pr-9', className)} {...props}/>
    <ChevronDown aria-hidden className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-ink-faint"/>
  </span>;
}

export function Disclosure({summary, children}: {summary: ReactNode; children: ReactNode}) {
  return <details className="group rounded-lg border border-line bg-subtle/60">
    <summary className="flex cursor-pointer items-center justify-between gap-2 px-3 py-2 text-xs font-medium text-ink-muted select-none hover:text-ink">
      {summary}
      <ChevronDown aria-hidden className="size-3.5 transition-transform duration-150 group-open:rotate-180"/>
    </summary>
    <div className="border-t border-line px-3 py-2.5">{children}</div>
  </details>;
}

export function Skeleton({className}: {className?: string}) {
  return <div aria-hidden className={cx('animate-pulse rounded-md bg-subtle', className)}/>;
}
