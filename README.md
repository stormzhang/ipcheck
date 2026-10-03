# ipcheck

网络环境诊断工具，检测 IP、DNS、代理、风险评分、时区一致性与 Claude 端点，帮助排查网络配置。附本地只读环境检查（`--local`），提示账号标识与历史残留。检测结果不保证平台风控、账号安全或所有残留已被覆盖。

[English](./README_EN.md)

维护时先读 [项目规范](AGENTS.md)；字段、风险判定与附属历史工具详见 [诊断手册](docs/diagnostics.md)。

![screenshot](./screenshot.png)

## 为什么需要这个工具

想让 Claude Code、OpenAI API、Cursor 等 AI 工具流畅稳定运行，网络环境配置至关重要。以下问题可能影响使用体验：

- **IPv6 泄露真实地址** -- 代理通常只处理 IPv4，IPv6 会暴露你的实际位置
- **DNS 泄露** -- 使用国内 DNS 会暴露真实地理位置
- **IP 风险过高** -- 机房 IP 或被滥用的 IP 可能影响连接质量
- **时区不一致** -- 本地时区配置与 IP 所在地不匹配
- **Claude 端点走了中转** -- 第三方中转站可能泄露数据，甚至触发封号

`ipcheck` 一次检查这些信号，帮助排查网络配置；覆盖范围受平台、网络和第三方数据源限制。

## 功能

| 检测项 | 说明 |
|--------|------|
| 本机真实 IP / IPv6 | 经国内直连回显拿到真实公网 IP（即使开着 VPN/TUN 也能露出真实 ISP 出口），并确认 IPv6 是否已禁用 |
| 本地 DNS | 识别 DNS 来源（国内 / 国外），标注已知 DNS 服务商 |
| 出口 IP 信息 | 出口（代理后）IP、国家 / 地区、城市、运营商、IP 归属、时区 |
| 代理检测 | 环境变量代理、系统代理、TUN/VPN 的开关状态 |
| IP 类型与风险 | 机房 / 住宅识别、proxycheck.io 风险评分、StopForumSpam 滥用记录、是否被标记为代理 |
| 时区一致性 | 系统时区与 CLI 时区（全 IANA），分别按 CC CLI（认 `$TZ`）和桌面版（走系统时区）比对出口 IP 时区 |
| Claude 端点检测 | 识别 Claude Code 端点是官方直连 / 国产大模型 / 第三方中转；中转会提示数据泄露与封号风险，并比对已知端点黑名单 |
| 本地环境检测（`--local`） | 换号防关联：收集本机 Claude 账号身份标识残留（`.claude.json` / 滚动备份 / 遥测重发队列）、shell 配置、Chrome Cookies、钥匙串凭证、进程与安装残留，敏感值默认打码 |
| 换号就绪度（`--full`） | 网络 + 本地都跑，末尾给出合并结论：本地有高危残留或网络高风险 → 不建议换号 |
| 综合结论 | 汇总各项风险，一句话判定当前环境 Claude 使用为高 / 中 / 低风险 |

## 安装

```bash
pip install ai-ipcheck
```

升级到最新版：

```bash
pip install --upgrade ai-ipcheck
```

## 使用

```bash
ipcheck                  # 网络环境诊断（默认）
ipcheck --local          # 本地环境检测（Claude 换号防关联，纯本地只读，零上传）
ipcheck --full           # 网络 + 本地，末尾给出换号就绪度合并结论
ipcheck --deep           # 本地检测加深扫会话记录中的历史标识（隐含 --local）
ipcheck --reveal         # 敏感值显示明文（默认打码，隐含 --local）
```

退出码：本地检测存在高危残留（fail）时为 `1`，否则为 `0`，可用于脚本串联。

### 环境要求

- Python 3.10+
- 支持 macOS / Linux / Windows

## 结果说明

**本机真实 IP & DNS** -- 本机真实 IP 经国内直连回显获取，即使开着 VPN/TUN 也能露出真实 ISP 出口，用于确认真实身份是否泄露。IPv6 建议禁用，大部分代理不处理 IPv6 流量，开启后可能同时暴露两个不同地区的 IP。若检测到国内 DNS，需要在代理软件中调整 DNS 设置。

**出口 IP 信息** -- 显示经过代理后的出口 IP、所在国家/地区、运营商、IP 归属和时区。这些信息直接影响 AI 服务对你请求来源的判断。

**代理检测** -- `ipcheck` 会展示环境变量代理、系统代理和 TUN/VPN 的开关状态。系统代理只代表系统配置状态，不等于所有 CLI 流量都会继承；Codex、Claude Code 等带沙箱或独立网络栈的工具，可能需要显式设置 `HTTP_PROXY` / `HTTPS_PROXY`，或者开启 TUN 模式兜底。

**IP 风险评估** -- 检测 IP 是住宅还是机房类型。机房 IP 不一定有问题，但会进一步查询风险评分和滥用记录。如果风险评分偏高，建议更换节点。

**时区一致性** -- 分两条比对：**CC CLI** 比对 CLI 时区（认 `$TZ`）与出口 IP 时区，**桌面版** 比对系统时区（不认 `$TZ`）与出口 IP 时区。Claude Code CLI 认 `$TZ`，可在 shell 或 `~/.claude/settings.json` 的 `env` 里把 `TZ` 设为与出口 IP 匹配的 IANA 时区（如 `America/Los_Angeles`）；Claude 桌面版走系统时区，需改系统设置。

**Claude 端点检测** -- 读取 Claude Code 的 `ANTHROPIC_BASE_URL`，分类为官方直连、国产大模型或疑似第三方中转，并比对项目已有的端点黑名单。分类和命中结果只作为诊断信号，不保证隐私、服务政策或账号安全。

**综合结论** -- 报告末尾单独成块，汇总各项给出一句话定论：当前环境 Claude 使用为「低 / 中 / 高风险」。建议启动 Claude 前先看这一行。

**本地环境检测（`--local`）** -- 面向换号场景的本地残留自检，纯本地只读、零上传。自动收集本机账号身份标识：以 `~/.claude.json` 为当前账号基准，从 `~/.claude/backups/` 滚动备份和 `~/.claude/telemetry/` 重发队列中识别历史账号残留；同时检查 shell 配置中的 `claude`/`anthropic` 行、Chrome Cookies（仅 macOS）、钥匙串 OAuth 凭证（仅 macOS）、运行中的 claude 进程、`claude` 命令与 npm 全局包、安装残留目录。`--deep` 额外以历史账号邮箱/UUID 为线索深扫会话记录文本残留。检测到的历史残留判为高危（fail），退出码为 1。非 macOS 平台上 macOS 专属项显示为「未覆盖」。

**换号就绪度（`--full`）** -- 网络与本地都跑完后单独成块给出合并结论。本地高危残留或网络高风险会触发不建议换号；程序的「可以安全换号」仅表示现有检查未发现标记问题，不是平台风控或账号安全保证。

## License

[MIT](LICENSE) © 2026 stormzhang
