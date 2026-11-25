import type {components} from '../app/api.generated';
import manifest from '../../docs/design/source-manifest.json';

export type View = components['schemas']['SessionView'];
export type Application = components['schemas']['ApplicationView'];
export type BusinessEvent = components['schemas']['Event'];
export type Capabilities = components['schemas']['Capabilities'];
export type Transport = 'text'|'voice'|'native_webrtc';
export type TaskViews = Record<string, View>;
export const routeNames = {phone:'电话告知',jingmai:'京麦书面',manual:'人工核实'};
export const stateNames:Record<View['state'],string> = {
  CASE_READY:'待处理',ROUTING:'渠道判定中',OPENING:'正在开场',VERIFYING:'身份核验中',EXPLAINING:'原因与整改告知',
  QA:'整改问答中',CALLBACK_PENDING:'待人工确认回电',CLOSING:'通话结束 · 待查证',ACTION_READY:'待执行书面操作',
  EXECUTING:'业务执行中',RECONCILING:'操作结果待查证',WRITEBACK_PENDING:'等待结果回写',CLOSED:'已完成',MANUAL_REVIEW:'需人工处理',
};
const counts = manifest.files.find(file=>'main_channel_counts_D13_D80' in file)?.main_channel_counts_D13_D80;
export const sourceStats = {total:(counts?.['电话']??0)+(counts?.['京麦驳回']??0),phone:counts?.['电话']??0,
  jingmai:counts?.['京麦驳回']??0,archiveSha256:manifest.archive_sha256};
export function taskComplete(view?: View) {
  return !!view && view.state==='CLOSED' && !!view.record && view.operations.length>0 && view.operations.every(op=>op.status==='succeeded');
}
export function eventLabel(event:BusinessEvent):string {
  const p=event.payload;
  switch(p.type) {
    case 'state.changed': return stateNames[p.state];
    case 'turn.started': return '受理新一轮对话';
    case 'generation.finished': return p.status==='completed'?'本轮回答已生成':p.status==='failed'?'本轮回答失败':'本轮回答已中断';
    case 'turn.interrupted': return '取消旧一轮播报';
    case 'operation.updated': return `业务操作：${{pending:'处理中',succeeded:'成功',unknown:'结果未知',not_found_yet:'尚未查明',failed:'失败'}[p.result.status]}`;
    case 'session.ended': return '通话结束，业务结果另行确认';
    case 'knowledge.used': return '引用已审核知识';
    case 'metric.recorded': return '记录响应测量';
    default: return '对话内容更新';
  }
}
