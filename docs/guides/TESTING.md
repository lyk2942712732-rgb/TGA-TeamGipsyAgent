# 测试运行指南

## 后端测试

```powershell
python -m pytest -q
```

## Windows：必须使用短的 `--basetemp` 路径

pytest 的 `tmp_path` 默认落在 `%LOCALAPPDATA%\Temp\pytest-of-<user>\` 下。迁移与
输入相关的用例会在该目录里再生成带时间戳与哈希的长文件名，例如：

```text
.evidence.db.task-json-v5-backup-20260731T104632909159Z-9554a3e0.json.<uuid>.tmp
```

当基础路径本身较深时，完整路径会超过 Windows 的 `MAX_PATH`（260 字符），
写入直接抛 `FileNotFoundError`。这类失败会被
`tga/migrations/schema_v5_to_v6.py` 的兜底 `except Exception` 吞掉，只在 stderr
留下一句 `migration failed [INTERNAL_ERROR]: source database was not replaced`，
**看起来像迁移逻辑出错，实际是路径长度问题**。

实测：同一套用例在长路径 basetemp 下 40 个失败，换成短路径后只剩 1 个失败。

所以在 Windows 上请显式指定一个**短且在仓库之外**的 basetemp：

```powershell
python -m pytest -q --basetemp=E:\tga-bt
```

> 历史包袱：仓库根目录曾遗留 234 个 `.phase*` / `.pytest-*` 目录（3.4 GB、
> 16510 个文件），就是因为早期用 `--basetemp=.phaseXX` 把 scratch 目录写进了
> 仓库内部来规避路径长度问题。这些目录已清理，并已加入 `.gitignore`；
> 请不要再把 basetemp 指向仓库内部。

也可以启用系统级长路径支持（需管理员权限，重启生效）：

```powershell
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
```

## 前端测试

```powershell
cd apps\web
npm test          # Vitest 单元测试
npm run build     # tsc 类型检查 + Vite 构建
npm run test:e2e  # Playwright 端到端
```

## 已知失败

`tests/test_runtime_context_v6.py::test_context_envelope_labels_selects_new_semantics_and_keeps_retrieval_empty`
断言 TaskHint 内容出现在渲染后的 Context Envelope 中，目前为已知失败，与目录整理无关。
