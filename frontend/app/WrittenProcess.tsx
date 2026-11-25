'use client';
import {useEffect,useRef,useState} from 'react';
import {api} from '../lib/api';
import {stateNames,taskComplete,type View,type WrittenPreview} from '../lib/workbench';

export default function WrittenProcess({view,pending,onExecute}:{view:View;pending:boolean;onExecute:(preview:WrittenPreview,key:string)=>Promise<void>}) {
  const [preview,setPreview]=useState<WrittenPreview|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[reload,setReload]=useState(0);
  const completed=taskComplete(view);
  const receipt=useRef<{preview:string;key:string}|null>(null);
  useEffect(()=>{
    let active=true;setLoading(true);setError('');setPreview(null);
    api<WrittenPreview>(`/staff/sessions/${view.session_id}/written-preview`).then(p=>{if(active)setPreview(p);})
      .catch(e=>{if(active)setError(e instanceof Error?e.message:String(e));}).finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[view.session_id,view.state_revision,view.last_event_seq,reload]);
  async function execute(){
    if(!preview?.executable||!preview.preview_id)return;
    if(receipt.current?.preview!==preview.preview_id)receipt.current={preview:preview.preview_id,key:crypto.randomUUID()};
    await onExecute(preview,receipt.current.key);setReload(n=>n+1);
  }
  return <section className="written-process" aria-label="书面驳回与结果回写">
    <div className="section-heading"><h3>{view.route==='jingmai'?'京麦书面驳回':'后续书面驳回与结果回写'}</h3><button className="refresh-button" disabled={loading||pending} onClick={()=>setReload(n=>n+1)}>重新核对正文</button></div>
    <p className="muted">预览不产生业务效果。执行时由服务端重新渲染并校验版本、正文和业务证据。</p>
    <div className="written-sheet"><span className="eyebrow">已审核书面意见</span>
      {loading?<p role="status">正在读取权威正文…</p>:error?<p role="alert">{error}</p>:preview?.previewable?<>
        <p data-testid="written-body">{preview.written_text}</p><small>模板版本：{preview.template_version} · 审核版本：{preview.case_revision}</small>
        <details className="source-details"><summary>正文来源与版本校验</summary><ul>{preview.source_refs?.map((r,i)=><li key={i}>{r.sheet} · 第{r.row}行 · {r.columns.join('、')}</li>)}</ul><code>SHA256 {preview.body_sha256}</code></details>
      </>:<p>当前没有可用的已审核书面正文。</p>}
      {!!preview?.missing_slots?.length&&<p className="notice warning">缺少可信事实：{preview.missing_slots.join('、')}</p>}
    </div>
    {!completed&&!!preview?.unavailable_reasons?.length&&<div className="notice warning">{preview.unavailable_reasons.map((r,i)=><p key={i}>{r}</p>)}</div>}
    <div className="written-actions">{completed&&<p className="muted">已完成业务执行和结果回写，无需重复提交。</p>}<button disabled={loading||pending||!preview?.executable} onClick={()=>void execute()} data-testid="written-execute">执行模拟京麦驳回</button><span className="muted">业务效果与回写结果在右侧分别确认。</span></div>
    {view.ended&&view.state!=='CLOSED'&&<p className="notice warning">通话已结束；当前“{stateNames[view.state]}”，尚未形成完整关单。</p>}
  </section>;
}
