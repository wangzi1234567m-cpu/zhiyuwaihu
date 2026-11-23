import {Info, Plus} from 'lucide-react';
import type {components} from '../api.generated';
import {Button, Card, CardHeader, Field, Select} from './ui';

type Capabilities = components['schemas']['Capabilities'];
type Application = components['schemas']['ApplicationView'];
type Mode = 'text' | 'voice';

type Props = {
  mode: Mode; onModeChange: (mode: Mode) => void;
  capabilities: Capabilities | null;
  applications: Application[]; selected: string; onSelectedChange: (id: string) => void;
  createDisabled: boolean; creating: boolean; onCreate: () => void;
};

export default function TaskPanel(props: Props) {
  const {mode, onModeChange, capabilities, applications, selected, onSelectedChange, createDisabled, creating, onCreate} = props;
  return <Card>
    <CardHeader title="合成任务" description="选择交互模式与申请单后创建会话"/>
    <div className="space-y-4 p-5">
      <Field label="交互模式">
        <Select value={mode} onChange={e => onModeChange(e.target.value as Mode)}>
          <option value="text">文本联调</option>
          <option value="voice" disabled={!capabilities?.voice}>实时语音</option>
        </Select>
      </Field>
      {capabilities?.voice_unavailable_reason && <p className="-mt-2 text-xs leading-5 text-ink-faint">{capabilities.voice_unavailable_reason}</p>}
      <Field label="选择申请单">
        <Select value={selected} onChange={e => onSelectedChange(e.target.value)}>
          {applications.map(a => <option key={a.application_id} value={a.application_id}>{a.label} · {a.company_hint}</option>)}
        </Select>
      </Field>
      <Button className="w-full" disabled={createDisabled} loading={creating} onClick={onCreate}>
        {!creating && <Plus aria-hidden className="size-4"/>}创建会话
      </Button>
      <div className="flex gap-2.5 rounded-lg bg-subtle px-3 py-2.5 text-xs leading-5 text-ink-muted">
        <Info aria-hidden className="mt-0.5 size-3.5 shrink-0 text-ink-faint"/>
        <div className="space-y-1">
          <p>身份输入：我是演示商家1（按所选商家编号调整）</p>
          <p>之后可问“补齐材料一定必过吗”，再说“明白了”。</p>
        </div>
      </div>
    </div>
  </Card>;
}
