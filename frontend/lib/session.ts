import type {components} from '../app/api.generated';
import type {Tone} from './cx';

export type View = components['schemas']['SessionView'];
type State = View['state'];

export const stateLabels: Record<string,string> = {CASE_READY:'待开始',OPENING:'开场',VERIFYING:'核验身份',QA:'整改问答',ACTION_READY:'准备处理',EXECUTING:'处理中',RECONCILING:'结果待查证',WRITEBACK_PENDING:'等待回写',CLOSED:'已完成',CLOSING:'结束中',MANUAL_REVIEW:'需人工处理',ROUTING:'判断渠道',EXPLAINING:'说明整改',CALLBACK_PENDING:'等待预约'};

export const stages: {label: string; states: State[]}[] = [
  {label: '准备', states: ['CASE_READY', 'ROUTING']},
  {label: '开场', states: ['OPENING']},
  {label: '核验', states: ['VERIFYING']},
  {label: '说明与问答', states: ['EXPLAINING', 'QA', 'CALLBACK_PENDING']},
  {label: '业务处理', states: ['CLOSING', 'ACTION_READY', 'EXECUTING', 'RECONCILING']},
  {label: '回写', states: ['WRITEBACK_PENDING']},
  {label: '完成', states: ['CLOSED']},
];

export function stageIndex(state: string) {
  return stages.findIndex(stage => (stage.states as string[]).includes(state));
}

export function stateTone(state: string): Tone {
  if (state === 'CLOSED') return 'success';
  if (state === 'MANUAL_REVIEW') return 'danger';
  if (state === 'RECONCILING' || state === 'CALLBACK_PENDING') return 'warning';
  if (state === 'CASE_READY') return 'neutral';
  return 'brand';
}
