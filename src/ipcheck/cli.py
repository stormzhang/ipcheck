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
import platform

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
COL_LABEL, COL_VALUE = 18, 46

def tbl_top(): print(f"  ╔{'═'*(COL_LABEL+2)}╤{'═'*(COL_VALUE+2)}╗")
def tbl_sep(): print(f"  ╠{'═'*(COL_LABEL+2)}╪{'═'*(COL_VALUE+2)}╣")
def tbl_bot(): print(f"  ╚{'═'*(COL_LABEL+2)}╧{'═'*(COL_VALUE+2)}╝")


def tbl_row(label, value):
    value = str(value)
    lpad = ' ' * max(0, COL_LABEL - display_len(label))
    vpad = ' ' * max(0, COL_VALUE - display_len(value))
    lstr = f"{label}{lpad}" if label else ' ' * COL_LABEL
    print(f"  ║ {lstr} │ {value}{vpad} ║")


# ── 数据采集 ─────────────────────────────────────────────
def get_lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return warn("获取失败")


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


def get_dns_servers():
    servers = []
    if IS_WIN:
        try:
            r = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 'Get-DnsClientServerAddress -AddressFamily IPv4 | '
                 'Select-Object -ExpandProperty ServerAddresses'],
                capture_output=True, text=True, timeout=5, encoding='utf-8',
            )
            seen = set()
            for line in r.stdout.splitlines():
                ip = line.strip()
                if not ip:
                    continue
                try:
                    ipaddress.ip_address(ip)
                    if ip not in seen:
                        seen.add(ip)
                        servers.append(ip)
                except ValueError:
                    pass
        except Exception:
            pass
    else:
        try:
            seen = set()
            with open('/etc/resolv.conf') as f:
                for line in f:
                    if line.strip().startswith('nameserver'):
                        ip = line.split()[1]
                        if ip not in seen:
                            seen.add(ip)
                            servers.append(ip)
        except Exception:
            pass
        if not servers:
            try:
                r = subprocess.run(
                    ['scutil', '--dns'], capture_output=True, text=True, timeout=3,
                )
                seen = set()
                for line in r.stdout.splitlines():
                    line = line.strip()
                    if line.startswith('nameserver['):
                        ip = line.split(':', 1)[1].strip()
                        if ip not in seen:
                            seen.add(ip)
                            servers.append(ip)
            except Exception:
                pass
    return servers


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
            parts.append(bad("已标记为代理"))
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
            return [ok("未收录  低风险 ✓")]
        confidence = float(data.get("confidence", 0))
        frequency  = int(data.get("frequency", 0))
        last_seen  = (data.get("lastseen") or "")[:10]
        color, level = risk_color(confidence)
        lines = [f"{color}{confidence:.1f}/100 {level}{C.RESET}  举报 {frequency} 次"]
        if last_seen:
            lines.append(f"最近举报 {last_seen}")
        return lines
    except Exception as e:
        return [warn(f"查询失败（{e}）")]


def get_proxy_envs():
    seen = {}
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"]:
        val = os.environ.get(key)
        if val and val not in seen.values():
            seen[key.upper()] = val
    return seen


def _utc_str(offset):
    total = int(offset.total_seconds())
    h, r  = divmod(abs(total), 3600)
    sign  = "+" if total >= 0 else "-"
    return f"UTC{sign}{h:02d}:{r//60:02d}"


def get_cli_tz_name():
    tz_env = os.environ.get('TZ', '')
    if tz_env:
        return tz_env, True

    if IS_WIN:
        try:
            r = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 '[System.TimeZoneInfo]::Local.Id'],
                capture_output=True, text=True, timeout=3, encoding='utf-8',
            )
            win_id = r.stdout.strip()
            if win_id:
                return win_id, False
        except Exception:
            pass

    name = datetime.datetime.now().astimezone().tzname() or "Unknown"
    return name, False


# ── 主程序 ────────────────────────────────────────────────
def main():
    if len(sys.argv) > 1 and sys.argv[1] in ('--version', '-v', '-V'):
        from ipcheck import __version__
        print(f"ipcheck {__version__}")
        return

    pub = get_public_info()
    pub_ok = pub.get("status") == "success"

    print(f"\n  {C.BOLD}ipcheck — 网络环境诊断工具{C.RESET}  "
          f"{C.GRAY}({platform.system()} / Python {platform.python_version()}){C.RESET}\n")
    tbl_top()

    # 本机网络
    tbl_row("局域网 IP", get_lan_ip())
    ipv6_addr = get_ipv6()
    ipv6_leaked = ipv6_addr is not None
    tbl_row("IPv6 地址", ipv6_addr if ipv6_leaked else warn("已禁用"))
    dns = get_dns_servers()
    if dns:
        tbl_row("DNS 服务器", dns_label(dns[0]))
        for d in dns[1:]:
            tbl_row("", dns_label(d))
    else:
        tbl_row("DNS 服务器", warn("获取失败"))
    dns_cn = any("(CN)" in KNOWN_DNS.get(d, "") for d in dns)

    tbl_sep()

    # 公网信息
    if pub_ok:
        pub_ip = pub.get("query")
        tbl_row("公网 IP",          pub_ip or bad("获取失败"))
        tbl_row("国家 / 省份",      f"{_val(pub.get('country'))} / {_val(pub.get('regionName'))}")
        tbl_row("城市",              _val(pub.get("city")))
        tbl_row("ISP(互联网服务商)", _val(pub.get("isp")))
        tbl_row("组织",              _val(pub.get("org")))
        pub_tz_name = pub.get("timezone")
        if pub_tz_name:
            zi = make_zone(pub_tz_name)
            if zi:
                off = datetime.datetime.now(zi).utcoffset()
                tbl_row("所处时区", f"{pub_tz_name}  ({_utc_str(off)})")
            else:
                tbl_row("所处时区", pub_tz_name)
        else:
            tbl_row("所处时区", _val(None))
    else:
        tbl_row("公网请求", bad(pub.get("message") or "未知错误"))

    tbl_sep()

    # 代理检测
    risk_score = None
    proxy_envs = get_proxy_envs()
    if proxy_envs:
        for k, v in proxy_envs.items():
            tbl_row(k, warn(v))
    else:
        tbl_row("环境变量代理", ok("未设置"))
    if pub_ok:
        tbl_row("IP 标记为代理", bad("是 ✗") if pub.get("proxy")   else ok("否 ✓"))
        tbl_row("机房 / 托管",   bad("是 ✗") if pub.get("hosting") else ok("否 ✓"))
        if (pub.get("hosting") or pub.get("proxy")) and pub_ip:
            risk_display, risk_score = get_ip_risk(pub_ip)
            tbl_row("IP 风险查询",  risk_display)
            spam_lines = get_stopforumspam(pub_ip)
            tbl_row("垃圾滥用记录", spam_lines[0])
            for line in spam_lines[1:]:
                tbl_row("", line)

    tbl_sep()

    # 时区
    tz_matched = None
    cli_dt     = datetime.datetime.now().astimezone()
    cli_offset = cli_dt.utcoffset()
    tz_name, is_iana = get_cli_tz_name()
    tbl_row("CLI 时区", f"{tz_name}  ({_utc_str(cli_offset)})")

    pub_tz_name = pub.get("timezone") if pub_ok else None
    if pub_tz_name:
        pub_zi     = make_zone(pub_tz_name)
        pub_offset = datetime.datetime.now(pub_zi).utcoffset() if pub_zi else None

        if is_iana:
            tz_matched = tz_name == pub_tz_name
            match = ok("一致 ✓") if tz_matched else bad("不一致 ✗")
        elif pub_offset is not None:
            tz_matched = cli_offset == pub_offset
            if tz_matched:
                match = warn("UTC 偏移一致（建议设置 $TZ=IANA 名称精确比对）")
            else:
                match = bad("不一致 ✗（UTC 偏移不同）")
        else:
            match = warn("无法比对（tzdata 未安装？pip install tzdata）")
        tbl_row("时区一致性", match)

    tbl_sep()
    conclusions = []
    if ipv6_leaked:
        conclusions.append(bad("✗ IPv6 泄露，暴露真实地址"))
    else:
        conclusions.append(ok("✓ IPv6 已禁用，无泄露风险"))
    if dns_cn:
        conclusions.append(bad("✗ DNS 使用国内服务商，暴露真实位置"))
    elif not dns:
        conclusions.append(warn("- DNS 获取失败，无法评估"))
    else:
        conclusions.append(ok("✓ DNS 未检测到国内服务商"))
    if not pub_ok:
        conclusions.append(warn("- IP 信息获取失败，无法评估风险"))
    elif pub.get("proxy") or pub.get("hosting"):
        if risk_score is not None:
            if risk_score < 30:
                conclusions.append(ok(f"✓ IP 风险低（{risk_score}/100）"))
            elif risk_score < 70:
                conclusions.append(warn(f"! IP 风险中等（{risk_score}/100），建议关注"))
            else:
                conclusions.append(bad(f"✗ IP 风险高（{risk_score}/100），建议更换节点"))
        else:
            conclusions.append(warn("! IP 为机房/代理，未查到风险分数"))
    else:
        conclusions.append(ok("✓ IP 正常，无风险标记"))
    if tz_matched is True:
        conclusions.append(ok("✓ 时区一致"))
    elif tz_matched is False:
        conclusions.append(bad("✗ 时区不一致，建议调整"))
    else:
        conclusions.append(warn("- 时区无法比对"))
    has_bad = (ipv6_leaked or dns_cn
               or (risk_score is not None and risk_score >= 70)
               or tz_matched is False)
    tbl_row("结论分析", conclusions[0])
    for c in conclusions[1:]:
        tbl_row("", c)
    tbl_sep()
    if has_bad:
        tbl_row("综合结论", bad("⚠ 当前环境 Claude 使用高风险"))
    else:
        tbl_row("综合结论", ok("✓ 当前环境 Claude 使用低风险"))

    tbl_bot()

    if IS_WIN and _ZI is None:
        print(f"\n  {C.YELLOW}提示：pip install tzdata  （Windows 时区精确比对所需）{C.RESET}")
    if IS_WIN and not _COLOR:
        print(f"\n  提示：pip install colorama  （启用彩色输出）")
    print()
