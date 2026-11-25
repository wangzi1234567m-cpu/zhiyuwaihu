# 接口与来源

- `design/contracts/openapi.json`：HTTP API，由后端 Pydantic 导出。
- `design/contracts/schema.json`：公共 DTO JSON Schema。
- `design/contracts/realtime-v04.schema.json`：实时交互与业务之间的内部契约。
- `design/source-manifest.json`：知识来源的文件哈希和原始场景统计。
- `THIRD_PARTY.json`：运行组件与云模型的来源、版本和许可说明；声网 SDK 的许可证保留在源码目录。

安装、启动、回滚与测试见根目录 README。生成的测试结果写入 `.cache`，不作为运行源码提交。