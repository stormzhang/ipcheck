# ip-check ROADMAP

## 规范迁移记录

- 2026-08-30 03:21 已完成：项目级约束统一以 `AGENTS.md` 为唯一内容源，`CLAUDE.md` 收敛为 AgentSync 兼容入口。
- 2026-08-30 03:21 最近验证：兼容入口内容、目标文件存在性和 `AGENTS.md` 16KB 预算检查通过。

> 本项目真实进度源,记录当前阶段、已完成、进行中、待办、阻塞、最近验证。
> 最后更新:2026-07-29

## 当前阶段

已发布的命令行工具 `ipcheck`(PyPI 包名 `ai-ipcheck`),**已发布版本 0.3.1**(2026-07-02,PyPI 已上线 https://pypi.org/project/ai-ipcheck/0.3.1/ )。**2026-07-29 新完成(已提交 f76bb73,未发版):移植 claude-check 项目的本地环境检测为 `src/ipcheck/local.py` 子模块**,新增 `ipcheck --local`(只跑本地)/ `--full`(网络+本地+换号就绪度合并结论)/ `--deep` / `--reveal`,本地有 fail 时退出码 1;配套 `tests/test_local.py`(20 项)与中英 README、CLAUDE.md ⑦ 更新。版本号未动,是否随下次发版升号(建议 0.4.0)待主人确认。此前阶段:0.3.0 起主面板大改,0.3.1 补代理开关式展示 + 「检测建议」改名 + 综合结论单独成块;**0.3.1 的版本号 commit、ROADMAP 记录及本次 local 模块 commit 均尚未 push 到 origin**。附属独立脚本 `claude_expose_check.py` 仍暂保留(不删不并入),详见其文件头与 CLAUDE.md。字段语义以 `AGENTS.md`「面板结构与字段语义」为准。

## 已实现能力与既有记录

- **本地环境检测模块 `src/ipcheck/local.py`**(2026-07-29,移植自 claude-check 项目的 `claude-check.py`,纯标准库):账号标识自动收集(`~/.claude.json` 当前基准 + backups 滚动备份历史账号 + telemetry JSONL 递归提取,按 accountUuid/邮箱合并,历史残留→fail)、.claude 目录三类分类、shell 配置 claude|anthropic 行、Chrome Cookies(macOS,sqlite3 immutable=1)、钥匙串 OAuth 凭证(macOS)、ps 进程、`which claude`、npm 全局包、安装残留目录、`--deep` 深扫会话记录(历史邮箱/UUID 为 needle,10MB 上限)、`--reveal` 明文切换(默认 `mask()` 打码)。渲染复用 cli.py tbl_* 表格风格;跨平台降级:macOS 专属项在非 macOS 报 info「未覆盖」
- **CLI 新参数 + 退出码**(2026-07-29):`ipcheck --local`(只本地)/ `--full`(网络+本地,末尾「换号就绪度」合并结论:本地 fail 或网络高风险→不建议换号)/ `--deep` / `--reveal`(单独用时隐含 --local)/ `--help`;本地有 fail 退出码 1,`__main__.py` 改 `raise SystemExit(main())`;`cli.py` 拆出 `run_network_report()` 返回 has_bad/has_mid 信号
- **`tests/test_local.py`**(2026-07-29,20 项):mask、递归提取、标识合并(sources 并集/is_current 黏性/邮箱兜底 key)、历史/当前判定、遥测队列 JSONL 解析、deep_needles 选择、深扫命中/跳过/干净、shell 配置三态;全部用 TemporaryDirectory 注入 home,不碰真实 home、不触发 subprocess 检测项
- 单模块 CLI 工具落地:`src/ipcheck/cli.py` 集中全部逻辑,`pyproject.toml` 注册 `ipcheck` 命令,`python -m ipcheck` 入口可用
- 已发布到 PyPI:包名 `ai-ipcheck`(`ipcheck` 被占),CLI 命令名 `ipcheck`,版本号到 **0.3.1**
- 公网信息检测:经 ip-api.com 获取出口 IP、国家/省份/城市、ISP、组织、代理/托管标记、公网时区
- 本机网络检测:局域网 IPv4、IPv6 可用性、DNS 服务器(Windows/Unix/macOS 分别处理)及 DNS 服务商标注
- 代理检测:环境变量代理(HTTP_PROXY/HTTPS_PROXY/ALL_PROXY)、macOS 系统代理(scutil)、TUN/VPN 启发式判断
- IP 风险检测:仅当 proxy/hosting 命中时调用 proxycheck.io 查风险分数 + stopforumspam.org 查滥用记录
- 时区一致性检测:CLI 时区($TZ/系统)与公网 IP 的 IANA 时区比对,生成分项结论与综合结论
- `ipcheck --version` 命令
- 跨平台支持(macOS / Linux / Windows)与中英双语 README
- 风险颜色分级调整:DNS 国内服务商、IP 标记为代理、机房托管降为黄色提醒(不再触发综合高风险);风险分查询保持 <30 绿/<70 黄/≥70 红
- **Claude Code 暴露自检脚本 `claude_expose_check.py`**(2026-07-01,独立脚本,纯标准库):四模块----① 反蒸馏水印自检(复刻 `Crt`/`Zup`/`edp`,判 8 态)② 敏感信息暴露(非官方端点时列出经过第三方的 system prompt 字段)③ 遥测状态(复刻 `VAs()` 三级判定)④ 文本反向检测(扫四种水印撇号 + 斜杠日期)。名单从 Claude Code 二进制按解码器指纹**实时解码**(147 域名 + 11 关键词),跨版本不失效。四模块本地实测均通过
- **水印存在性检测 + `--dump-data` 导出**(2026-07-01):`binary_watermark_status()` 用三特征(时区判断代码 / 撇号表 / XOR 解码器)判定二进制里水印在不在,区分 present / removed / obfuscation-changed;`--dump-data` 导出当前二进制的检测数据快照(147 域名 + 11 关键词 + 9 家公司映射 + 8 态参照,带时间戳)。已在 2.1.197(有水印,正常导出)与 2.1.198(已移除,正确识别)双版本验证
- **第 5 模块「服务端可见参数」`--server-params`**(2026-07-01,可独立跑,也进完整自检):列出「删了客户端水印也删不掉」的识别信息----身份标识(账号 UUID / 组织 UUID / `userID` / `machineID` / 邮箱,读 `~/.claude.json` 脱敏显示)+ 请求头(`User-Agent=claude-cli/版本`、`x-app`、`X-Claude-Code-Session-Id`、`X-Stainless-OS/-Arch/-Runtime`、`anthropic-version/-beta`)+ 请求体(`metadata.user_id` 由 account_uuid + device_id + session_id 派生、`system`/`messages`/`tools`)。基于对 2.1.198 二进制请求构造的静态审计;OS/架构从本机 `platform` 读真实值

## 进行中

- 待确认 -- 未发现明确「写完代码但未验证」的半成品;最近 commit `5e687b5 Add system proxy and TUN detection` 已是完整功能提交

## 待办

- **远端 442bac4 的更完善渲染未采用**（可后续捞回）：6/14 在另一台机器开发的「表格渲染按终端宽度自适应」含 plain 极窄退化（`<43` 列退化无边框列表，不破版）+ 6 个 `AnsiWrapTests` 单测；本次因与本地 `fit_width` 功能重复，按决策 `merge -s ours` 保留本地版、丢弃该 commit 内容。如需极窄终端不破版保护或补渲染单测，可从 `442bac4` 手动捞回（注意 `COL_LABEL` 需保持 20 以适配「Anthropic 147 黑名单」长 label）
- `claude_expose_check.py` 是否并入主命令做 `ipcheck claude` 子命令(复用 `tbl_*` 表格渲染 + `IS_WIN` 跨平台逻辑);若并入需同步升版本、改 README。待主人确认方向
- `claude_expose_check.py` 二级兜底:当前二进制找不到时回退名单仅 `["cn"]`(域名匹配退化);可选嵌入 147+11 快照做兜底,便于「没装 Claude 也能用」,代价是快照会随版本过期。待主人确认是否需要
- 完善 MCP 使用体验与公开文档(来自 projects.json 的 nextStep;注意:MCP server 已于 commit 16bf65f 移除并重构为纯 CLI,此 nextStep 与当前代码状态不一致,需主人确认方向是否仍要 MCP)
- ~~测试现状待澄清~~(2026-07-29 已澄清):`tests/` 真实存在且能跑通----`test_cli.py` 7 项 + 新增 `test_local.py` 20 项,`PYTHONPATH=src python3 -m unittest discover -s tests` 27 项全过;CLAUDE.md 原「无测试」表述已同步修正
- 非 macOS 平台的系统代理检测(当前 `get_system_proxy()` 仅 macOS 实现,其他平台不判断)

## 阻塞

- 无

## 既有验证与验收缺口

- 2026-07-29 -- **本地环境检测模块落地(未提交/未发版)**:`PYTHONPATH=src python3 -m unittest discover -s tests` 27 项全过(原 7 项 + 新 20 项);本机 macOS 实跑 `python3 -m ipcheck --local` 正常出报告、退出码 1(真实 fail:5 个滚动备份 + 历史账号标识、claude 命令仍存在;warn:27 条 Chrome cookie、2 个 claude 进程、2 处安装残留);`--full` 网络+本地+换号就绪度合并结论(判「不建议换号」)正常;默认 `ipcheck` 网络面板输出不变、退出码 0;`--deep` 实跑扫描 7 个会话文件未发现历史标识;`--help` / `--version` 正常。字段语义已写入 CLAUDE.md ⑦
- 2026-07-02 -- **发布 0.3.1 到 PyPI**：版本号两处同步升 0.3.0 → 0.3.1，`python -m build` 打包 + `twine check` 双产物 PASSED + `twine upload` 上线，`/pypi/ai-ipcheck/0.3.1/json` 返回 HTTP 200、版本 0.3.1、whl + tar.gz 均在。本次含环境变量代理开关式展示、「检测建议」改名 + 综合结论单独成块。https://pypi.org/project/ai-ipcheck/0.3.1/ 。git 已提交至版本号 commit，**尚未 push 到 origin**
- 2026-07-02 -- **「结论和建议」改名「检测建议」+ 综合结论单独成块**：label 由「结论和建议」改为「检测建议」；在检测建议与综合结论之间加 `tbl_sep()`，综合结论单独成一块（原为同块无分隔线）。mock 面板验证通过。字段语义已同步 CLAUDE.md ⑥
- 2026-07-02 -- **环境变量代理展示统一**：原逻辑设了代理就逐行列出变量名 + 代理地址（暴露 `127.0.0.1:7890` 端口，且与系统代理/TUN 的开关式展示不一致），改为固定一行「已设置（绿）/ 未设置（黄）」，三个代理项展示彻底统一、不再泄露地址，兑现 CLAUDE.md ③「只显示开关状态、三项统一」。mock 面板验证通过
- 2026-07-02 -- **合并远端 442bac4 并推送**：push 时发现远端有一个本地缺失的 commit（`442bac4` 表格渲染自适应，与本地 `fit_width` 功能重复、分叉冲突）。按决策 `git merge -s ours origin/main` 保留本地渲染、丢弃远端 plain 模式与单测，校验 merge 后 `cli.py` 仍为本地版（COL_LABEL=20 / fit_width）、`tests` 无 AnsiWrapTests，随后 push origin/main。远端渲染增强转入「待办」备查
- 2026-07-01 -- **发布 0.3.0 到 PyPI**：版本号两处（`__init__.py` / `pyproject.toml`）同步升 0.2.1 → 0.3.0，`python -m build` 打包 + `twine check` 双产物 PASSED + `twine upload` 上线 https://pypi.org/project/ai-ipcheck/0.3.0/ 。本次为累积发布（含面板大改、Claude 检测块、综合结论三档风险、`claude_expose_check` 暴露自检脚本）。git 已提交至版本号 commit `811f136`，**尚未 push 到 origin**（本地领先 origin/main 6 个 commit）
- 2026-07-01 -- **综合结论改三档 高/中/低（本地跑通，未提交）**：在原「高/低」之间加**中风险**。`has_bad`（高，红）= IP 风险分≥70 / 命中黑名单；`has_mid`（中，黄，仅非高时判）= CLI 时区不一致 / 出口节点有投诉 / 没开 TUN，任一命中即中风险；都不命中才低风险。为拿「出口节点有投诉」信号，`get_stopforumspam()` 加第二返回值 `appears`（bool）。实测：时区不一致→中风险（黄）；命中黑名单+时区不一致→高风险（高优先于中）；当前环境（TUN 开、时区一致、未收录、66 分）→低风险。字段语义已同步 `CLAUDE.md` ⑥
- 2026-07-01 -- **`has_bad` 收窄为两条（本地跑通，未提交）**：移出「IPv6 泄露」「时区不一致」，综合高风险**只认** IP 风险分≥70 / 中转命中 147 黑名单。IPv6 与时区仍在「结论和建议」里红字提示，但不再拉高综合结论。实测当前环境（66 分中风险、无黑名单、时区一致）综合结论判低风险
- 2026-07-01 -- **Claude 检测块增强（本地跑通，未提交）**：① **中转命中 147 黑名单 → 综合结论直接高风险**----Claude 检测块新增 `blacklist_matched` 标志，命中时并入 `has_bad`，并在「结论和建议」补一条红字「中转端点命中 Anthropic 黑名单，封号风险高」；② **没装 CC 不再误报**----新增 `claude_installed()`（查 `~/.claude` 目录 / `~/.claude.json` / `which claude` 任一），未设 base url 时先分岔：装了→绿「官方直连（未设 ANTHROPIC_BASE_URL）」，没装→黄「未检测到 Claude Code（未安装或未使用）」。实测：场景 A（`ANTHROPIC_BASE_URL=api.88code.ai`）正确命中黑名单红字告警 + 综合结论高风险；`claude_installed()` mock 三分支（配置目录 / which / 全不命中）判定均正确。字段语义已同步 `CLAUDE.md` ⑤⑥
- 2026-07-01 -- **面板微调 + 代码优化（本地跑通，未提交）**：① 三个代理项颜色语义统一----开启/设置=绿、未开启/未设置=黄；② 删掉「IP 标记为代理」行（与 IP 风险查询里 proxycheck 的「已标记为代理」重复），「已标记为代理」改黄；③ **时区一致性拆两条**----CC CLI（比 CLI 时区 vs 出口，认 $TZ）+ 桌面版（比系统时区 vs 出口，不认 $TZ），综合结论以 CC CLI 为准；④ 代码优化：抽 `tz_display()` 去重时区格式化、`get_dns_servers()` 改末尾统一 `dict.fromkeys` 去重、新增 `_tz_match()`/`_tz_verdict()`。字段语义已同步 `CLAUDE.md`
- 2026-07-01 -- **`ipcheck` 主面板大改（本地已跑通，未提交/未发版）**：字段语义重构----「本机真实 IP」= 国内直连回显（ip.3322.net）拿到的**真实公网 IP**（不是内网地址，此前一度误做成本地网卡枚举，已纠正）；「公网 IP」→「出口 IP」（VPN 代理出口）；新增「系统时区」+ CLI 时区全 IANA 化（读 /etc/localtime）；本地 DNS macOS 改优先 `networksetup` 手动 DNS；「机房/托管」→「机房/住宅」（机房 IP / 住宅 IP）；新增「Claude 检测」块（CLI 端点三态：官方直连 / 国产大模型无风险 / 中转红色告警 + Anthropic 147 黑名单命中，名单为冻结快照）；「结论分析+综合结论」合并为「结论和建议」（只列可优化项）+ 保留综合结论行。渲染层：**去 `✓✗⚠` 歧义宽度符号修右边框错位**、`fit_width()` 终端自适应、`_wrap_ansi()` 长值折行。字段语义已写入 `CLAUDE.md`「面板结构与字段语义」防再记错
- 2026-07-01 -- **重大发现:反蒸馏水印在 Claude Code 2.1.198 被移除**。逐版本核对二进制:2.1.196(6/29)、2.1.197(6/30)`currentDate:eca(KSe())` 水印函数仍在;2.1.198(7/01 18:04)`currentDate` 变回普通模板 `` `Today's date is ${ybe()}.` ``,`Asia/Shanghai`/`Asia/Urumqi`/四撇号/`replaceAll("-","/")`/XOR 解码器/labKw/cnTZ **全部归零**。公开曝光在 6/30,移除在 7/1,间隔约一天----Anthropic 未发声明,以代码静默移除作回应。`claude_expose_check.py` 已能正确识别 198 为「已移除」
- 2026-07-01 -- 第 5 模块「服务端可见参数」本地实测:`--server-params` 独立跑通,读 `~/.claude.json` 正确取出账号 UUID / 组织 UUID / userID / machineID / 邮箱并脱敏显示,`X-Stainless-OS/-Arch` 从本机读出 MacOS/arm64,`User-Agent` 取到 claude-cli/2.1.198;也已并进完整自检末尾。请求参数清单来自对 2.1.198 二进制的静态审计(确认 `metadata.user_id` 由 account_uuid+device_id+session_id 派生、自定义头 x-app/X-Claude-Code-Session-Id、X-Stainless-* 存在);**更正**早前 2.1.196 审计「x-stainless 全 0、未暴露 OS/架构」的结论----2.1.198 里这些头确实存在(强证据,待抓包终证)
- 2026-07-01 -- `claude_expose_check.py` 四模块本地实测通过:① 水印自检覆盖官方直连(不触发)+ 三种号商场景(`anyrouter.top` 命中 known+cnTZ → U+2019、`deepseek` 端点命中 labKw、`localhost`+香港时区演示绕过与港澳台排除)② 号商场景正确列出经过第三方的字段并告警 ③ 遥测正确识别本机 `essential-traffic` 最严档 ④ 文本反向检测三种输入(满水印/仅斜杠/干净)均判对。名单从本机 2.1.197 二进制实时解出 `XOR=91、解码器 Zla()、147 域名 + 11 关键词`
- 2026-06-01 -- 版本号升至 0.2.1 并打包发布到 PyPI(https://pypi.org/project/ai-ipcheck/0.2.1/);`twine check` 通过,whl + sdist 上传成功
- 2026-06-01 -- 风险颜色分级调整后运行 `PYTHONPATH=src python -m ipcheck` 验证通过:IP 标记为代理/机房托管显示黄色 `是 !`,风险分 66/100 显示黄色中风险,综合结论因无红线项判绿色低风险;DNS 国内服务商黄色因当前环境为 Cloudflare/Google 未触发实测,仅代码逻辑核对
- 待确认 -- 仓库内无测试运行日志或验证记录文件;此前代码改动为 git 提交 `5e687b5 Add system proxy and TUN detection`(README/CLAUDE.md mtime 2026-05-22),README 附有运行截图 `screenshot.png` 可作为命令可运行的间接证据,但无对应的逐项验证记录

## 最近完成

- 2026-10-03 10:01 项目规范保留工程与数据边界，面板和诊断细节移入 docs/diagnostics.md；中英文 README 取消账号安全保证。本次仅修改规范、必要文档与指令模板，未重新执行历史业务验收。入口：[项目规范](AGENTS.md)。

## 最近验证


- 2026-10-03 10:08 文档检查：本项目受影响规范、文档引用、代码围栏、最近记录数量与时间顺序、Git diff 空白检查通过；全工作区 AGENTS 预算及 CLAUDE 兼容入口检查通过。入口：[项目规范](AGENTS.md)。本次未重跑历史业务验收。Markdown 渲染解析通过，Mermaid 示例未产生注释包裹的图源码。
