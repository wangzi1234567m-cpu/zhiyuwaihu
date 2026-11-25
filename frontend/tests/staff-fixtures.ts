import type {Page} from '@playwright/test';
export const id=(n:number)=>`00000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
export function projection(n:number,snapshot:Record<string,unknown>|null){
  return {application_id:id(n),label:`申请单${n}`,company_hint:`合成商家${n}`,brand_name:`合成品牌${n}`,scene_name:'合成审核场景',business_category:'授权书审核',scenario_id:'synthetic',
    default_route:n===1?'phone':'jingmai',effective_route:snapshot?.route??(n===1?'phone':'jingmai'),state:snapshot?.state??'CASE_READY',
    case_revision:1,submission_attempt:1,call_attempt:0,contact_outcome:'unknown',contact_event_id:null,reason:'工作人员可见的原因',remedy:'工作人员可见的整改',
    routing_decision:{decision_id:id(9),application_id:id(n),case_revision:1,route:snapshot?.route??(n===1?'phone':'jingmai'),rule_version:'route-r2',reason_codes:[],required_evidence:[],evidence:[]},
    knowledge_version:'demo-k1',rule_version:'route-r2',source_refs:[],snapshot,available_actions:[]};
}
export async function mockStaff(page:Page,snapshots:()=>Record<string,unknown>[]){
  await page.route('**/api/v1/staff/applications',r=>r.fulfill({json:snapshots().map((s,i)=>projection(i+1,s))}));
  await page.route('**/api/v1/staff/applications/*',r=>{const n=r.request().url().endsWith(id(1))?1:2;return r.fulfill({json:projection(n,snapshots()[n-1])});});
  await page.route('**/api/v1/staff/support-catalog',r=>r.fulfill({json:{original_total:68,original_phone:54,original_jingmai:14,knowledge_version:'demo-k1',published_scenarios:2,phone_supported:1,written_supported:2,scenes:[]}}));
  await page.route('**/api/v1/staff/sessions/*',r=>r.fulfill({json:snapshots()[r.request().url().endsWith(id(1))?0:1]}));
  await page.route('**/written-preview',r=>r.fulfill({json:{session_id:id(1),case_revision:1,state_revision:1,knowledge_version:'demo-k1',rule_version:'route-r2',previewable:false,executable:false,source_refs:[],missing_slots:[],unavailable_reasons:['等待业务证据']}}));
}
