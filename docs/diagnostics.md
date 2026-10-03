# 诊断字段与实现手册

本文件维护当前实现的字段、风险判定和附属历史工具。执行边界见 [AGENTS.md](../AGENTS.md)，进度与验证范围见 [ROADMAP.md](../ROADMAP.md)。涉及第三方客户端版本的判断仅对应已有审计快照，不代表当前外部服务状态。

## 面板结构与字段语义（当前实现，改字段前必读）

入口：`ipcheck` / `python -m ipcheck` → `ipcheck.cli:main`。`main()` 先 `fit_width()`（按终端宽度自适应值列），再按分区渲染，各块用 `tbl_sep()` 分隔。

**以下字段语义容易记错，改前务必核对（尤其带 ★ 的）：**

### ① 本机网络
| 字段 | 含义 | 函数 |
|------|------|------|
| ★ **本机真实 IP** | **真实公网 IP，不是内网地址！** 请求**国内直连回显服务**（`ip.3322.net`，兜底 `4.ipw.cn` / `myip.ipip.net`）拿到----规则代理下国内 IP 走直连、绕过 VPN，露出真实 ISP 出口。探测失败显示「探测失败（无国内直连）」 | `get_real_public_ip()` |
| IPv6 地址 | 已禁用(绿) / 泄露则显示地址(黄) + 续行「建议禁用」 | `get_ipv6()` |
| 本地 DNS | 用户 DNS。**macOS 优先取手动设置的 DNS**（`networksetup -getdnsservers` 遍历服务），避免被 Tailscale/VPN 顶掉 `/etc/resolv.conf` 而漏掉；无手动设置才回退 resolv.conf/scutil | `get_dns_servers()` / `_macos_manual_dns()` |

### ② 公网信息
| 字段 | 含义 |
|------|------|
| ★ **出口 IP** | **VPN 代理出口**（`ip-api.com` 走代理返回的，也是 Anthropic 看到的 IP）----和「本机真实 IP」是**对立的两个 IP**：本机真实 IP = 你真实身份，出口 IP = 伪装后的对外出口 |
| 国家/省份、城市、运营商、IP 归属 | 出口 IP 的 ip-api 归属（isp→运营商，org→IP 归属） |
| 所处时区 | 出口 IP 的 IANA 时区 |

### ③ 代理检测
- 环境变量代理 / 系统代理 / TUN·VPN：**只显示开关状态**（不显示地址/网卡细节）。★ **颜色语义:开启/设置=绿（好，走代理），未开启/未设置=黄（提醒）**----三项统一
- 机房/住宅（`机房 IP` / `住宅 IP`，来自 ip-api `hosting`）
- IP 风险查询（proxycheck.io）：分数（`risk_color`：<30 绿 / <70 黄 / ≥70 红）+ 类型 + 「已标记为代理」（proxycheck `proxy`，**黄色**）。★ **「是不是代理」只在此一处说**----避免再增加重复的代理标记行
- 垃圾滥用记录（stopforumspam）
- IP 风险查询 + 垃圾滥用记录：**仅 ip-api `proxy`/`hosting` 命中时才查**

### ④ 时区（全部 IANA 名）
- ★ **系统时区**：`get_system_tz()` 读 `/etc/localtime`，机器真实系统时区（**不受 $TZ 影响**）
- ★ **CLI 时区**：`get_cli_tz_name()`，$TZ 优先，没设则读**系统 IANA 时区**（不用 CST 这种有歧义的缩写）
- ★ **时区一致性拆两条**（`_tz_match()` 先比 IANA 名、名不同再比 UTC offset；`_tz_verdict()` 上色）：
  - **CC CLI**：CLI 时区 vs 出口 IP 时区（CC CLI 认 $TZ）
  - **桌面版**：系统时区 vs 出口 IP 时区（桌面版 GUI 不认 $TZ、走系统时区）
  - 综合结论（`has_bad`）以 **CC CLI**（`cli_m`）为准----本工具主要面向 CC CLI 用户
- 关键认知：CC 读时区用 `Intl...timeZone`，**认 $TZ**；「系统时区 ≠ CLI 时区」= 靠 $TZ 伪装；桌面版则会暴露系统时区

### ⑤ Claude 检测（CLI，桌面版另说，见下）
- `get_claude_base_url()` 读 shell env + `~/.claude/settings.json` 的 `ANTHROPIC_BASE_URL`（桌面版不共享此变量，会用自己的 apiHost 覆盖）
- ★ **四态**：未设 base url 时先经 `claude_installed()`（查 `~/.claude` 目录 / `~/.claude.json` / `which claude` 任一）分岔----**装了**→官方直连(绿，未设 ANTHROPIC_BASE_URL)；**没装**→「未检测到 Claude Code」(黄，避免对没装 CC 的用户误报「官方直连」)。已设 base url：官方域名→官方直连(绿) / `is_domestic_model()` 命中→**国产大模型**（绿，按端点分类；不保证账号或服务端风险） / 其余→**中转**(红，「疑似中转，注意数据泄露风险」)
- 中转时加「Anthropic 147 黑名单」行：`blacklist_hit()` 精确/后缀匹配 `_BLACKLIST_147`（**冻结快照**，来源 `../../fuxi/raw/2026-06-30-cc-反蒸馏水印审计/域名黑名单-147项.md`；水印已于 CC 2.1.198 移除，二进制里已无此名单，故硬编码）；★ **命中即置 `blacklist_matched=True`，进 `has_bad` 拉高综合结论为高风险**，并在「结论和建议」补一条红字

### ⑥ 检测建议 + 综合结论（★ 各自成块，`tbl_sep()` 分隔，综合结论单独一块）
- **检测建议**（label 为「检测建议」）：只列非绿色（可优化）项，绿色正常项**不提**；全绿显示「各项正常，暂无可优化项」
- **综合结论**：`当前环境 Claude 使用高/中/低风险`（★ **三档**，`if has_bad → elif has_mid → else`，高优先于中）
- ★ `has_bad`（**高**风险，红）：IP 风险分≥70 / **中转命中 147 黑名单**（`blacklist_matched`）
- ★ `has_mid`（**中**风险，黄，仅 `not has_bad` 时才判）：CLI 时区不一致（`tz_matched is False`）/ 出口节点有投诉（`spam_listed`，来自 `get_stopforumspam()` 第二返回值 `appears`）/ 没开 TUN（`tun_active is not True`，含无法检测）。任一命中即中风险
- ★ IPv6 泄露**不进综合结论**（仍在「检测建议」里红字提示）；时区不一致属于 has_mid（中风险）
- 代理覆盖建议：TUN 开 或 (环境变量+系统代理都设) → 绿；否则 → 黄提示「代理可能不完整」

### ⑦ 本地环境检测（local.py，`--local` / `--full`）

移植自 claude-check 项目（换号防关联），纯标准库、只读、零上传。核心：`LocalChecker`（`home` 可注入便于测试）+ `run_local_checks(deep, reveal, home)`，结果存 `rep.results`（`Result(status, label, summary, details)`，status ∈ pass/warn/fail/info），`rep.has_fail` / `rep.has_warn` 汇总。渲染在 cli.py `render_local_report()`（用 tbl_* 表格，pass 绿 / warn 黄「! 」/ fail 红「! 」/ info 灰）。

- **CLI 分派**（`main()`，简单 flag 解析非 argparse）：默认只跑网络 `run_network_report()`（返回 `{"has_bad", "has_mid"}`）；`--local` 只跑本地；`--full` 都跑 + `render_switch_verdict()` 合并「换号就绪度」（本地 fail 或网络 has_bad → 不建议换号；有 warn/has_mid → 可先处理；全绿 → 可安全换号）；`--deep` / `--reveal` 透传本地模块且单独用时隐含 `--local`；`--help` / `--version`。**退出码：本地有 fail → 1**，否则 0（`__main__.py` 用 `raise SystemExit(main())`）
- **账号标识收集**：`~/.claude.json`（当前基准，is_current）+ `backups/*.claude.json.backup*`（历史）+ `telemetry/*.json` JSONL（`extract_identity_fields()` 递归提取 account_uuid/organization_uuid/device_id/email）；按 accountUuid 优先、邮箱兜底合并（`add_identity()`，sources 并集、is_current 黏性）；汇总：历史残留→fail / 仅当前→info / 无→pass
- **检测项**：telemetry 重发队列(fail) / 滚动备份(fail) / .claude 顶层三类分类(info) / shell 配置 claude|anthropic 行(warn) / Chrome Cookies(macOS 限定，sqlite3 immutable=1 只读) / 钥匙串 `Claude Code-credentials`(macOS 限定，fail) / ps 进程(排除自身和 claude-check 字样) / `which claude`(fail) / npm 全局包 / 安装残留目录
- **跨平台降级**：macOS 专属项（钥匙串、Chrome Cookies）非 macOS → info「未覆盖」；ps 进程检测 Windows → info「未覆盖」；文件类检测跨平台
- **深度扫描**（`--deep`）：`deep_needles()` = 历史身份的邮箱/UUID + 非当前 device_id，扫 `~/.claude/projects/*/*.jsonl` + `history.jsonl`（单文件 10MB 上限），命中→warn
- **打码**：`mask()` ≤10 位留前 2 + `•••`，否则前 5…后 4；`--reveal` 明文

## 附属工具：claude_expose_check.py

> **状态（2026-07-01）**：反蒸馏水印已在 Claude Code 2.1.198 从二进制移除，「反蒸馏水印自检」与「文本反向检测」两块**基本失效、仅留历史参照**。本文件**暂不删除、也不并入面板**，主要为保留三块之后可能有用的逻辑（面板目前不含）：**遥测状态 / 服务端可见参数 / 敏感信息暴露**。之后要用再决定搬进面板或保留独立脚本。

仓库根目录的**独立脚本**（纯标准库、不联网、不加依赖），检测本机用 Claude Code 时的暴露风险。与 `ipcheck` 主命令暂**未合并**。五个模块（前四个进完整自检，文本检测独立）：

1. **反蒸馏水印自检**：复刻 `Crt`/`Zup`/`edp` 逻辑，判当前 `ANTHROPIC_BASE_URL` 会不会被打指纹、落在 8 态哪一种。先经 `binary_watermark_status()` 判水印在不在（present / **removed** / obfuscation-changed）----**水印已于 2.1.198 从二进制移除**，此版本报「客户端水印已移除」，但明确提示这不等于服务端不标记你。
2. **敏感信息暴露**：走非官方端点时，列出会经过第三方的 system prompt 字段（工作目录绝对路径、git 改动、AGENTS.md／CLAUDE.md、邮箱、系统信息）。
3. **遥测状态**：复刻 `VAs()` 三级判定（`essential-traffic` / `no-telemetry` / `default`），读 shell env + `~/.claude/settings.json` env 块，报当前档位与建议。
4. **服务端可见参数**（`--server-params` 可独立跑）：列出「删了客户端水印也删不掉」的识别信息----账号 UUID / 组织 UUID / 设备 ID(`userID`) / 机器 ID(`machineID`) / 邮箱（读 `~/.claude.json`，脱敏显示）+ 请求头（`User-Agent=claude-cli/版本`、`x-app`、`X-Claude-Code-Session-Id`、`X-Stainless-OS/-Arch/-Runtime`、`anthropic-version/-beta`）+ 请求体（`metadata.user_id` 由 account_uuid + device_id + session_id 派生、`system`/`messages`/`tools`）。
5. **文本反向检测**（`--scan-*` 独立）：给一段文本，扫四种水印撇号（U+2019 / U+02BC / U+02B9）+ 斜杠日期，反解 known/labKw/cnTZ。

关键设计：
- **水印存在性检测**：`binary_watermark_status()` 用三特征（时区判断代码 / 撇号表 / XOR 解码器）判水印在不在，区分 present / removed / obfuscation-changed；避免在已移除的版本上误报。
- **名单实时从二进制解码**：按「解码器指纹」（`String.fromCharCode(r^常量) + split(",")`）定位，present 时现解 147 域名 + 11 关键词，跨版本不失效（混淆变量名每次构建都变，指纹不变）。
- **诚实边界（贯穿全工具）**：只审「客户端会装配/计算什么」；服务端如何处理（是否用账号 / IP / 请求头 / 流量模式打标记、是否投毒）是黑盒，本工具看不到----**客户端水印移除 ≠ 服务端不再识别你**。100% 坐实需抓包。
- 二进制定位：`~/.local/share/claude/versions/` 取最高版本，兜底解析 `which claude` 软链。

## 开发命令

```bash
# 安装到当前环境（可编辑模式）
pip install -e .

# 运行
ipcheck
python -m ipcheck
ipcheck --local          # 本地环境检测（换号防关联，纯本地只读）
ipcheck --full           # 网络 + 本地 + 换号就绪度合并结论
ipcheck --deep           # 本地检测 + 深扫会话记录（隐含 --local）
ipcheck --reveal         # 敏感值明文（隐含 --local）

# Claude Code 暴露自检（附属独立脚本，纯标准库，不联网）
python3 claude_expose_check.py                      # 完整自检（水印 + 暴露 + 遥测 + 服务端参数）
python3 claude_expose_check.py --server-params      # 只看服务端可见参数（独立）
python3 claude_expose_check.py --dump-data          # 导出检测数据快照（147 域名 + 11 关键词 + 8 态）
python3 claude_expose_check.py --scan-file out.txt  # 文本反向检测水印
echo "Today’s date is 2026/07/01." | python3 claude_expose_check.py --scan-stdin

# 测试
PYTHONPATH=src python -m unittest discover -s tests

# 构建
python -m build

# 发布（红线操作，必须先确认）
twine upload dist/*
```
