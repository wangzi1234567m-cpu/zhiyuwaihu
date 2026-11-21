# 骨架数据流验证记录

日期：2026-11-16。实现／契约版本0.3.0，数据库迁移版本0002。

## 结论

**基本文本数据流已实际跑通。** 使用Windows本机临时PostgreSQL 18.7，未使用Docker或WSL；前端Next.js、后端FastAPI均启动为真实HTTP服务，浏览器使用Playwright Chromium。

实际链路：浏览器登录→读取三条合成申请单→创建会话→主动开场→身份核对→整改问答→模拟驳回→结构化回写→刷新恢复。Agent、模型和业务系统分别是DemoAgent、FakeModel、MockBusiness，未连接真实京东或真实语音服务。

## 最终验收

| 检查 | 结果 |
|---|---|
| pytest | 30项通过，无跳过；包含11项真实PostgreSQL集成测试 |
| 浏览器完整流程 | 1项通过：登录、开始、三轮交流、回写及刷新恢复 |
| 持久化事实核对 | 1会话、7消息、54连续事件、1操作、1业务效果、1回写记录 |
| 操作结果 | succeeded；会话CLOSED；已核验；没有重复业务效果 |
| 迁移与种子 | 真实PostgreSQL执行0001和0002；upgrade head及种子可重跑 |
| Next.js | 生产构建通过，浏览器实际通过前端同源代理访问后端 |
| 其他此前检查 | Pydantic样例、类型检查、依赖检查、Compose静态解析、生成稳定性及文档链接通过 |

结构化事实见 [flow-evidence.json](flow-evidence.json)。浏览器截图在frontend/test-results/smoke-three-text-rounds-writeback-and-reload/completed-flow.png；该测试输出目录不提交Git。

## 本轮发现并修复

1. **消息顺序不稳定**：Windows时间戳精度会让同轮用户和AI消息同时间写入，按UUID排序可能把用户消息排在最后。新增每会话单调position及唯一约束，迁移已有消息；回归用例强制全部时间戳相同，仍保持问答顺序。
2. **SSE在前端代理处被缓冲**：直连后端能收到事件，Next.js代理启用gzip后小事件帧被缓存，页面一直停留在开场。关闭Next.js压缩，并为SSE设置no-transform；浏览器全流程已复验。
3. **中断测试时机不确定**：改为等待生成器进入后、明确阻塞输出，再触发取消，验证取消发生在业务派发前且没有业务效果。

## 复验与边界

设置专用_test数据库的DATABASE_URL后，执行README中的迁移和pytest命令。前端构建及Chromium安装完成后，运行 `python scripts/verify_browser_flow.py`：它会重置专用测试库、临时启动前后端、运行浏览器流程、核对数据库事实并停止前后端进程。

临时验证库仅监听127.0.0.1:55432；本轮结束时停止数据库。没有安装系统服务或修改WSL。缓存中的安装包和测试数据不提交Git。

尚未验证或实现：Docker容器实际启动、云端声网、真实模型、真实业务接口、公网部署、语音时延／打断和RSI改善。文本链路通过不代表这些能力达标。CI配置已编写但尚未在GitHub运行。

## 首次CI回写断言失败后的修复

代码审查发现测试将身份已核对／旧的打断按钮禁用状态当作当前回复结束，可能提前发送下一句，打断尚未完成的整改告知。现改为绑定POST返回的generation_id，等待对应assistant消息status=completed再继续；最终回写仍须页面实际显示。

同时修复turn.started的busy展示、发送请求后旧快照覆盖新事件的问题。服务端快照读取锁定会话，保持状态、消息、操作与游标一致。失败时附加backend-session-snapshot用于CI诊断。

本地使用FAKE_TOKEN_DELAY=0.08（原为0.015）复验：浏览器完整流程通过，1次操作和1条回写；30项后端测试通过，类型检查和生产构建通过。CI采用相同慢速设置覆盖异步等待。远程CI仍需要推送后重新运行，不将本地结果表述为远程已通过。

## 2026-11-18语音与模型适配增量

本轮在独立PostgreSQL上执行58项后端测试全部通过；云请求全部MockTransport，未消耗账号额度。新增测试包括DeepSeek流协议、首包前取消、首文本／整轮超时、脱敏错误、持久化调用限额、声网绑定鉴权、轮次去重／重放、晚到响应、断开取消、单会话限制和生命周期清理。最后调整Agent的RTC＋RTM组合Token后，相关8项语音数据库测试重新通过。

前端类型检查和生产构建通过。原文本浏览器三轮流程、业务提交、回写和刷新恢复复验通过；新增首文本观测事件后，本次证据为1会话、7消息、58连续事件、1操作、1业务效果、1回写。所有语音字幕仍待真实声网验证。

根目录.env已准备，内部随机密钥已生成，账号ID／Key留待本人填写。Docker引擎仍因Windows Secrets Engine失效socket而无法启动；代理进程重命名目录被Windows拒绝，已提供交互式管理员修复脚本。真实DeepSeek、声网云端三轮语音和公网HTTPS均尚未验收，不将模拟测试视作云端通过。详细配置与差异见 [接入交接](voice-integration.md)。

## 2026-11-18 20:35 Docker容器验收

用户重装Docker Desktop 4.99.0 (245822)后，Docker Engine 29.9.3 linux/amd64与Compose连接正常。此前Windows组件修复及虚拟机平台重新安装后，vmcompute/hns运行正常。管理员脚本取消了本项目先前登记的一对开机socket目录改名任务，保留其他四对系统待执行操作；无需继续运行旧的修复脚本。

第一次真实镜像构建发现backend/.pytest_cache被包含在上下文，因缓存目录权限导致打包失败。补充.dockerignore排除嵌套pytest缓存、Playwright产物、tsbuildinfo及.env.*后重新构建成功；未修改目录权限，未改变业务代码。Docker日志在.cache/docker-compose-build.log。

`docker compose up --build -d`完成：db/backend健康检查通过，frontend页面HTTP可达。运行原有Playwright冒烟：1 passed (6.2s)，覆盖登录、开场、三轮文本、身份核对、受控问答、模拟业务回写和页面刷新。调用仅使用DemoAgent/FakeModel/MockBusiness，未启用云端凭证覆盖文件。外层临时Python打印命令因Windows GBK无法输出Playwright字符而报编码错误；原始Playwright日志明确为1 passed，未因此重复运行业务测试。

数据库核对：1个CLOSED且verified的会话、7消息、58连续事件、1个succeeded操作、1个业务效果、1条回写，迁移版本0002，PostgreSQL 18.7。重启本项目三个容器后，HTTP与健康检查恢复，上述事实逐项一致，没有重复业务效果；启动过程重复执行迁移和种子没有新增重复数据。结构化证据见 [docker-evidence.json](docker-evidence.json)。

此结果验证本地Linux容器文本部署；未验证Windows／Docker Desktop再次重启恢复、真实声网音频、DeepSeek真实调用、Qwen TTS、公网HTTPS或语音延迟。容器保留运行供本机联调，页面为http://localhost:3000，后端健康为http://localhost:8000/health/ready。

## Qwen TTS转换与环境配置增量

新增Qwen-Audio-3.1-TTS-Flash的HTTP SSE到声网generic_http PCM转换，独立会话鉴权、starting开场授权、参数固定、超时、取消、错误脱敏及持久调用上限。真实模型／TTS探针使用scripts/probe_cloud.py，根目录配置增加TTS_PROVIDER与百炼参数；不需要将百炼Key填写到AGORA_TTS_CONFIG。

82项后端测试在Docker PostgreSQL专用_test库通过（17.56秒），云请求均MockTransport；包含首音频前／后取消、格式和截断错误、模型／音色覆盖拒绝、跨域URL拒绝、鉴权与过期。OpenAPI导出、前端类型生成／类型检查及Docker生产构建通过。

对已完成过同一申请单的持久演示库重复运行原浏览器用例，触发既定跨会话幂等保护并转MANUAL_REVIEW，因此首次办理回写断言失败；未改动业务规则或幂等键。新增scripts/verify_compose_browser.py，临时使用新建专用_test库执行同一浏览器用例，再恢复原后端。隔离复验1 passed (4.5秒)，测试库核对1个CLOSED会话／1操作／1效果／1回写，原演示库保留。脚本暂时重建后端容器，使用前应避免正在进行的本地演示通话；测试库与证据保留在本机。

用户已允许各一次DeepSeek／百炼真实探针，赠送额度确认已保存；最新配置检查仍发现DEEPSEEK_API_KEY及DASHSCOPE_API_KEY为空，所以没有执行任何真实云请求。固定公网HTTPS未就绪，未进行声网端到端语音或腾讯云部署。资料与操作步骤见[公网部署说明](public-deployment.md)和[接入交接](voice-integration.md)。

## 填写凭证后的真实探针

11月21日服务器后续实测（用户终端输出）：Docker Engine 29.10.0／Compose 5.8.0已安装，80／443／3000端口已释放且nginx开机启动关闭。Docker Hub直连出现TCP 443超时，配置腾讯云官方内网镜像源后，hello-world:latest拉取和运行通过，摘要sha256:1e4c05b897a751332ce85c7e9449da2d93514ea2dea6dbb0e2753c9e949b2bfa。此证据只证明服务器镜像下载和最小容器运行，尚不代表项目容器、公网HTTPS或声网接入通过。

后续部署授权变更：用户明确同意停止旧Nginx和3000端口Node服务，以本项目为主。当前主方案重新采用独立Caddy／compose.public-ip.yaml，Nginx复用配置仅保留作备选。新增scripts/release-server-ports.py，默认只检查，--stop仅停止nginx.service和经可执行文件、进程启动时间核实的3000端口Node进程；对进程自动重启只报告，不强杀整个管理器。Python语法及帮助入口已检查，尚未在服务器执行停止。

11月21日服务器盘点补充：用户提供Ubuntu 24.04.4 amd64、可用内存约1487MiB、磁盘余量约24GB、Nginx监听80且已有Node监听3000、Docker未安装；腾讯云防火墙截图确认80／443已放行。因此新增宿主Nginx方案compose.nginx.yaml，Compose解析确认前端只绑定127.0.0.1:13000、不保留3000映射、不启动gateway。新增Ubuntu官方仓库安装脚本、ACME片段、HTTPS站点模板及Nginx续期脚本，两个sh脚本Bash语法检查通过。未登录／修改服务器，Nginx模板仍需结合实际nginx -T审查并在服务器nginx -t通过后启用；不得将这些静态检查表述为公网证书或部署通过。

后续用户已填写两家Key及四项声网凭证。按此前授权各请求一次（无重试）：DeepSeek成功返回4字文本；百炼Qwen-Audio-3.1-TTS-Flash、longanhuan_v3.1音色合成成功。收到的PCM封装WAV后为24kHz／单声道／16位、2.64秒、126764字节，文件仅保存在.cache/qwen-tts-probe.wav。结果记录于.cache/real-cloud-probes.json，不包含Key或上游原始错误。

探针将首输出超时临时放宽至15秒以验证连接，并设置模型总超时30秒／TTS总超时40秒；没有测得或声称语音端到端P50。根目录及运行中应用的MODEL_PROVIDER=fake、CLOUD_CALLS_ENABLED=false、VOICE_ENABLED=false保持；声网公网接口、真实音频播放／打断及腾讯云部署均未验收。
