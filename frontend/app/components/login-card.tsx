import {LockKeyhole} from 'lucide-react';
import {Button, Card, Field, inputClass} from './ui';

type Props = {password: string; onPasswordChange: (value: string) => void; submitting: boolean; onSubmit: () => void};

export default function LoginCard({password, onPasswordChange, submitting, onSubmit}: Props) {
  return <Card className="mx-auto mt-4 w-full max-w-sm animate-fade-in sm:mt-10">
    <div className="px-6 pt-6">
      <span aria-hidden className="grid size-9 place-items-center rounded-lg bg-brand-soft text-brand">
        <LockKeyhole className="size-4"/>
      </span>
      <h2 className="mt-4 text-base font-semibold text-ink">进入演示环境</h2>
      <p className="mt-1 text-sm leading-6 text-ink-muted">使用本地配置的演示密码。默认值见启动说明。</p>
    </div>
    <form className="space-y-4 p-6" onSubmit={e => {e.preventDefault(); onSubmit();}}>
      <Field label="演示密码">
        <input aria-label="演示密码" type="password" autoComplete="current-password" className={inputClass}
          value={password} onChange={e => onPasswordChange(e.target.value)}/>
      </Field>
      <Button className="w-full" disabled={submitting} loading={submitting}>登录</Button>
    </form>
  </Card>;
}
