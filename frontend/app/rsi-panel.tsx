'use client';
import {useRef, useState} from 'react';

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
  return <section className="routing"><h2>RSI 评测对比</h2>
    <p>选择 rsi_run.py 导出的开发集或独立验证报告，在本机查看各组完成率。</p>
    <label>评测报告（JSON）<input type="file" accept=".json,application/json" onChange={e=>read(e.target.files?.[0])}/></label>
    {error && <p role="alert">{error}</p>}
    {rows.length>0 && <table><thead><tr><th>评测组</th><th>通过 / 总次数</th><th>完成率</th></tr></thead>
      <tbody>{rows.map(r=><tr key={r.label}><th scope="row">{r.label}</th><td>{r.passed} / {r.runs}</td><td>{r.runs ? (100*r.passed/r.runs).toFixed(1)+'%' : '无运行数据'}</td></tr>)}</tbody></table>}
  </section>;
}
