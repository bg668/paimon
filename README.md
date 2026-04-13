# paimonsdk

`paimonsdk` 是一个偏底层的 Python Agent Runtime SDK，用来构建可自定义的 agent loop。

仓库采用纯 SDK 结构：

- `src/paimonsdk/`：正式发布的包代码
- `tests/`：SDK 回归测试
- `examples/`：最小示例与示例配置
- `scripts/`：本地 smoke 检查脚本
- `docs/`：功能说明、开发者参考与设计文档
- `_archive/refs/`：归档的 TypeScript 参考实现，不参与打包发布

常用命令：

```bash
uv run pytest -q
uv build
uv run python examples/chat_demo.py
```

文档入口见 [docs/README.md](/Users/biguncle/project/uu-work/docs/README.md)。
