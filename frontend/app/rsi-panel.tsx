'use client';
import {useRef, useState} from 'react';
import {Card, CardHeader, Field} from './components/ui';

type Summary = {label:string; runs:number; passed:number};
function summary(label:string, value:unknown): Summary {
  if (!value || typeof value!=='object' || !('runs' in value) || !('passed' in value))
    throw new Error('报告缺少运行数或通过数');
  const {runs,passed} = value;
  if (typeof runs!=='number' || typeof passed!=='number' || !Number.isSafeInteger(runs) ||
      !Number.isSafeInteger(passed) || runs<0 || passed<0 || passed>runs)
    throw new Error('报告中的计数无效');
  return {label,runs,passed};
}

export default function RsiPanel() {
  const [rows,setRows] = useState<Summary[]>([]);
  const [error,setError] = useState('');
  const request = useRef(0);
  async function read(file?:File) {
    const id = ++request.current;
    setRows([]); setError('');
    if (!file) return;
    try {
      const report = JSON.parse(await file.text());
      const suites = report.dev && report.holdout ? [['开发集', report.dev],['独立验证集',report.holdout]] : [['开发集', report]];
      const results = suites.flatMap(([name,suite])=>[
        summary(name+' · 基线',suite.arm_summary?.baseline),
        summary(name+' · 候选',suite.arm_summary?.candidate),
      ]);
      if (id===request.current) setRows(results);
    } catch(e) {
      if (id===request.current) setError(e instanceof Error ? e.message : '报告读取失败');
    }
  }
  return <Card>
    <CardHeader title="RSI 评测对比" description="选择 rsi_run.py 导出的开发集或独立验证报告，在本机查看各组完成率。"/>
    <div className="space-y-4 p-5">
      <Field label="评测报告（JSON）">
        <input type="file" accept=".json,application/json" onChange={e=>read(e.target.files?.[0])}
          className="block w-full cursor-pointer rounded-lg border border-dashed border-line-strong bg-subtle/60 p-2 text-sm text-ink-muted transition-colors hover:border-zinc-400 file:mr-3 file:cursor-pointer file:rounded-md file:border-0 file:bg-surface file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-ink file:shadow-sm file:ring-1 file:ring-line-strong"/>
      </Field>
      {error && <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}
      {rows.length>0 && <div className="overflow-x-auto rounded-lg border border-line">
        <table className="w-full text-left text-sm">
          <thead className="bg-subtle text-xs text-ink-muted"><tr>
            <th className="px-4 py-2.5 font-medium">评测组</th><th className="px-4 py-2.5 font-medium">通过 / 总次数</th><th className="px-4 py-2.5 font-medium">完成率</th>
          </tr></thead>
          <tbody className="divide-y divide-line">{rows.map(r=>{
            const rate = r.runs ? 100*r.passed/r.runs : null;
            return <tr key={r.label} className="animate-fade-in">
              <th scope="row" className="px-4 py-3 font-medium text-ink">{r.label}</th>
              <td className="px-4 py-3 font-mono text-ink-muted">{r.passed} / {r.runs}</td>
              <td className="px-4 py-3">
                <div className="flex items-center gap-3">
                  <span className="w-14 font-mono text-ink">{rate===null ? '无运行数据' : rate.toFixed(1)+'%'}</span>
                  {rate!==null && <span aria-hidden className="hidden h-1.5 w-32 overflow-hidden rounded-full bg-line sm:block">
                    <span className="block h-full rounded-full bg-brand" style={{width:`${rate}%`}}/>
                  </span>}
                </div>
              </td>
            </tr>;
          })}</tbody>
        </table>
      </div>}
    </div>
  </Card>;
}
