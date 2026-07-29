"""ipcheck.local — Claude 本地环境检测（换号防关联）

移植自 claude-check 项目（claude-check.py），检测本机 Claude Code 账号
身份标识残留：换号前确认旧账号标识已清理干净，避免新账号被关联封号。
纯本地只读检测，零上传，纯标准库实现。

检测项：
  - 账号标识自动收集（~/.claude.json / 滚动备份 / 遥测重发队列）
  - .claude 目录分类（身份标识 / 本地存档 / 用户资产）
  - shell 配置中的 claude|anthropic 行
  - Chrome Cookies（仅 macOS，sqlite3 immutable=1 只读）
  - 系统级：钥匙串（仅 macOS）、进程、claude 命令、npm 全局包、安装残留
  - 深度扫描（可选）：以历史账号邮箱/UUID 为 needle 扫会话记录
"""

import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

SHELL_FILES = [".zshrc", ".bashrc", ".zprofile", ".bash_profile"]
IDENTITY_ITEMS = {"telemetry", "backups"}
ARCHIVE_ITEMS = {"projects", "history.jsonl", "file-history", "sessions"}
RESIDUAL_PATHS = (
    ".local/bin/claude",
    ".local/share/claude",
    "Library/Caches/claude-cli-nodejs",
)
COOKIE_DB_REL = "Library/Application Support/Google/Chrome/Default/Cookies"
KEYCHAIN_SERVICE = "Claude Code-credentials"
MAX_DEEP_BYTES = 10 * 1024 * 1024
IS_MACOS = platform.system() == "Darwin"


def mask(v, reveal=False):
    """敏感值打码：≤10 位留前 2，否则前 5…后 4；reveal 时显示明文。"""
    if not v:
        return ""
    s = str(v)
    if reveal:
        return s
    if len(s) <= 10:
        return s[:2] + "•••"
    return s[:5] + "…" + s[-4:]


def extract_identity_fields(obj, out):
    """遥测事件 JSON 结构不固定，递归提取身份字段。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                if k == "account_uuid":
                    out.setdefault("account_uuid", v)
                elif k == "organization_uuid":
                    out.setdefault("org_uuid", v)
                elif k == "device_id":
                    out.setdefault("device_id", v)
                elif k in ("emailAddress", "email"):
                    out.setdefault("email", v)
            else:
                extract_identity_fields(v, out)
    elif isinstance(obj, list):
        for item in obj:
            extract_identity_fields(item, out)


class Result:
    """单项检测结果：status ∈ pass / warn / fail / info。"""

    __slots__ = ("status", "label", "summary", "details")

    def __init__(self, status, label, summary, details=None):
        self.status = status
        self.label = label
        self.summary = summary
        self.details = details or []

    def __repr__(self):
        return f"Result({self.status!r}, {self.label!r}, {self.summary!r})"


class LocalChecker:
    """本地环境检测器。home 可注入（测试用 tmp_path），默认 Path.home()。"""

    def __init__(self, home=None, reveal=False):
        self.home = Path(home) if home else Path.home()
        self.reveal = reveal
        self.results = []
        self.identities = {}   # key -> {email, account_uuid, org_uuid, sources:set, is_current}
        self.device_ids = {}   # value -> {sources:set, is_current}

    def mask(self, v):
        return mask(v, self.reveal)

    def add(self, status, label, summary, details=None):
        self.results.append(Result(status, label, summary, details))

    @property
    def has_fail(self):
        return any(r.status == "fail" for r in self.results)

    @property
    def has_warn(self):
        return any(r.status == "warn" for r in self.results)

    # ---------- 账号标识收集 ----------

    def add_identity(self, email="", account_uuid="", org_uuid="",
                     source="", is_current=False):
        key = account_uuid or email
        if not key:
            return
        cur = self.identities.setdefault(key, {
            "email": "", "account_uuid": "", "org_uuid": "",
            "sources": set(), "is_current": False,
        })
        cur["email"] = cur["email"] or email
        cur["account_uuid"] = cur["account_uuid"] or account_uuid
        cur["org_uuid"] = cur["org_uuid"] or org_uuid
        if source:
            cur["sources"].add(source)
        if is_current:
            cur["is_current"] = True

    def add_device_id(self, value, source="", is_current=False):
        if not value:
            return
        cur = self.device_ids.setdefault(value, {"sources": set(), "is_current": False})
        if source:
            cur["sources"].add(source)
        if is_current:
            cur["is_current"] = True

    def collect_from_claude_json(self, data, source, is_current):
        oa = (data or {}).get("oauthAccount") or {}
        self.add_identity(
            email=oa.get("emailAddress", ""),
            account_uuid=oa.get("accountUuid", ""),
            org_uuid=oa.get("organizationUuid", ""),
            source=source, is_current=is_current,
        )
        self.add_device_id((data or {}).get("userID"), source, is_current)

    # ---------- 文件类检测 ----------

    def check_claude_json(self):
        path = self.home / ".claude.json"
        if not path.exists():
            self.add("pass", ".claude.json", "已清除，无身份残留")
            return
        try:
            data = json.loads(path.read_text(errors="replace"))
        except (json.JSONDecodeError, OSError):
            self.add("warn", ".claude.json", "存在但无法解析", ["文件非合法 JSON，建议人工检查"])
            return
        self.collect_from_claude_json(data, ".claude.json(当前)", True)
        oa = data.get("oauthAccount") or {}
        self.add("info", ".claude.json", "存在，当前账号已纳入比对基准", [
            f"当前 userID: {self.mask(data.get('userID')) or '(无)'}",
            f"当前登录: {self.mask(oa.get('emailAddress')) or '(未登录)'}",
            "历史账号将从滚动备份和遥测队列中识别",
        ])

    def check_claude_dir(self):
        cdir = self.home / ".claude"
        if not cdir.is_dir():
            self.add("pass", ".claude 目录", "不存在，配置与遥测均已清除")
            return

        # telemetry 重发队列 —— 最高危
        tele = cdir / "telemetry"
        tele_files = sorted(tele.glob("*.json")) if tele.is_dir() else []
        if tele_files:
            for f in tele_files:
                try:
                    for line in f.read_text(errors="replace").splitlines():
                        if not line.strip():
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        out = {}
                        extract_identity_fields(ev, out)
                        if out.get("account_uuid") or out.get("email"):
                            self.add_identity(email=out.get("email", ""),
                                              account_uuid=out.get("account_uuid", ""),
                                              org_uuid=out.get("org_uuid", ""),
                                              source="遥测重发队列")
                        self.add_device_id(out.get("device_id"), "遥测重发队列")
                except OSError:
                    pass
            self.add("fail", "遥测重发队列", f"存在（{len(tele_files)} 个文件）", [
                "事件携带旧账号 account_uuid / device_id，新账号登录后会被自动上报",
                "是关联封号的最主要通道",
                *[f".claude/telemetry/{f.name}" for f in tele_files],
            ])
        elif tele.is_dir():
            self.add("pass", "遥测重发队列", "队列为空，无待重发事件")
        else:
            self.add("pass", "遥测重发队列", "未发现 .claude/telemetry 目录")

        # backups 滚动备份 —— 易漏
        bak = cdir / "backups"
        baks = sorted(bak.glob("*.claude.json.backup*")) if bak.is_dir() else []
        if baks:
            for b in baks:
                try:
                    self.collect_from_claude_json(
                        json.loads(b.read_text(errors="replace")), "滚动备份", False)
                except (json.JSONDecodeError, OSError):
                    pass
            self.add("fail", "滚动备份", f"发现 {len(baks)} 个 .claude.json 备份", [
                "含与 .claude.json 相同的全套身份字段，是清理时最容易漏掉的位置",
                *[f".claude/backups/{b.name}" for b in baks],
            ])

        # 顶层内容分类：只有 telemetry/ 和 backups/ 携带身份标识，其余与旧账号无关
        names = []
        try:
            names = [e.name for e in cdir.iterdir()]
        except OSError:
            pass
        identity_items = sorted(n for n in names if n in IDENTITY_ITEMS)
        archive_items = sorted(n for n in names if n in ARCHIVE_ITEMS)
        asset_items = sorted(n for n in names if n not in IDENTITY_ITEMS and n not in ARCHIVE_ITEMS)
        details = ["分类（仅 telemetry/ 和 backups/ 携带身份标识，其余与旧账号无关）:"]
        if identity_items:
            details.append(f"身份标识（换号必删）: {'、'.join(identity_items)}")
        if archive_items:
            details.append(f"本地存档（无身份字段，可保留；--resume 恢复时会作为上下文发出）: {'、'.join(archive_items)}")
        if asset_items:
            details.append(f"用户资产（与账号无关，勿误删）: {'、'.join(asset_items)}")
        details.append("想保留资产的话，只删身份标识类即可，无需整个 rm -rf ~/.claude")
        self.add("info", ".claude 目录", f"存在（{len(names)} 个顶层条目）", details)

    def report_identities(self):
        historical = [i for i in self.identities.values() if not i["is_current"]]
        lines = []
        for n, i in enumerate(self.identities.values(), 1):
            tag = "(当前)" if i["is_current"] else "(历史残留)"
            lines.append(f"账号 {n}{tag} · 来源: {'、'.join(sorted(i['sources']))}")
            if i["email"]:
                lines.append(f"  邮箱: {self.mask(i['email'])}")
            if i["account_uuid"]:
                lines.append(f"  account_uuid: {self.mask(i['account_uuid'])}")
            if i["org_uuid"]:
                lines.append(f"  org_uuid: {self.mask(i['org_uuid'])}")
        if self.device_ids:
            lines.append("设备级 userID / device_id（跨账号不变，删除 .claude.json 才重置）:")
            for v, d in self.device_ids.items():
                lines.append(f"  {self.mask(v)} · 来源: {'、'.join(sorted(d['sources']))}")

        if historical:
            self.add("fail", "账号标识汇总",
                     f"检测到 {len(self.identities)} 个账号标识，其中 {len(historical)} 个为历史残留",
                     lines)
        elif self.identities:
            self.add("info", "账号标识汇总", "仅当前账号，无历史残留", lines)
        else:
            self.add("pass", "账号标识汇总", "未检测到任何账号标识",
                     ["本地各位置均未发现账号身份字段，环境干净"])

    def check_shell_configs(self):
        pat = re.compile(r"claude|anthropic", re.I)
        any_file, any_hit = False, False
        for name in SHELL_FILES:
            path = self.home / name
            if not path.is_file():
                continue
            any_file = True
            try:
                matched = [l for l in path.read_text(errors="replace").splitlines() if pat.search(l)]
            except OSError:
                continue
            if matched:
                any_hit = True
                self.add("warn", name, f"含 {len(matched)} 行 claude/anthropic 配置",
                         [l.strip() for l in matched] +
                         ["建议换号时移除（ANTHROPIC_* 环境变量、claude alias 等）"])
        if not any_file:
            self.add("info", "shell 配置", "未找到 " + " / ".join(SHELL_FILES))
        elif not any_hit:
            self.add("pass", "shell 配置", "干净，未发现 claude/anthropic 相关行")

    def deep_needles(self):
        """深度扫描的比对目标：历史账号的邮箱/UUID + 非当前设备 ID。"""
        needles = set()
        for i in self.identities.values():
            if i["is_current"]:
                continue
            needles.update(x for x in (i["email"], i["account_uuid"], i["org_uuid"]) if x)
        for v, d in self.device_ids.items():
            if not d["is_current"]:
                needles.add(v)
        return needles

    def check_deep_scan(self):
        needles = self.deep_needles()
        if not needles:
            self.add("info", "深度扫描", "跳过（未检测到历史账号标识，无比对目标）")
            return

        targets = sorted((self.home / ".claude/projects").glob("*/*.jsonl"))
        hist = self.home / ".claude/history.jsonl"
        if hist.is_file():
            targets.append(hist)

        found, scanned, skipped = [], 0, 0
        for t in targets:
            try:
                if t.stat().st_size > MAX_DEEP_BYTES:
                    skipped += 1
                    continue
                text = t.read_text(errors="replace")
            except OSError:
                continue
            scanned += 1
            rel = t.relative_to(self.home)
            for n in needles:
                if n in text:
                    found.append(f"{rel} → 含 {self.mask(n)}")
        if found:
            shown = found[:30]
            self.add("warn", "深度扫描", f"发现 {len(found)} 处历史标识文本",
                     ["以下为对话内容中的文本残留（非身份字段，不会主动上传，"
                      "但 --resume 恢复会话时会作为上下文发送）:"] +
                     shown + ([f"… 共 {len(found)} 处"] if len(found) > 30 else []))
        else:
            summary = f"扫描 {scanned} 个文件，未发现历史标识"
            if skipped:
                summary += f"（跳过 {skipped} 个超大文件）"
            self.add("pass", "深度扫描", summary)

    def check_cookies(self):
        if not IS_MACOS:
            self.add("info", "Chrome Cookies", "未覆盖（仅支持 macOS 检测路径）")
            return
        db_path = self.home / COOKIE_DB_REL
        if not db_path.is_file():
            self.add("info", "Chrome Cookies", "未找到数据库（未装 Chrome 或非 Default 配置）")
            return
        try:
            # immutable=1 以只读快照方式打开，即使 Chrome 运行中数据库被锁也能读
            db = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
            rows = db.execute(
                "SELECT host_key, count(*) FROM cookies "
                "WHERE host_key LIKE '%claude%' OR host_key LIKE '%anthropic%' GROUP BY host_key"
            ).fetchall()
            db.close()
        except sqlite3.Error as e:
            self.add("warn", "Chrome Cookies", "数据库读取失败", [f"错误: {e}"])
            return
        if rows:
            total = sum(r[1] for r in rows)
            self.add("warn", "Chrome Cookies", f"存在 {total} 条 claude/anthropic cookie",
                     [f"{r[0]}: {r[1]} 条" for r in rows] +
                     ["换号前建议清除（Chrome 设置 → 隐私 → 网站数据）"])
        else:
            self.add("pass", "Chrome Cookies", "干净，无 claude/anthropic 相关 cookie")

    # ---------- 系统级检测 ----------

    def check_keychain(self):
        if not IS_MACOS or not shutil.which("security"):
            self.add("info", "钥匙串凭证", "未覆盖（仅 macOS 钥匙串）")
            return
        p = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
                           capture_output=True, text=True)
        if p.returncode == 0:
            self.add("fail", "钥匙串凭证", "存在 OAuth 凭证（Claude Code-credentials）", [
                "新账号登录可能复用旧会话",
                "清除命令（可能有多条，执行到报 not found 为止）:",
                f'security delete-generic-password -s "{KEYCHAIN_SERVICE}"',
            ])
        else:
            self.add("pass", "钥匙串凭证", "无 OAuth 凭证残留")

    def check_processes(self):
        if platform.system() == "Windows" or not shutil.which("ps"):
            self.add("info", "claude 进程", "未覆盖（当前平台不支持 ps 检测）")
            return
        p = subprocess.run(["ps", "-axo", "pid,args"], capture_output=True, text=True)
        hits = []
        for line in p.stdout.splitlines()[1:]:
            if "claude" not in line.lower():
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            pid, args = parts
            if int(pid) == os.getpid() or "claude-check" in args:
                continue  # 排除检测脚本自身
            hits.append(f"PID {pid}: {args[:120]}")
        if hits:
            self.add("warn", "claude 进程", f"发现 {len(hits)} 个运行中的相关进程",
                     ["换号清理前建议先退出所有 Claude 进程:"] + hits)
        else:
            self.add("pass", "claude 进程", "无 claude 相关进程")

    def check_command_and_npm(self):
        claude_bin = shutil.which("claude")
        if claude_bin:
            self.add("fail", "claude 命令", f"仍存在: {claude_bin}",
                     ["换号场景下应已卸载"])
        else:
            self.add("pass", "claude 命令", "PATH 中未找到 claude 可执行文件")

        if not shutil.which("npm"):
            self.add("info", "npm 全局包", "未检测到 npm，跳过")
            return
        p = subprocess.run(["npm", "ls", "-g", "--depth=0"], capture_output=True, text=True)
        hits = [l for l in p.stdout.splitlines() if "claude" in l.lower()]
        if hits:
            self.add("warn", "npm 全局包", "仍安装着 claude 相关包",
                     [l.strip() for l in hits] + ["可用 npm uninstall -g 移除"])
        else:
            self.add("pass", "npm 全局包", "无 claude 相关包")

    def check_residual_dirs(self):
        found = [str(self.home / p) for p in RESIDUAL_PATHS if (self.home / p).exists()]
        if found:
            self.add("warn", "安装残留", f"发现 {len(found)} 处残留目录",
                     found + ["确认无用后可删除"])
        else:
            self.add("pass", "安装残留", "常见残留位置均不存在")


def run_local_checks(deep=False, reveal=False, home=None):
    """跑全部本地检测，返回 LocalChecker（结果在 .results）。"""
    c = LocalChecker(home=home, reveal=reveal)
    c.check_claude_json()
    c.check_claude_dir()
    c.report_identities()
    c.check_shell_configs()
    if deep:
        c.check_deep_scan()
    c.check_cookies()
    c.check_keychain()
    c.check_processes()
    c.check_command_and_npm()
    c.check_residual_dirs()
    return c
