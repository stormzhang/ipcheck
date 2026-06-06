# Changelog

本项目所有重要改动记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 背景

原版的 DNS 检测是**读本机配置的 DNS IP（`/etc/resolv.conf` / `scutil`），跟硬编码的
公共 DNS 国别表（`KNOWN_DNS`）比对**，命中 `(CN)` 即报泄露。两个硬伤：

- **误报**：代理用 fake-ip + 分域名 DoH 时，配置里的 `223.5.5.5` 并不参与实际解析，却照样报泄露。
- **漏报**：只认识表里的少数中国 IP，其他中国 DNS 或本地代理监听地址一律放过。

核心问题——它测的是「配置」，不是「DNS 真实从哪出口」。本次改动改用**真实出口测试 + 路由探针**。

### Added

- **真实 DNS 出口测试**（bash.ws 随机子域名法）。解析多个唯一子域名，强制递归 resolver
  访问权威服务器（不命中缓存），由权威端记录实际发起解析的 resolver IP / 国家 / ASN。
  - 直连路径（`socket` 系统解析）与代理路径（经代理远端解析）**分开测**，分别反映
    系统级解析与 Claude 实际走的路径。
  - 异常 / 超时 / 无数据时**自动换新 id 重试**（默认 2 次）。
  - 相关常量：`LEAK_LOOKUP_COUNT=6`、`LEAK_HTTP_TIMEOUT=5`、`LEAK_RETRIES=2`。
- **AI 域名隧道探针**。解析主流国外 AI 域名（anthropic / openai / gemini / x.ai /
  perplexity / huggingface 等共 10 个），测「解析到的 IP 是否经代理隧道可达」。
  - 测路由可达性（连接往哪走），与 DNS 出口测试（解析从哪走）互补。
  - 若本地 DNS 被污染、解析出假 IP，隧道 `CONNECT` 会失败，可顺带暴露污染。
- **`--no-leak` / `--fast` 参数**：跳过上述两项网络重测试，回退到静态判断（更快）。
- 表格新增行：`通用DNS出口(直连)`、`通用DNS出口(代理)`、`DNS 出口一致性`、`AI 域名隧道探针`。
- 底部脚注，说明「通用DNS出口」测的是非白名单域名、Claude 等白名单 AI 域名以隧道探针为准。

### Changed

- **结论拆成两段**：`Claude 专用风险`（IPv6 / 出口 IP 信誉 / 时区一致性 / Claude 核心域名
  隧道可达性）与 `通用环境风险`（通用 DNS 出口、其他 AI 域名可达性），各判各的，
  取代原先单一的「Claude 高 / 低风险」。解决了「通用 DNS 泄露却误判 Claude 高风险」的矛盾。
- 静态 `KNOWN_DNS` 表**降级**为辅助标注 + 未做出口测试时的回退依据，不再作为主判据。
- DNS 风险判定改为基于**国别**（`_is_cn_country`）与**出口地一致性**（`_country_eq`），
  而非硬编码 IP 命中。

### Known limitations

- 无法直接测 `anthropic.com` 自身的 DNS 出口：bash.ws 法需控制权威服务器，故用通用域名
  做代表，Claude 专属域名的可达性以隧道探针为准。
- 使用全球 anycast DoH（Cloudflare / Google）时，DNS 出口地可能与公网 IP 不同国，
  会标「留意」——属常态，非风险。
- 依赖 bash.ws 可用性；全部重试失败时该行显示「测试失败 / 超时」，不影响其他检测项。

## [0.2.1] - 此前

- 上游 stormzhang/ipcheck 既有版本（本机 IP / IPv6 / DNS 配置 / 公网信息 /
  代理状态 / IP 信誉 / 时区一致性检测）。
