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
