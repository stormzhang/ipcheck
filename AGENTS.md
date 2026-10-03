# ipcheck 项目规范

网络与本地环境诊断 CLI，Python 3.10+、hatchling；网络模块依赖 requests，本地检测使用标准库。诊断是配置、观测与启发式结果，不能保证平台风控或账号安全。

## 项目地图与入口

- `src/ipcheck/cli.py`：网络采集、风险判定、终端渲染与 CLI 分派。
- `src/ipcheck/local.py`：本地只读检查；`LocalChecker` 可注入 home，禁止上传检测数据。
- `tests/`：unittest；`pyproject.toml`：依赖、入口与包版本。
- `claude_expose_check.py`：独立历史暴露自检，不并入主命令或据旧水印推断当前服务端行为。
- [README.md](README.md)：安装与使用；[诊断字段与实现手册](docs/diagnostics.md)：字段、判定、附属工具和命令；[ROADMAP.md](ROADMAP.md)：状态与验证。

## 不可混淆的语义

- “本机真实 IP”是国内直连服务观察到的公网出口，不是内网地址；“出口 IP”是当前请求路径观察到的代理出口。探测失败或路径不确定时不得断言真实流量走向。
- 环境代理、系统代理和 TUN／VPN分别是进程环境、配置和启发式状态。沙箱观测不能替代宿主机状态或实际出口验证。
- 系统时区不受 `TZ` 影响；CLI 时区优先取 `TZ`。CLI 与桌面版分别比较，各自限制见诊断手册。
- 网络综合结论依现有高／中／低判定，IPv6 仍单独提示；修改字段或风险逻辑前先读手册、核对代码并覆盖判定边界。
- 147 域名名单是冻结的历史快照，来源见 [审计证据](../fuxi/raw/2026-06-30-cc-反蒸馏水印审计/域名黑名单-147项.md)，不是实时官方黑名单。客户端水印状态不证明服务端处理。
- `--local`、`--deep`、`--reveal` 的数据保持本地；默认打码，明文只在显式 reveal 时展示。本地 fail 的退出码为 1，其余为 0；跨平台未覆盖项必须如实标注。

## 工程边界

- 版本同时更新 `src/ipcheck/__init__.py:__version__` 和 `pyproject.toml:[project].version`。
- 渲染复用 `C`、`tbl_*`、`_emit_cell()`、`_wrap_ansi()`、`display_len()` 与 `char_width()`；保持 ANSI 跨行颜色、中文宽度、窄终端折行和 `fit_width()` 自适应。
- 状态用颜色与 ASCII `!`／`-`；禁止歧义宽度符号导致边框错位。
- 检测分区顺序和风险语义见手册；跨平台降级使用 `IS_WIN`，外部 API 保持超时。不得读取凭据后写入夹具、日志或提交。

## 验证

```bash
PYTHONPATH=src python -m unittest discover -s tests
python -m build
```

按改动检查 CLI 退出码、窄终端、中文／ANSI 对齐和相关平台降级。联网诊断不能替代确定性单元测试。PyPI 包名为 `ai-ipcheck`，CLI 为 `ipcheck`；发布须单独授权。
