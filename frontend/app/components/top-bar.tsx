import {AudioLines} from 'lucide-react';
import type {components} from '../api.generated';
import {Badge} from './ui';

type Capabilities = components['schemas']['Capabilities'];

export default function TopBar({capabilities}: {capabilities: Capabilities | null}) {
  const voice = !!capabilities?.voice;
  return <header className="sticky top-0 z-20 border-b border-line bg-surface/80 backdrop-blur-md">
    <div className="mx-auto flex h-14 max-w-7xl items-center justify-between gap-4 px-4 sm:px-6">
      <div className="flex min-w-0 items-center gap-3">
        <span aria-hidden className="grid size-7 shrink-0 place-items-center rounded-lg bg-ink text-white">
          <AudioLines className="size-4"/>
        </span>
        <nav aria-label="位置" className="flex min-w-0 items-center gap-2 text-sm">
          <span className="font-semibold tracking-tight text-ink">智语外呼</span>
          <span aria-hidden className="text-line-strong">/</span>
          <span className="truncate text-ink-muted">开发联调</span>
        </nav>
      </div>
      <div className="flex items-center gap-2">
        {capabilities && <Badge className="hidden sm:inline-flex">
          模型 · <span className="font-mono">{capabilities.model}</span>
        </Badge>}
        <Badge tone={voice ? 'success' : 'neutral'} dot>{voice ? '语音可联调' : '语音未配置'}</Badge>
      </div>
    </div>
  </header>;
}
