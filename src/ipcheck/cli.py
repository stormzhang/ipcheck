#!/usr/bin/env python3
"""
ipcheck — 网络环境诊断工具
检测本机 IP、IPv6、DNS、公网信息、代理状态、时区
支持 macOS / Linux / Windows
"""

import socket
import ipaddress
import os
import sys
import subprocess
import datetime
import re
import json
import shutil
import platform
from urllib.parse import urlsplit

import requests

try:
    from zoneinfo import ZoneInfo as _ZI
except ImportError:
    _ZI = None

# ── 编码修正（Windows cmd 默认非 UTF-8）────────────────────
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

IS_WIN = platform.system() == 'Windows'


# ── 已知 DNS ──────────────────────────────────────────────
KNOWN_DNS = {
    '1.1.1.1':         'Cloudflare (US)',
    '1.0.0.1':         'Cloudflare (US)',
    '1.1.1.2':         'Cloudflare for Families (US)',
    '1.0.0.2':         'Cloudflare for Families (US)',
    '1.1.1.3':         'Cloudflare for Families (US)',
    '1.0.0.3':         'Cloudflare for Families (US)',
    '8.8.8.8':         'Google Public DNS (US)',
    '8.8.4.4':         'Google Public DNS (US)',
    '9.9.9.9':         'Quad9 (US)',
    '149.112.112.112': 'Quad9 (US)',
    '208.67.222.222':  'OpenDNS/Cisco (US)',
    '208.67.220.220':  'OpenDNS/Cisco (US)',
    '223.5.5.5':       'AliDNS 阿里 (CN)',
    '223.6.6.6':       'AliDNS 阿里 (CN)',
    '119.29.29.29':    'DNSPod 腾讯 (CN)',
    '182.254.116.116': 'DNSPod 腾讯 (CN)',
    '114.114.114.114': '114DNS (CN)',
    '114.114.115.115': '114DNS (CN)',
    '180.76.76.76':    'BaiduDNS 百度 (CN)',
    '1.2.4.8':         'CNNIC (CN)',
    '210.2.4.8':       'CNNIC (CN)',
    '94.140.14.14':    'AdGuard (CY)',
    '94.140.15.15':    'AdGuard (CY)',
    '185.228.168.9':   'CleanBrowsing (US)',
    '185.228.169.9':   'CleanBrowsing (US)',
    '76.76.2.0':       'Alternate DNS (US)',
    '76.76.10.0':      'Alternate DNS (US)',
}


def dns_label(ip):
    if ip in KNOWN_DNS:
        return f"{ip}  {KNOWN_DNS[ip]}"
    try:
        if ipaddress.ip_address(ip).is_private:
            return f"{ip}  局域网路由器"
    except Exception:
        pass
    return ip


def make_zone(name):
    if not _ZI or not name:
        return None
    try:
        return _ZI(name)
    except Exception:
        return None


def _val(v, fallback="未知"):
    return v if v else warn(fallback)


# ── 颜色 ─────────────────────────────────────────────────
def _init_color():
    if IS_WIN:
        try:
            import colorama
            colorama.init()
            return True
        except ImportError:
            pass
        try:
            import ctypes
            h = ctypes.windll.kernel32.GetStdHandle(-11)
            m = ctypes.c_ulong()
            ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(m))
            ctypes.windll.kernel32.SetConsoleMode(h, m.value | 0x0004)
            return True
        except Exception:
            return False
    return True

_COLOR = _init_color()


class C:
    RESET  = "\033[0m"  if _COLOR else ""
    BOLD   = "\033[1m"  if _COLOR else ""
    RED    = "\033[91m" if _COLOR else ""
    GREEN  = "\033[92m" if _COLOR else ""
    YELLOW = "\033[93m" if _COLOR else ""
    GRAY   = "\033[90m" if _COLOR else ""

ANSI_RE = re.compile(r'\033\[[0-9;]*m')


def char_width(c):
    cp = ord(c)
    if (0x2E80 <= cp <= 0x303E or 0x3040 <= cp <= 0x33FF or
        0x3400 <= cp <= 0x4DBF or 0x4E00 <= cp <= 0x9FFF or
        0xAC00 <= cp <= 0xD7AF or 0xF900 <= cp <= 0xFAFF or
        0xFE30 <= cp <= 0xFE6F or 0xFF00 <= cp <= 0xFF60 or
        0x20000 <= cp <= 0x2FFFD):
        return 2
    return 1


def display_len(s):
    return sum(char_width(c) for c in ANSI_RE.sub('', s))


def ok(v):   return f"{C.GREEN}{v}{C.RESET}"
def warn(v): return f"{C.YELLOW}{v}{C.RESET}"
def bad(v):  return f"{C.RED}{v}{C.RESET}"


def risk_color(score):
    if score < 30:
        return C.GREEN, "低风险"
    if score < 70:
        return C.YELLOW, "中风险"
    return C.RED, "高风险"


# ── 表格渲染 ──────────────────────────────────────────────
COL_LABEL, COL_VALUE = 20, 46


def fit_width():
    """按终端宽度自适应值列宽（窄终端自动收窄，避免整框折行错位）。"""
    global COL_VALUE
    try:
        cols = shutil.get_terminal_size((80, 24)).columns
    except Exception:
        cols = 80
    # 整框宽 = COL_LABEL + COL_VALUE + 9（缩进/边框/间隔）
    COL_VALUE = max(20, min(46, cols - COL_LABEL - 9))


def tbl_top(): print(f"  ╔{'═'*(COL_LABEL+2)}╤{'═'*(COL_VALUE+2)}╗")
def tbl_sep(): print(f"  ╠{'═'*(COL_LABEL+2)}╪{'═'*(COL_VALUE+2)}╣")
def tbl_bot(): print(f"  ╚{'═'*(COL_LABEL+2)}╧{'═'*(COL_VALUE+2)}╝")


def _wrap_ansi(value, width):
    """按可见显示宽度折行；ANSI 颜色码不计宽度、逐段保留（段尾 reset、段首重放当前色）。"""
    segs, cur, w, active = [], '', 0, ''
    i, n = 0, len(value)
    while i < n:
        m = ANSI_RE.match(value, i)
        if m:
            code = m.group(0)
            cur += code
            active = '' if code == C.RESET else code
            i = m.end()
            continue
        cw = char_width(value[i])
        if w + cw > width and w > 0:
            segs.append(cur + (C.RESET if active else ''))
            cur, w = active, 0
        cur += value[i]
        w += cw
        i += 1
    segs.append(cur + (C.RESET if active else ''))
    return segs


def _emit_cell(label, value):
    lpad = ' ' * max(0, COL_LABEL - display_len(label))
    vpad = ' ' * max(0, COL_VALUE - display_len(value))
    lstr = f"{label}{lpad}" if label else ' ' * COL_LABEL
    print(f"  ║ {lstr} │ {value}{vpad} ║")


def tbl_row(label, value):
    value = str(value)
    if display_len(value) <= COL_VALUE:
        _emit_cell(label, value)
    else:
        segs = _wrap_ansi(value, COL_VALUE)
        _emit_cell(label, segs[0])
        for s in segs[1:]:
            _emit_cell('', s)


# ── 数据采集 ─────────────────────────────────────────────
def get_real_public_ip():
    """请求国内直连回显服务拿真实公网 IP。

    规则代理（Clash 等）默认对国内 IP 走直连，请求国内回显服务会绕过 VPN，
    露出真实 ISP 出口（而非代理出口）。探测失败返回 None。
    """
    for url in ("http://ip.3322.net", "https://4.ipw.cn"):
        try:
            ip = requests.get(url, timeout=6).text.strip()
            if re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
                return ip
        except Exception:
            continue
    try:
        r = requests.get("https://myip.ipip.net", timeout=6)
        m = re.search(r'当前\s*IP[：:]\s*([\d.]+)', r.text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def get_ipv6():
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as s:
            s.connect(("2001:4860:4860::8888", 80))
            ip = s.getsockname()[0]
            if ip and ip not in ('', '::'):
                return ip
    except Exception:
        pass
    return None


def _macos_manual_dns():
    """macOS：用户在「系统设置 → 网络 → DNS」手动设置的 DNS。

    遍历所有启用的网络服务，收集 networksetup 返回的 DNS（手动设置才会返回 IP，
    未设置返回 "There aren't any..."）。不依赖 /etc/resolv.conf——避免被 Tailscale/VPN
    顶掉主 resolver 时漏掉用户真正设的 DNS。没手动设返回 []。
    """
    servers = []
    try:
        lines = subprocess.run(
            ['networksetup', '-listallnetworkservices'],
            capture_output=True, text=True, timeout=4,
        ).stdout.splitlines()[1:]  # 首行是说明文字
    except Exception:
        return []
    for svc in lines:
        svc = svc.strip()
        if not svc or svc.startswith('*'):  # * 前缀 = 已禁用服务
            continue
        try:
            out = subprocess.run(
                ['networksetup', '-getdnsservers', svc],
                capture_output=True, text=True, timeout=3,
            ).stdout
        except Exception:
            continue
        for line in out.splitlines():
            line = line.strip()
            try:
                ipaddress.ip_address(line)
                if line not in servers:
                    servers.append(line)
            except ValueError:
                pass
    return servers


def get_dns_servers():
    """返回 DNS 服务器列表（去重保序）。各分支只管收集，末尾统一去重。"""
    servers = []
    if IS_WIN:
        try:
            r = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 'Get-DnsClientServerAddress -AddressFamily IPv4 | '
                 'Select-Object -ExpandProperty ServerAddresses'],
                capture_output=True, text=True, timeout=5, encoding='utf-8',
            )
            for line in r.stdout.splitlines():
                ip = line.strip()
                if not ip:
                    continue
                try:
                    ipaddress.ip_address(ip)
                    servers.append(ip)
                except ValueError:
                    pass
        except Exception:
            pass
    else:
        # macOS：优先取用户手动设置的 DNS（不被 Tailscale/VPN 顶掉的 resolv.conf 掩盖）
        if platform.system() == "Darwin":
            manual = _macos_manual_dns()
            if manual:
                return manual
        try:
            with open('/etc/resolv.conf') as f:
                for line in f:
                    if line.strip().startswith('nameserver'):
                        servers.append(line.split()[1])
        except Exception:
            pass
        if not servers:
            try:
                r = subprocess.run(
                    ['scutil', '--dns'], capture_output=True, text=True, timeout=3,
                )
                for line in r.stdout.splitlines():
                    line = line.strip()
                    if line.startswith('nameserver['):
                        servers.append(line.split(':', 1)[1].strip())
            except Exception:
                pass
    return list(dict.fromkeys(servers))


def get_public_info():
    try:
        resp = requests.get(
            "http://ip-api.com/json/",
            params={"fields": "status,message,country,regionName,city,isp,org,proxy,hosting,query,timezone"},
            timeout=6,
        )
        return resp.json()
    except Exception as e:
        return {"status": "fail", "message": str(e)}


def get_ip_risk(ip):
    try:
        resp = requests.get(
            f"https://proxycheck.io/v2/{ip}",
            params={"risk": 1, "vpn": 1, "asn": 1},
            timeout=6,
        )
        data = resp.json().get(ip, {})
        risk  = data.get("risk")
        itype = data.get("type", "")
        proxy = data.get("proxy", "")
        parts = []
        score = None
        if risk is not None:
            score = int(risk)
            color, level = risk_color(score)
            parts.append(f"{color}{score}/100 {level}{C.RESET}")
        if itype:
            parts.append(f"类型 {itype}")
        if proxy == "yes":
            parts.append(warn("已标记为代理"))
        display = "  ".join(parts) if parts else warn("暂无数据")
        return display, score
    except Exception as e:
        return warn(f"查询失败（{e}）"), None


def get_stopforumspam(ip):
    try:
        resp = requests.get(
            "https://api.stopforumspam.org/api",
            params={"json": 1, "ip": ip},
            timeout=6,
        )
        data = resp.json().get("ip", {})
        if not data.get("appears"):
            return [ok("未收录  低风险")], False
        confidence = float(data.get("confidence", 0))
        frequency  = int(data.get("frequency", 0))
        last_seen  = (data.get("lastseen") or "")[:10]
        color, level = risk_color(confidence)
        lines = [f"{color}{confidence:.1f}/100 {level}{C.RESET}  举报 {frequency} 次"]
        if last_seen:
            lines.append(f"最近举报 {last_seen}")
        return lines, True
    except Exception as e:
        return [warn(f"查询失败（{e}）")], False


def get_proxy_envs():
    seen = {}
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"]:
        val = os.environ.get(key)
        if val and val not in seen.values():
            seen[key.upper()] = val
    return seen


def parse_macos_proxy(output):
    config = {}
    for line in output.splitlines():
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        config[key.strip()] = value.strip()

    proxies = []
    for name, prefix in [
        ("HTTP", "HTTP"),
        ("HTTPS", "HTTPS"),
        ("SOCKS", "SOCKS"),
    ]:
        if config.get(f"{prefix}Enable") != "1":
            continue
        host = config.get(f"{prefix}Proxy")
        port = config.get(f"{prefix}Port")
        if host and port:
            proxies.append(f"{name} {host}:{port}")

    if config.get("ProxyAutoConfigEnable") == "1":
        url = config.get("ProxyAutoConfigURLString")
        proxies.append(f"PAC {url}" if url else "PAC 已启用")

    return proxies


def get_system_proxy():
    if platform.system() != "Darwin":
        return None
    try:
        r = subprocess.run(
            ['scutil', '--proxy'], capture_output=True, text=True, timeout=3,
        )
        return parse_macos_proxy(r.stdout)
    except Exception:
        return None


def parse_tun_vpn(ifconfig_output, route_output):
    details = []
    interfaces = set()

    for match in re.finditer(r'^(utun\d*|tun\d*|tap\d*|wg\d*|ppp\d*):([\s\S]*?)(?=^\S|\Z)', ifconfig_output, re.MULTILINE):
        name, block = match.groups()
        interfaces.add(name)
        ipv4 = re.search(r'\binet\s+(\d+\.\d+\.\d+\.\d+)', block)
        if ipv4 and ipv4.group(1).startswith("198.18."):
            details.append(f"{name} {ipv4.group(1)}")

    for line in route_output.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        gateway = parts[1]
        netif = parts[-2] if parts[-1].isdigit() else parts[-1]
        if netif in interfaces or netif.startswith(('utun', 'tun', 'tap', 'wg', 'ppp')):
            item = f"{netif} 路由"
            if item not in details:
                details.append(item)
        if gateway.startswith("198.18."):
            item = f"{gateway} 代理网段"
            if item not in details:
                details.append(item)

    return bool(details), details


def get_tun_vpn_status():
    if IS_WIN:
        return None, []
    try:
        ifconfig_r = subprocess.run(
            ['ifconfig'], capture_output=True, text=True, timeout=3,
        )
        if platform.system() == "Darwin":
            route_cmd = ['netstat', '-rn', '-f', 'inet']
        else:
            route_cmd = ['ip', 'route']
        route_r = subprocess.run(route_cmd, capture_output=True, text=True, timeout=3)
        return parse_tun_vpn(ifconfig_r.stdout, route_r.stdout)
    except Exception as e:
        return None, [str(e)]


def _utc_str(offset):
    total = int(offset.total_seconds())
    h, r  = divmod(abs(total), 3600)
    sign  = "+" if total >= 0 else "-"
    return f"UTC{sign}{h:02d}:{r//60:02d}"


def get_system_tz():
    """系统 IANA 时区（不受 $TZ 影响）。macOS/Linux 读 /etc/localtime 软链，取不到返回 None。"""
    try:
        p = "/etc/localtime"
        if os.path.islink(p):
            target = os.readlink(p)
            if "zoneinfo/" in target:
                return target.split("zoneinfo/", 1)[1]
    except Exception:
        pass
    try:
        if os.path.exists("/etc/timezone"):
            with open("/etc/timezone") as f:
                name = f.read().strip()
            if name:
                return name
    except Exception:
        pass
    if IS_WIN:
        try:
            r = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 '[System.TimeZoneInfo]::Local.Id'],
                capture_output=True, text=True, timeout=3, encoding='utf-8',
            )
            wid = r.stdout.strip()
            if wid:
                return wid
        except Exception:
            pass
    return None


def get_cli_tz_name():
    tz_env = os.environ.get('TZ', '')
    if tz_env:
        return tz_env, True

    # 未设 $TZ：CLI 继承系统时区，优先给 IANA 名（含 '/' 可精确比对）
    sys_tz = get_system_tz()
    if sys_tz:
        return sys_tz, ('/' in sys_tz)

    name = datetime.datetime.now().astimezone().tzname() or "Unknown"
    return name, False


def tz_display(name):
    """IANA 时区名 → 'name  (UTC±HH:MM)'；无法解析则原样返回，空则 None。"""
    if not name:
        return None
    zi = make_zone(name)
    if zi:
        return f"{name}  ({_utc_str(datetime.datetime.now(zi).utcoffset())})"
    return name


def _tz_match(local_name, exit_name):
    """本地时区 vs 出口 IP 时区是否一致：先比 IANA 名，名不同再比 UTC offset。
    一致 True / 不一致 False / 无法比对 None。"""
    if not local_name or not exit_name:
        return None
    if local_name == exit_name:
        return True
    lz, ez = make_zone(local_name), make_zone(exit_name)
    if lz and ez:
        return datetime.datetime.now(lz).utcoffset() == datetime.datetime.now(ez).utcoffset()
    return None


def _tz_verdict(matched):
    """一致 → 绿；不一致 → 红；无法比对 → 黄。"""
    if matched is None:
        return warn("无法比对")
    return ok("一致") if matched else bad("不一致")


# ── Claude 检测 ──────────────────────────────────────────
def get_claude_base_url():
    """CLI 的 ANTHROPIC_BASE_URL：shell 环境变量优先，再读 ~/.claude/settings.json 的 env。

    返回 (url, source)；都没设返回 (None, None)。
    """
    shell = os.environ.get("ANTHROPIC_BASE_URL")
    if shell:
        return shell, "环境变量"
    cfgdir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    for name in ("settings.json", "settings.local.json"):
        try:
            with open(os.path.join(cfgdir, name)) as f:
                env = json.load(f).get("env") or {}
            if env.get("ANTHROPIC_BASE_URL"):
                return env["ANTHROPIC_BASE_URL"], "settings.json"
        except Exception:
            pass
    return None, None


def claude_installed():
    """本机是否装过 / 用过 Claude Code：配置目录、配置文件或二进制任一存在即算。"""
    cfgdir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    if os.path.isdir(cfgdir):
        return True
    if os.path.exists(os.path.expanduser("~/.claude.json")):
        return True
    return bool(shutil.which("claude"))


def is_official_base(url):
    """未设 或 host（含端口）== api.anthropic.com 视为官方（复刻 CC 的 Crt/Rrt）。"""
    if not url:
        return True
    try:
        sp = urlsplit(url)
        host = (sp.hostname or "") + (f":{sp.port}" if sp.port else "")
        return host.lower() == "api.anthropic.com"
    except Exception:
        return False


# 国产大模型关键词：hostname 命中 → 判为国产替代（不经 Anthropic，无封号风险）
DOMESTIC_MODEL_HINTS = [
    "deepseek", "moonshot", "kimi", "minimax", "xaminim", "zhipu", "bigmodel",
    "glm", "baichuan", "stepfun", "01ai", "lingyiwanwu", "dashscope", "qwen",
    "tongyi", "volces", "volcengine", "doubao", "hunyuan", "wenxin", "ernie",
    "iflytek", "spark", "sensenova",
]


def is_domestic_model(host):
    """host 含国产大模型关键词 → 判为国产替代（启发式，宁可漏判为有风险）。"""
    h = (host or "").lower()
    return any(kw in h for kw in DOMESTIC_MODEL_HINTS)


# 147 项域名黑名单快照：Anthropic 反蒸馏水印的 known 名单。
# 2.1.198 已把名单从二进制移除，此为 2.1.197 解出的冻结快照。
# 来源留底：fuxi/raw/2026-06-30-cc-反蒸馏水印审计/域名黑名单-147项.md
_BLACKLIST_147 = (
    "cn,sankuai.com,netease.com,163.com,baidu-int.com,baidu.com,alibaba-inc.com,alipay.com,"
    "antgroup-inc.cn,kuaishou.com,bytedance.net,xiaohongshu.com,ctripcorp.com,jd.com,jdcloud.com,"
    "bilibili.co,iflytek.com,stepfun-inc.com,aliyuncs.com,cn-shanghai.fcapp.run,cn-beijing.fcapp.run,"
    "xaminim.com,moonshot.ai,anyrouter.top,packyapi.com,aicodemirror.com,aigocode.com,hongshan.com,"
    "iwhalecloud.com,dhcoder.net,lemongpt.top,zhihuiapi.top,intsig.net,high-five-ai.xyz,cloudsway.net,"
    "4sapi.com,529961.com,88996.cloud,88code.ai,88code.org,91code.pro,992236.xyz,ai.codeqaq.com,"
    "ai.hybgzs.com,ai.kjvhh.com,aicanapi.com,aicoding.sh,aifast.site,aihubmix.com,anmory.com,"
    "api.5202030.xyz,api.ablai.top,api.bianxie.ai,api.bltcy.ai,api.cpass.cc,api.dev88.tech,"
    "api.dreamger.com,api.expansion.chat,api.gueai.com,api.holdai.top,api.ikuncode.cc,api.lconai.com,"
    "api.linkapi.org,api.mkeai.com,api.nekoapi.com,api.oaipro.com,api.ruyun.fun,api.ssopen.top,"
    "api.tu-zi.com,api.uglycat.cc,api.v3.cm,api.whatai.cc,api.wpgzs.top,api.xty.app,api.yuegle.com,"
    "api.zzyu.me,apimart.ai,apipro.maynor1024.live,apiyi.com,applyj.hiapi.top,augmunt.com,b4u.qzz.io,"
    "clauddy.com,claude-code-hub.app,claude-opus.top,claudeide.net,co.yes.vg,code.wenwen-ai.com,"
    "code.x-aio.com,codeilab.com,cubence.com,deeprouter.top,dimaray.com,dmxapi.com,docs.aigc2d.com,"
    "duckcoding.com,fk.hshwk.org,flapcode.com,foxcode.hshwk.org,foxcode.rjj.cc,fuli.hxi.me,getgoapi.com,"
    "gpt.zhizengzeng.com,gptgod.cloud,gptkey.eu.org,gptpay.store,hdgsb.com,henapi.top,instcopilot-api.com,"
    "jeniya.top,jiekou.ai,kg-api.cloud,n1n.ai,new-api.u4vr.com,new.xychatai.com,one-api.bltcy.top,"
    "one.ocoolai.com,oneapi.paintbot.top,open.xiaojingai.com,openclaude.me,opus.gptuu.com,poloai.top,"
    "poloapi.top,privnode.com,proxyai.com,qinzhiai.com,right.codes,runanytime.hxi.me,sssaicode.com,"
    "store.zzyus.top,tiantianai.pro,uiuiapi.com,uniapi.ai,vip.undyingapi.com,wolfai.top,wzw.de5.net,"
    "wzw.pp.ua,xairouter.com,xaixapi.com,xiaohuapi.site,xiaohumini.site,xy.poloapi.com,yansd666.com,"
    "yansd666.top,yunwu.ai,yunwu.zeabur.app,zenmux.ai"
).split(",")


def blacklist_hit(host):
    """host 精确或后缀命中 147 名单 → 返回命中项，否则 None（复刻水印 known 匹配）。"""
    h = (host or "").lower()
    for d in _BLACKLIST_147:
        if h == d or h.endswith("." + d):
            return d
    return None


# ── 本地环境检测渲染（local.py）────────────────────────────
def render_local_report(rep):
    """把 local.LocalChecker 的结果渲染成 ipcheck 原生表格板块。"""
    print(f"\n  {C.BOLD}本地环境检测（Claude 换号防关联）{C.RESET}  "
          f"{C.GRAY}(纯本地只读，零上传 / {platform.system()}){C.RESET}\n")
    tbl_top()
    for r in rep.results:
        if r.status == "pass":
            v = ok(r.summary)
        elif r.status == "info":
            v = f"{C.GRAY}{r.summary}{C.RESET}"
        elif r.status == "warn":
            v = warn(f"! {r.summary}")
        else:
            v = bad(f"! {r.summary}")
        tbl_row(r.label, v)
        for d in r.details:
            for line in str(d).splitlines():
                tbl_row("", f"{C.GRAY}{line}{C.RESET}")
    tbl_sep()
    if rep.has_fail:
        tbl_row("本地结论", bad("存在高危残留，清理后再换号"))
    elif rep.has_warn:
        tbl_row("本地结论", warn("无高危项，但有残留建议核对"))
    else:
        tbl_row("本地结论", ok("本地环境干净，可以安全切换账号"))
    tbl_bot()


def render_switch_verdict(rep, net):
    """--full 的合并结论：本地有 fail 或网络高风险 → 不建议换号。"""
    print(f"\n  {C.BOLD}换号就绪度（网络 + 本地合并结论）{C.RESET}\n")
    tbl_top()
    reasons = []
    if rep.has_fail:
        reasons.append(bad("! 本地检测存在高危残留（历史账号标识 / 凭证 / 遥测队列未清理）"))
    elif rep.has_warn:
        reasons.append(warn("! 本地有非高危残留，建议先核对"))
    if net["has_bad"]:
        reasons.append(bad("! 网络环境高风险（IP 风险分 ≥70 或中转命中 Anthropic 黑名单）"))
    elif net["has_mid"]:
        reasons.append(warn("! 网络环境中风险（时区不一致 / 节点有投诉 / 代理覆盖不完整）"))
    if rep.has_fail or net["has_bad"]:
        tbl_row("换号就绪度", bad("不建议换号，先处理上述红字项"))
    elif rep.has_warn or net["has_mid"]:
        tbl_row("换号就绪度", warn("可以换号，但建议先处理上述注意项"))
    else:
        tbl_row("换号就绪度", ok("网络与本地环境均就绪，可以安全换号"))
    for r in reasons:
        tbl_row("", r)
    tbl_bot()


# ── 主程序 ────────────────────────────────────────────────
USAGE = """\
ipcheck — 网络环境诊断工具

用法:
  ipcheck                  网络环境诊断（默认）
  ipcheck --local          本地环境检测（Claude 换号防关联，纯本地只读）
  ipcheck --full           网络 + 本地，末尾给出换号就绪度合并结论
  ipcheck --deep           本地检测加深扫会话记录中的历史标识（隐含 --local）
  ipcheck --reveal         敏感值显示明文（默认打码，隐含 --local）
  ipcheck --version        显示版本

退出码: 本地检测存在高危残留（fail）时为 1，否则为 0。
"""


def run_network_report():
    """网络环境诊断面板（默认输出），返回综合结论信号。"""
    pub = get_public_info()
    pub_ok = pub.get("status") == "success"

    print(f"\n  {C.BOLD}ipcheck — 网络环境诊断工具{C.RESET}  "
          f"{C.GRAY}({platform.system()} / Python {platform.python_version()}){C.RESET}\n")
    tbl_top()

    # 本机网络
    real_ip = get_real_public_ip()
    tbl_row("本机真实 IP", real_ip if real_ip else warn("探测失败（无国内直连）"))
    ipv6_addr = get_ipv6()
    ipv6_leaked = ipv6_addr is not None
    if ipv6_leaked:
        tbl_row("IPv6 地址", warn(ipv6_addr))
        tbl_row("", warn("建议禁用，避免暴露真实地址"))
    else:
        tbl_row("IPv6 地址", ok("已禁用"))
    dns = get_dns_servers()
    if dns:
        tbl_row("本地 DNS", dns_label(dns[0]))
        for d in dns[1:]:
            tbl_row("", dns_label(d))
    else:
        tbl_row("本地 DNS", warn("获取失败"))
    dns_cn = any("(CN)" in KNOWN_DNS.get(d, "") for d in dns)

    tbl_sep()

    # 公网信息
    if pub_ok:
        pub_ip = pub.get("query")
        tbl_row("出口 IP",          pub_ip or bad("获取失败"))
        tbl_row("国家 / 省份",      f"{_val(pub.get('country'))} / {_val(pub.get('regionName'))}")
        tbl_row("城市",              _val(pub.get("city")))
        tbl_row("运营商", _val(pub.get("isp")))
        tbl_row("IP 归属",           _val(pub.get("org")))
        tbl_row("所处时区", tz_display(pub.get("timezone")) or _val(None))
    else:
        tbl_row("公网请求", bad(pub.get("message") or "未知错误"))

    tbl_sep()

    # 代理检测
    risk_score = None
    proxy_envs = get_proxy_envs()
    if proxy_envs:
        tbl_row("环境变量代理", ok("已设置"))
    else:
        tbl_row("环境变量代理", warn("未设置"))
    system_proxy = get_system_proxy()
    if system_proxy:
        tbl_row("系统代理", ok("已开启"))
    elif system_proxy == []:
        tbl_row("系统代理", warn("未开启"))
    else:
        tbl_row("系统代理", warn("暂不支持检测"))
    tun_active, _ = get_tun_vpn_status()
    if tun_active is True:
        tbl_row("TUN / VPN", ok("疑似开启"))
    elif tun_active is False:
        tbl_row("TUN / VPN", warn("未检测到"))
    else:
        tbl_row("TUN / VPN", warn("无法检测"))
    spam_listed = False
    if pub_ok:
        tbl_row("机房 / 住宅",   warn("机房 IP") if pub.get("hosting") else ok("住宅 IP"))
        if (pub.get("hosting") or pub.get("proxy")) and pub_ip:
            risk_display, risk_score = get_ip_risk(pub_ip)
            tbl_row("IP 风险查询",  risk_display)
            spam_lines, spam_listed = get_stopforumspam(pub_ip)
            tbl_row("垃圾滥用记录", spam_lines[0])
            for line in spam_lines[1:]:
                tbl_row("", line)

    tbl_sep()

    # 时区
    tz_matched = None
    cli_offset = datetime.datetime.now().astimezone().utcoffset()

    sys_tz = get_system_tz()
    tbl_row("系统时区", tz_display(sys_tz) or warn("未知"))

    tz_name, _ = get_cli_tz_name()
    tbl_row("CLI 时区", f"{tz_name}  ({_utc_str(cli_offset)})")

    pub_tz_name = pub.get("timezone") if pub_ok else None
    if pub_tz_name:
        # CC CLI 认 $TZ → 比 CLI 时区；桌面版不认 $TZ、走系统时区 → 比系统时区
        cli_m = _tz_match(tz_name, pub_tz_name)
        sys_m = _tz_match(sys_tz, pub_tz_name)
        tz_matched = cli_m  # 综合结论以 CC CLI 为准（本工具主要面向 CC CLI 用户）
        tbl_row("时区一致性", f"CC CLI：{_tz_verdict(cli_m)}")
        tbl_row("", f"桌面版：{_tz_verdict(sys_m)}")

    tbl_sep()

    # Claude 检测（CLI）
    blacklist_matched = False
    claude_url, claude_src = get_claude_base_url()
    if not claude_url:
        if claude_installed():
            tbl_row("CLI 端点", ok("官方直连（未设 ANTHROPIC_BASE_URL）"))
        else:
            tbl_row("CLI 端点", warn("未检测到 Claude Code（未安装或未使用）"))
    elif is_official_base(claude_url):
        tbl_row("CLI 端点", ok("官方直连（api.anthropic.com）"))
    else:
        claude_host = urlsplit(claude_url).hostname or claude_url
        if is_domestic_model(claude_host):
            tbl_row("CLI 端点", ok(claude_host))
            tbl_row("", ok("疑似国产大模型，不经 Anthropic，无封号风险"))
        else:
            tbl_row("CLI 端点", bad(claude_host))
            tbl_row("", bad("疑似中转，注意数据泄露风险"))
            hit = blacklist_hit(claude_host)
            if hit:
                blacklist_matched = True
                tbl_row("Anthropic 147 黑名单", bad(f"命中（{hit}）"))
            else:
                tbl_row("Anthropic 147 黑名单", ok("未命中"))

    tbl_sep()
    # 检测建议：只列可优化项（非绿色），正常项不提；综合结论另起一块
    suggestions = []
    if ipv6_leaked:
        suggestions.append(bad("! IPv6 泄露，暴露真实地址，建议禁用"))
    if dns_cn:
        suggestions.append(warn("! DNS 使用国内服务商，可能暴露真实位置"))
    elif not dns:
        suggestions.append(warn("- DNS 获取失败，无法评估"))
    if not pub_ok:
        suggestions.append(warn("- IP 信息获取失败，无法评估风险"))
    elif pub.get("proxy") or pub.get("hosting"):
        if risk_score is None:
            suggestions.append(warn("! IP 为机房/代理，未查到风险分数"))
        elif risk_score >= 70:
            suggestions.append(bad(f"! IP 风险高（{risk_score}/100），建议更换节点"))
        elif risk_score >= 30:
            suggestions.append(warn(f"! IP 风险中等（{risk_score}/100），建议关注"))
    if not (tun_active is True or (proxy_envs and system_proxy)):
        suggestions.append(warn("! 代理可能不完整，建议开 TUN，或环境变量 + 系统代理都设"))
    if tz_matched is False:
        suggestions.append(bad("! 时区不一致，建议调整"))
    elif tz_matched is None:
        suggestions.append(warn("- 时区无法比对"))
    if blacklist_matched:
        suggestions.append(bad("! 中转端点命中 Anthropic 黑名单，封号风险高，建议改官方直连或国产大模型"))

    if suggestions:
        tbl_row("检测建议", suggestions[0])
        for s in suggestions[1:]:
            tbl_row("", s)
    else:
        tbl_row("检测建议", ok("各项正常，暂无可优化项"))

    tbl_sep()
    has_bad = ((risk_score is not None and risk_score >= 70)
               or blacklist_matched)
    has_mid = (tz_matched is False
               or spam_listed
               or tun_active is not True)
    if has_bad:
        tbl_row("综合结论", bad("当前环境 Claude 使用高风险"))
    elif has_mid:
        tbl_row("综合结论", warn("当前环境 Claude 使用中风险"))
    else:
        tbl_row("综合结论", ok("当前环境 Claude 使用低风险"))

    tbl_bot()

    if IS_WIN and _ZI is None:
        print(f"\n  {C.YELLOW}提示：pip install tzdata  （Windows 时区精确比对所需）{C.RESET}")
    if IS_WIN and not _COLOR:
        print(f"\n  提示：pip install colorama  （启用彩色输出）")
    print()

    return {"has_bad": has_bad, "has_mid": has_mid}


def main():
    argv = sys.argv[1:]
    if argv and argv[0] in ('--version', '-v', '-V'):
        from ipcheck import __version__
        print(f"ipcheck {__version__}")
        return 0
    if argv and argv[0] in ('--help', '-h'):
        print(USAGE)
        return 0

    local_only = '--local' in argv
    full = '--full' in argv
    deep = '--deep' in argv
    reveal = '--reveal' in argv
    # --deep / --reveal 只作用于本地检测，单独使用时隐含 --local
    want_local = local_only or full or deep or reveal
    want_net = not want_local or full

    fit_width()

    net = None
    if want_net:
        net = run_network_report()

    rep = None
    if want_local:
        from ipcheck.local import run_local_checks
        rep = run_local_checks(deep=deep, reveal=reveal)
        render_local_report(rep)
        print()

    if full:
        render_switch_verdict(rep, net)
        print()

    # 本地检测存在 fail → 非零退出码
    return 1 if (rep and rep.has_fail) else 0
