# V0.4 实时交互层交付与验收

日期：2026-11-24。分支：`codex/v04-realtime`。起点：`657310e74b6ef19bd3ecaa9fd0cd4afda159225b`；公共契约版本0.4.0，原API版本保持0.3.0。本文不是生产部署或真实模型验收记录。

## 1. 交付范围与硬门槛

浏览器WebRTC → 后端音频 → 原生理解 → 注入的RealtimeBusinessPort → 已审核片段 → 流式TTS → WebRTC。旧声网模式保留，新模式默认关闭。没有独立可信播放完成证据时，禁止启用语音业务写入。

**用户说完至首个有效业务答案音频开始，P50必须严格小于2000ms。** 240ms是待发送PCM的队列上限，不是端到端基线；等待话术也不是有效答案。相对提速只辅助报告。每种方案至少30个有效轮次，报告全部失败、超时和缺失；P95不超过同条件基线110%，任务成功率不下降。打断到远端非静音PCM停止的目标为P95≤300ms，按钮和自动插话分别报告。PCM观测不等同物理扬声器测量。

本轮没有调用真实供应商、未部署、未启用业务写入；尚不能宣布真实P50达标。队友的业务Mock尚未在指定基线或已获取分支中发现，双方Mock联调待交付。

## 2. 实现与行为

核心代码位于 `backend/app/realtime/`，业务依赖仅为公共端口；未实现第二套业务Agent或状态机。

- 媒体：完整offer/answer、单音轨、无DataChannel、总协商上限20秒。输入重采样为16kHz PCM16单声道，输出24kHz，每帧20ms。线上编码由WebRTC协商。
- 并行：媒体输入、模型接收、业务事件接收、事件归并、提交、合成播放和控制分别运行。输入最多25帧/500ms；输出最多12帧/240ms。输入溢出明确失败，输出施加背压，避免积压多秒音频。
- 输入：一个供应商接收者，按speech item、response ID及epoch绑定。只交付最终文本与理解候选，不把自由模型音频或模型权限字段交给播放器。来源及授权仍由业务侧重验。供应商事件顺序假设需在真实探针中复核，异常绑定拒绝而不猜测。
- 输出：只消费审核片段，固定话术不二次经过通用模型。TTS使用会话级HTTP连接，音频到达就发送；超1200字符只进行保序合成分块，仍属于原业务segment，不改写审核原文。
- 等待：600ms无答案才可合成固定非业务等待反馈，answer=false；业务片段到达时取消等待播报，不将其计入有效回答时延。
- 仲裁：一个播放消费者、一项当前合成任务，后续审核片段有界排队；不并发播放两个音源。没有额外预取整段PCM。
- 打断：本地立即静音；服务端提升epoch、清队列、取消合成和可取消生成。每次入队/发送检查epoch。新输出需要浏览器观察旧RTP排空并确认ready，避免解静音后播出旧缓冲或丢失新答案开头。
- 取消与未知结果：通过C3通知业务；已派发操作继续查证。旧操作结果仍可更新状态，但不能触发旧语音播放。相同事件/片段去重，改内容的同片段拒绝。
- 清理：启动失败、控制断开、媒体失败、显式结束、心跳超时及180秒上限共用幂等退出。心跳10秒，30秒未收到则清理。取消并等待任务，释放轨道、音频上下文、PC、订阅和供应商连接。
- 恢复：重新绑定和协商，从一致快照游标订阅新事件；不自动重播历史答案或重派业务。首期单worker、同一时间1个实时会话。

真实输入模型使用 `qwen3.8-omni-flash-realtime` 的text-only模式；独立ASR和DeepSeek理解不是新路径的强制前置条件。原生模型不接收额外的审核业务话术回传；省略回答、身份及授权须由业务模块结合本地上下文校验，不允许模型自证。

## 3. 接线与队友交接

装配方式：

```python
from app.main import create_app

# teammate_business是队友提供的RealtimeBusinessPort实例；此处不假定工厂名。
app = create_app(realtime_business=teammate_business)
```

不注入时capabilities明确返回不可用，新模式不会回落到另一个未经验证的业务实现。可注入 `realtime_input_factory`、`realtime_speech_factory`、`realtime_media_factory` 测试传输边界；这些不是业务Mock。不要把测试fixture作为实际业务模块部署。

队友交付需包含：公共端口实例或明确工厂入口、正常/慢业务3秒/超时/重复/迟到/打断/断线的配置方法、专用合成会话创建方法、初始化和结束方法、游标及版本规则。本轮不猜测这些入口，也不复制对方业务替身。

五类接口使用：C1提交可信轮次；C2持久补读和持续订阅，早于受理返回的事件暂存归并；C3取消生成；C4重连快照；C5仅报告发送/中断观察，evidence_ref为空。若没有独立证据却返回evidence_verified，实时层停止该路径，不能冒认已告知。

**业务方接入事项：** 旧Coordinator受理入口明确拒绝native_webrtc会话，防止绕过新端口的输入绑定。B的后续功能PR需按契约实现经校验的业务受理适配，不能简单改source或关闭该保护。业务侧保持权限、两个revision、知识版本、幂等和同步数据库事务隔离。开场/CASE_READY的合法推进也由业务模块负责，实时侧不提交伪造用户输入来启动业务。

网络增量：

| 接口 | 内容 |
| --- | --- |
| GET `/api/v1/sessions/{sid}/realtime/ice` | 登录、归属及白名单检查；返回STUN与短期TURN凭据 |
| POST `/api/v1/sessions/{sid}/realtime/offer` | SDP及expected_state_revision，返回answer、connection_binding、expires_at |
| WS `/api/v1/sessions/{sid}/realtime/control?binding=...` | 校验登录、Origin、会话和绑定；ready/heartbeat/interrupt/playback_ready/observation/end |

控制消息最大4096字符、每秒最多40条；通知最多128条，不通过该连接传输PCM。客户端不能提交审核答案、模型事件或业务状态。transport和capabilities字段是兼容增量，DTO生成OpenAPI/TS；不要手改生成文件。退出媒体不等于结束业务；用户点击结束时再调用原业务end接口，后台操作仍需查证回写。

## 4. 配置、依赖、部署准备与回滚

安装基础依赖后，可选安装 `backend/requirements-rtc.lock`，锁定aiortc/PyAV及其依赖。未安装RTC时旧应用仍可启动。Docker可用 `INSTALL_REALTIME=true` 构建，`compose.realtime.yaml` 是可选覆盖文件；本轮未执行部署。

新增配置见 `.env.example`：REALTIME_WEBRTC_ENABLED默认false、REALTIME_APPLICATION_IDS为合成申请白名单、REALTIME_STUN_URLS为JSON列表、REALTIME_TURN_URLS为JSON列表、REALTIME_TURN_SECRET仅服务端保存。MAX_DAILY_NATIVE_SESSIONS默认2，MAX_DAILY_NATIVE_TURNS默认8。现有百炼地域、Key、工作空间、TTS和云调用开关仍须显式配置；VOICE_WRITES_ENABLED保持false。

TURN采用300秒短期凭据，浏览器不接触长期签名密钥；服务端与浏览器均使用相应ICE配置。Caddy仅代理HTTPS/WS，不能替代媒体连通配置。容器host candidate可能无法公网访问，公网优先准备可验证的TURN中继；只配置STUN或开放443不能视为媒体验收通过。

为避免实时接收循环同步访问数据库，原生理解在建连前保守预占整通最多8轮额度，实际少说也不退回；计数是预留尝试量而不是实际usage或账单，失败预占也保留。TTS按每个合成请求预占额度。达到额度拒绝，不清零账本，不因本轮开发自动扩大额度。

回滚：关闭REALTIME_WEBRTC_ENABLED并回到原声网模式；不删除数据、不执行down -v、不重置计数。业务核心和数据库结构未迁移，旧会话voice_transport缺省为agora。

## 5. 无云验证与证据边界

从仓库根运行：

```text
python scripts/export_contracts.py
python scripts/export_realtime_contract.py --check
npm run types --prefix frontend
npm run typecheck --prefix frontend
npm run build --prefix frontend
python -m pytest -c backend/pytest.ini backend/tests/test_realtime_contract.py backend/tests/test_realtime_runtime.py backend/tests/test_realtime_provider.py backend/tests/test_realtime_media.py backend/tests/test_realtime_boundary.py
node scripts/verify_realtime_browser.cjs
```

浏览器脚本需Playwright Chromium；可用REALTIME_TEST_PYTHON指定Python，PLAYWRIGHT_BROWSERS_PATH指定已有浏览器缓存。脚本只启动127.0.0.1:18764的临时测试应用，不连接数据库/供应商，结束后停止进程。其20次循环使用真实浏览器WebRTC、实际音频编解码与测试边界，不是队友Mock或真实模型验收。报告写入忽略目录 `.cache/realtime-browser.json`。

新测试覆盖：3秒受理等待期间持续消费音频和模型事件、提前事件不丢、首PCM不等整段合成、重复/迟到过滤、合成中打断后下一轮恢复、两段审核答案独立播放、未知结果/超时不播成功、20次资源清理、输入背压、实际本地双向RTP/重采样、文本长度保序分块、权限字段拒绝、旧输入入口隔离、短期TURN和Origin拒绝。

同条件真实延迟记录格式：JSON对象含metadata及samples。metadata包含case_set_sha256、network_profile、client、measurement_method=`controlled_input_end_to_remote_pcm`，另记录版本、模型和媒体差异；samples每轮记录status和effective_answer_ms，失败样本也保留。浏览器用同一performance时钟，在可控输入音频结束时发 `zhiyu-realtime-input-ended` 观测标记，收集 `zhiyu-realtime-metric`。自然麦克风VAD估计单列，不能冒充已知输入结束时刻。

```text
python scripts/report_realtime_latency.py --baseline baseline.json --candidate candidate.json --output comparison.json
```

脚本使用nearest-rank分位数；2000ms整也判不达标。缺失视为失败，不从分母删除；少于30个有效样本、条件不一致、失败率升高、P95恶化或P50不低于2秒均不通过。报告不会自动授予业务正确性验收。先做同业务Mock语音对照，再做真实业务闭环，分别交付结果。

## 6. 当前交付与待验收清单

| 项目 | 状态 |
| --- | --- |
| WebRTC信令、控制、媒体格式、可选依赖 | 已实现，本地媒体测试通过 |
| 并行任务、审核片段流式TTS、仲裁、打断、清理 | 已实现，测试边界验证通过 |
| 浏览器按钮及自动插话、20次建连退出 | 本地真实浏览器适配测试通过，不代表真实模型指标 |
| 新Schema/前端类型、检查工作流 | 已生成并加入自动检查 |
| 队友七类Mock完整联调 | 待队友交付入口与会话初始化方式 |
| 原生模型事件真实兼容、噪声/双讲、弱网/TURN | 待明确预算与环境授权后验证 |
| 有效业务答案P50 < 2000ms及P95/失败率 | 未实测，未验收 |
| 独立WebRTC可信播放证据及业务写入闭环 | 未验收，写入保持关闭 |

本地验证的具体命令、数量及环境限制见本PR说明和提交的脱敏汇总。隔离源码目录复用了既有node_modules链接，默认Turbopack拒绝跨根依赖，因此额外使用 `npm run build --prefix frontend -- --webpack` 完成生产构建；正常npm ci环境由原CI执行默认构建，不能把链接路径问题当产品已修复。

最终本地记录：隔离临时PostgreSQL测试库全量 **458 passed**（1条已有Starlette/httpx弃用提示）；契约Schema一致性通过，前端类型检查及Webpack生产构建通过。真实Chromium与aiortc完成20次建连/退出，按钮和自动插话停止输出观察均完成。浏览器报告使用测试边界音频，不得当作真实模型P50；对方Mock、真实输入理解准确率、30轮同条件P50及公网弱网/TURN仍待验收。脱敏摘要见 [本地验证汇总](v04-realtime-local-results.json)。默认agora传输保持旧创建请求收据的hash语义，兼容性已在专用库测试。

官方协议依据：[Qwen原生WebSocket](https://help.aliyun.com/en/model-studio/omni-realtime-websocket-access)、[客户端事件](https://help.aliyun.com/en/model-studio/client-events)、[流式TTS HTTP](https://help.aliyun.com/en/model-studio/qwen-audio-tts-http-api)、[aiortc API](https://aiortc.readthedocs.io/en/latest/api.html)。文档能力不等于当前Key、网络和版本的真实验收。
