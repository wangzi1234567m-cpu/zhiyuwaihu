# 声网与DeepSeek接入交接（2026-11-18）

## 当前状态

代码已增加DeepSeek适配、声网REST生命周期、自定义Chat Completions入口、RTC／RTM语音组件及Qwen TTS协议转换。默认云调用关闭；本地模拟协议测试通过不代表云端已接通。Docker Desktop重装至4.99.0后本地Compose文本部署已通过，公网HTTPS尚未就绪。用户已选择百炼Qwen-Audio-3.1-TTS-Flash；转换代码已实现，账号权限、音色和真实播放尚未实测。

## 你只需填写的账号信息

根目录`.env`已经生成，普通参数与内部随机密钥已填好，已有值会保留。该文件被Git忽略，不提交、不截图、不复制到聊天。

| 字段 | 来源 |
|---|---|
| DEEPSEEK_API_KEY | DeepSeek控制台API Keys |
| AGORA_APP_ID | 声网项目详情App ID |
| AGORA_APP_CERTIFICATE | 同一项目的App Certificate，不能用Customer Secret替代 |
| AGORA_CUSTOMER_ID | 设置→RESTful API中的客户ID／Key |
| AGORA_CUSTOMER_SECRET | 同一行下载的客户密钥／Secret |

INTEGRATION_SECRET是本项目自己生成的入口签名密钥；COOKIE_SECRET用于登录Cookie，二者已随机生成。不要替换成供应商Key。

还需要确认两项非Key资源：声网已开通对话式AI和RTM、中文ASR／TTS供应商与赠送额度。AGORA_TTS_CONFIG目前为空对象，必须按实际开通的vendor及params填写；不能假设声网免费额度涵盖第三方TTS费用。固定公网地址确定后填写INTEGRATION_PUBLIC_URL，必须为HTTPS origin。

配置检查（不访问云、不显示密钥）：

```powershell
.venv/Scripts/python.exe scripts/cloud_doctor.py
```

确认赠送额度后才将CLOUD_CALLS_ENABLED和FREE_QUOTA_CONFIRMED改为true。DeepSeek文本测试需MODEL_PROVIDER=deepseek；语音还需VOICE_ENABLED=true与完整配置。VOICE_WRITES_ENABLED保持false。三轮语音通过后仍须接入可信告知完成证据，不能只改开关就开启写入；当前客户端遥测不会产生这种权限证据。

## Docker修复

用户已执行启用VirtualMachinePlatform并重启。新错误是Secrets Engine无法rename engine.sock；文件为0字节Archive/ReparsePoint。Codex进程中的目录备份操作被Windows拒绝，未删除数据。

在交互式管理员PowerShell运行 `./scripts/diagnose-docker.ps1` 可只读检查。针对当前socket错误，运行 `./scripts/repair-docker-socket.ps1`：停止指定安装目录中的Docker进程、同目录重命名备份socket目录、重新启动Docker。若仍失败保留输出，不能直接恢复出厂、删除VHDX或注销WSL。

目录备份方案依据 [Docker官方问题记录](https://github.com/docker/for-win/issues/15064)，是绕过特定失效socket的方法，并非根因已修复的证明。

引擎恢复后先跑默认离线Compose与文本验收，再启用可选云配置：

```powershell
docker compose up --build -d
# 账号、额度和公网地址准备好之后：
docker compose -f compose.yaml -f compose.cloud.yaml up --build -d
```

默认compose不读取云凭证至容器，cloud覆盖文件通过env_file读取，密钥只进入backend。新增public覆盖文件和Caddyfile留待固定域名就绪使用；APP_ENV=staging，需长密码和安全Cookie，开发登录与故障注入禁用。PUBLIC_ORIGIN必须与浏览器HTTPS域名一致；PUBLIC_HOST填DNS hostname。不要直接把development模式暴露公网。

## 新接口与兼容性

现有文本／业务接口保持兼容，公共业务端口未更名。增量类型见生成OpenAPI；未来扩展应保持同一Pydantic源，不能另维护前端结构。

- POST sessions支持mode=voice；只有配置就绪才接受。credentials返回短期RTC／RTM Token，私钥、Customer Secret和DeepSeek Key不下发。
- POST sessions/{sid}/voice/start使用expected_revision；浏览器须先加入RTC、订阅RTM及发布麦克风，再调用启动。开场由云Agent的greeting_message执行一次，服务器同时保存播放范围unknown的开场记录。
- voice/interrupt、voice/heartbeat、voice/telemetry及GET voice/status用于打断、租约、观测和本地生命周期状态。status不是远程实时状态证明。
- 现有end同时停止本地生成和请求云Agent leave；云结果未知保留cleanup_pending，后台重试。硬上限三分钟、心跳10秒、失联45秒清理，云侧idle_timeout=30。网络故障时无法承诺立即停止计费。
- /integrations/agora/sessions/{sid}/v1/chat/completions只接受绑定Bearer。llm.vendor=custom，从最后user消息读取content和turn_id；顶层turn_id若存在须一致。收到同轮同文本重试重放同一生成，参数改变409；同文本新轮次正常执行；晚到旧轮409。
- 不拼接声网历史。Context和历史由后端生成，声网system/tools不作为权限来源。缺少turn_id拒绝，不使用文本hash伪装轮次ID。
- 后端语音文本delta的channel为voice_transport，只供声网流桥接，前端语音字幕不订阅这份文本。字幕由Toolkit TRANSCRIPT_UPDATED全量替换，业务事实从原SSE获取。

每个生成只有一个Agent执行和模型流，声网SSE通过持久事件重放输出。最后一个订阅断开取消生成；其他重复订阅仍在线时不取消。新ASR使旧generation失效。已派发operation不随取消撤销。

## 模型和事实约束

DeepSeek使用HTTPX、content-only流、非思考模式；不透传浏览器base_url/model/tools，不自动重试付费请求。首content默认2.5秒，整轮8秒，可取消首包前的等待；鉴权、额度、限流、超时与截断返回脱敏错误。

固定身份、整改依据及业务结果使用代码模板。现有DemoAgent仅在非权威问答（例如补齐是否必过）调用真实模型，并将完整候选与演示允许答复集合核对，超出集合使用固定已审答复；此阶段会等待模型完整回复后才能播出，因此不能将其视为低延迟优化已完成。魏的正式Agent接入后再扩展受控生成，不能绕过工具守卫。

模型请求到首content用服务端monotonic记录，区分fake与deepseek；实际播放、用户说完到首文本／首音频仍需真实音频探针或同设备录音标注。RTM状态、play()、收到音频包都不能证明扬声器已发声。本轮未启用录音。

浏览器上报的SDK观测存入RequestReceipt，仅用于追踪，不能确认为身份或业务成功。自然打断观测只有provider_turn_id匹配当前轮时可取消当前生成。它不修改用户确认／已告知事实，也不把全文标记为已播放。

## 额度与验收

每日调用尝试上限持久化到PostgreSQL：模型30次、语音3次，单语音会话。它是本地保守限额，不是供应商余额查询，也不能防止同账号在别处消耗额度。赠送额度需在控制台核实。

DeepSeek单次探针：确认开关、额度及数据库迁移后运行 `python scripts/probe_deepseek.py`，只请求短答，不打印密钥或原始错误体。

真实声网验收仍须固定公网HTTPS：先确认声网请求确实到达入口，再完成3通×至少3轮及6次插话、断网和超时测试。记录版本、sid、provider turn、generation及失败样本；达到验收后停止重复测试。没有公网地址时只能验证RTC设备与自定义入口协议，不能声称三轮语音通过。

技术依据：[声网自定义模型](https://doc.shengwang.cn/doc/convoai/restful/user-guides/custom-llm)、[短期记忆与turn_id](https://doc.shengwang.cn/doc/convoai/restful/user-guides/short-term-memory)、[声网REST快速开始](https://doc.shengwang.cn/doc/convoai/restful/get-started/quick-start)、[打断与RTM参数](https://doc.shengwang.cn/doc/convoai/restful/user-guides/interrupt-agent)、[DeepSeek API](https://api-docs.deepseek.com/)。实际npm声明已核对：RTC 4.24.10、RTM 2.3.0、Toolkit 2.10.0。RTM使用命名导出的RTMClient；AccessToken2来自AgoraIO官方源码，固定提交和LICENSE保存在vendor目录。

## 本轮验证状态

2026-11-18：58项后端测试已在独立PostgreSQL 18.7上通过，包含DeepSeek模拟HTTP流、首包前取消、首文本超时、额度错误脱敏、语音轮次去重／重放、鉴权、断开取消、单会话限制、过期清理和未知启动的频道查证。前端类型检查、生产构建及原文本浏览器流程通过；数据库为1业务操作／1业务效果／1回写。

所有自动测试禁用真实云调用并注入MockTransport，未消耗赠送额度。尚未验证Docker容器实际运行、真实DeepSeek调用、真实声网RTC／RTM／ASR／TTS、三轮语音、实际播放时延和公网鉴权。原文本事实证据不是语音通过证明。

## 2026-11-18后续环境诊断

用户确认对话式AI引擎已开通，后续选择百炼Qwen-Audio-3.1-TTS-Flash。声网级联服务由引擎调用TTS供应商；声网引擎免费分钟不自动涵盖第三方TTS。可在Playground配置受支持的供应商后View code查看tts参数；所选供应商协议不兼容时需要适配。

用户交互式管理员重命名socket仍被拒绝。后续查询：VirtualMachinePlatform和Microsoft-Windows-Subsystem-Linux的InstallState均为1；HypervisorPresent=True；WSL仍报告虚拟化不可用；sc qc vmcompute返回1060（服务不存在），System32/vmcompute.exe不存在。Docker引擎API返回500／超时。此状态提示应先检查Windows组件完整性，不能仅按socket故障反复重命名，也不能仅凭CPU属性False断言BIOS未开启。

下一步需交互式管理员运行DISM /Online /Cleanup-Image /RestoreHealth及sfc /scannow，记录各自完成信息，再重启复查vmcompute和WSL。依据微软WSL故障排查及Windows映像修复文档；这是组件检查／修复步骤，不保证直接解决全部Docker故障。没有注销WSL、恢复出厂、删除VHDX或修改用户数据。

### 管理员诊断更新

后续通过UAC启动`scripts/diagnose-windows-admin.ps1`，管理员报告确认VirtualMachinePlatform及WSL均为Enabled，hypervisorlaunchtype为Auto，vmcompute和hns服务均不存在。DISM `/CheckHealth`返回“可以修复组件存储”，退出码0；该退出码只表示健康检查执行成功，不代表组件存储健康。HypervisorPresent=True，暂不建议更改BIOS或重装Docker。

随后启动`scripts/repair-windows-components.ps1`，顺序执行RestoreHealth和SFC（仅在DISM成功或返回需重启3010时继续）。脚本不自动重启、不删除WSL或Docker数据；完整输出保存在被Git忽略的`.cache/windows-component-repair.txt`。必须审查实际完成结果，不能将已启动修复写成修复成功。管理员脚本需要用户响应Windows UAC。

2026-11-18 17:17（北京时间）已完成此轮修复：RestoreHealth退出码0，CBS记录3057项CSI Payload Corruption已修复及“All WCP store corruptions were fixed”；SFC退出码0，并报告“Windows资源保护找到了损坏文件并成功修复了它们”。WinSxS中两个版本的vmcompute.exe已恢复，但System32/vmcompute.exe仍不存在，vmcompute/hns服务仍缺失，因此不能标记WSL或Docker恢复成功。检查未发现CBS/WU重启挂起标记或pending.xml，但Session Manager仍有6项待重命名操作（归属尚未确认），下一步先由用户保存工作并重启，再复查服务、WSL和Docker；若仍缺失，再考虑分阶段重新安装VirtualMachinePlatform，不手动复制程序或创建服务。

补充的系统级下载诊断`scripts/inspect-windows-download.ps1`因UAC被取消而未执行，未重新尝试该权限请求。这不影响先前获准启动并已完成的DISM/SFC修复。修复过程中未重装Docker、注销WSL、删除发行版或清理镜像卷。

### 重启复查与虚拟机平台重新安装

用户于17:20重启后，复查仍缺少vmcompute/hns服务及System32/vmcompute.exe，WSL2仍报告虚拟化不可用；当时Docker引擎管道不存在，不能把此项单独当作Docker安装损坏的证明。WSL列表报告没有安装发行版。

通过`scripts/repair-virtual-machine-platform.ps1`分阶段重新安装VirtualMachinePlatform：17:49禁用成功，状态Disabled，RestartNeeded=False；因此不强制中间重启，随后执行启用。17:50启用请求成功，状态EnablePending，RestartNeeded=True，必须由用户保存工作并重启后才能继续验收。脚本使用-NoRestart，禁用不使用-Remove，未修改WSL发行版、Docker镜像或卷。状态与阶段日志写入`.cache/virtual-machine-platform-repair.json`和对应txt；每个阶段均需Windows UAC授权。若禁用阶段返回需要重启，脚本会阻止在同次开机内立即启用。

此阶段不代表Docker恢复，重启后需重新检查vmcompute/hns、WSL、Docker Server及Compose文本流程。依据：[微软禁用可选功能参数](https://learn.microsoft.com/en-us/powershell/module/dism/disable-windowsoptionalfeature)、[微软WSL故障排查](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting)。

### 17:58重启后的复查

19:17复查确认vmcompute和hns均Running，System32/vmcompute.exe存在，WSL状态不再报告虚拟化不可用。Windows虚拟化依赖已恢复；但没有已安装的WSL发行版，未完成实际Linux命令启动验证。

启动Docker Desktop 4.97.0后仍因Secrets Engine的engine.sock无法改名而退出，Linux引擎管道未建立。普通进程和独立UAC管理员进程对整个docker-secrets-engine目录的改名均被拒绝；目录为普通目录，ACL中用户和管理员有FullControl，故不能仅归因于缺少管理员权限。未执行Compose构建、数据库迁移或容器浏览器验收，不能标记Docker部署通过。

19:23通过`scripts/schedule-docker-socket-backup.ps1`登记一次Windows开机改名备份：严格校验固定目录仅含一个零字节engine.sock重解析点，使用MoveFileExW的MOVEFILE_DELAY_UNTIL_REBOOT，将完整目录备份至同级时间戳目录。目标不为空，不删除、不覆盖其他目录，不改动镜像、卷或发行版。返回成功只表示任务已登记，不能证明开机改名成功或Docker恢复；详细计划在忽略的`.cache/docker-socket-boot-backup.json`，重启后需检查目标目录与Docker启动。用户须保存工作并手动重启，脚本不自动重启。

这是基于微软延迟改名机制的一次恢复尝试，并非Docker官方保证有效的修复。依据：[Docker同类socket问题记录](https://github.com/docker/for-win/issues/15064)、[微软MoveFileExW文档](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)。当前不建议盲目重装相同版本或恢复出厂。

20:07用户反馈重启后仍失败。实际复查：Windows LastBootUpTime仍为17:58:46，Kernel-General事件12的最近启动仍为17:58:47，运行时长约129分钟；19:23登记的目录备份源／目标配对仍在重启队列中，备份目录尚不存在。因此无法将本轮反馈记为“开机改名已执行但无效”；工具未观察到登记操作之后的完整内核重启。快速启动或仅重启应用是可能解释，尚未确认用户具体操作。20:07重新启动Docker，最新日志仍明确报engine.sock改名失败。下一步需保存工作后执行Windows“重新启动”（而非仅关闭／打开Docker）；重启后先核对内核启动事件和备份结果，不重复登记任务。Docker部署仍未通过。

## Qwen TTS接入核对（2026-11-18）

Docker状态更新（20:35）：重装至Docker Desktop 4.99.0后，引擎已恢复且Compose文本部署通过，旧的开机目录备份任务已精确取消。PostgreSQL／后端／前端运行正常；浏览器三轮、回写、刷新及容器重启持久化均通过。详见[验收记录](verification.md)与[结构化证据](docker-evidence.json)。此前故障段落为排查历史，不再代表当前Docker状态；真实云端语音仍未验收。

百炼官方确认模型ID为`qwen-audio-3.1-tts-flash`，适用于实时客服；它与`qwen3-tts-flash`、CosyVoice是不同模型，不应混用参数。官方HTTP入口为`https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer`（北京），新加坡使用`{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com`；API Key必须与地域匹配。请求使用`model`和`input.text/voice/format/sample_rate`；流式输出通过SSE返回Base64音频。

声网`generic_http`要求兼容OpenAI TTS协议。两端协议不同，百炼原生URL不能直接当generic_http入口。本项目现已增加`backend/app/tts.py`和`/integrations/agora/sessions/{sid}/v1/audio/speech`：接收OpenAI风格input，调用百炼HTTP SSE，逐块解码Base64并输出24kHz单声道PCM16。只允许配置的模型／音色、PCM和默认语速，不透传任意URL、SSML或指令；输出格式需由实际探针及声网播放确认。

用户已确认北京地域和WorkspaceId；实际值及HTTP地址只在忽略的根目录`.env`保存。`TTS_PROVIDER=qwen`时自动生成声网generic_http配置，AGORA_TTS_CONFIG不再需要手写。TTS使用与LLM分离的会话绑定HMAC，只发给声网，不向声网／浏览器发送百炼Key。授权允许starting状态的主动开场，结束／过期拒绝；手动打断和停止会取消已注册TTS流，HTTP断开也关闭上游。晚到请求和声网播放丢弃仍需真实语音验收，不能仅凭本地取消测试宣布打断达标。

默认首音频超时5秒、整轮30秒，每日最多100次合成尝试（PostgreSQL持久计数，不是余额查询）。首音频前的错误返回脱敏JSON；流已开始后的错误中断流，不把失败转换为成功音频。默认不保留通话录音；明确运行的合成探针只在忽略的.cache生成测试WAV。模型、话术、身份确认和业务工具的原有边界保持。

用户已确认DeepSeek和百炼赠送额度，并允许各一次探针；FREE_QUOTA_CONFIRMED已设true，CLOUD_CALLS_ENABLED／VOICE_ENABLED仍关闭。最新检查确认两家Key及四项声网凭证均已填写。按授权分别执行了一次真实探针：DeepSeek成功返回4字文本；Qwen成功返回24kHz、单声道、16位PCM，转换为2.64秒WAV保存在忽略的.cache/qwen-tts-probe.wav。未自动重试；仅探针进程临时启用云调用并放宽连接验证超时，不改变运行中应用开关，也不作为实时语音时延达标证据。声网账号权限仍待实际REST／云端联调验证，不能只凭字段已填认定可用。

探针命令为`.venv/Scripts/python.exe scripts/probe_cloud.py deepseek`与`... scripts/probe_cloud.py tts`；后续再次执行会产生新的供应商请求，不应将其当作无费用的配置检查。示例音色longanhuan_v3.1本次真实探针成功；尚未验证声网实际播放和端到端打断。

腾讯云服务器／域名检查及固定HTTPS操作见[公网部署说明](public-deployment.md)。本地模型／TTS探针只需出站网络；声网云端调用自研入口需要公网可访问HTTPS，不能用localhost证明云端可达。

依据：[百炼模型概述](https://help.aliyun.com/zh/model-studio/tts-model/)、[Qwen-Audio-TTS HTTP API](https://help.aliyun.com/zh/model-studio/qwen-audio-tts-http-api)、[百炼合成教程](https://help.aliyun.com/zh/model-studio/non-realtime-tts-user-guide)、[声网generic_http发布说明](https://doc.shengwang.cn/doc/convoai/restful/overview/release-notes)。
