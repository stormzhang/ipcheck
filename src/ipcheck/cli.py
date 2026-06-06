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
import random
import concurrent.futures

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


def clip(s, width):
    """按显示宽度裁剪字符串，保留 ANSI 颜色码，超出部分用省略号代替。
    保证表格值不会撑破右边框。"""
    if display_len(s) <= width:
        return s
    out, used, has_ansi = [], 0, False
    for m in re.finditer(r'(\033\[[0-9;]*m)|(.)', s):
        if m.group(1):
            out.append(m.group(1))
            has_ansi = True
            continue
        ch = m.group(2)
        w = char_width(ch)
        if used + w > width - 1:        # 预留 1 格给省略号
            break
        out.append(ch)
        used += w
    out.append('…')
    if has_ansi:
        out.append(C.RESET)
    return ''.join(out)


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
    value = clip(str(value), COL_VALUE)
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


# ── 真实 DNS 出口测试（bash.ws 随机子域名法）────────────────
# 原理：解析 N 个唯一子域名 → 强制递归 resolver 去访问 bash.ws 权威服务器
# （不命中缓存），权威端记录"实际是哪些 resolver IP 来查的"，即 DNS 真实出口。
# 比静态读 /etc/resolv.conf 更可信：能抓出代理接管后的真实解析路径，
# 也能识别未在 KNOWN_DNS 表内的国内 DNS。
LEAK_LOOKUP_COUNT = 6
LEAK_HTTP_TIMEOUT = 5
LEAK_RETRIES = 2          # 异常 / 超时 / 无数据后自动重试次数


def _is_cn_country(name):
    if not name:
        return False
    s = str(name).strip().upper()
    return "CHINA" in s or s == "CN"


def detect_proxy_url():
    """从环境变量或 macOS 系统代理推断一个可用的代理地址，供代理路径测试用。"""
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                "ALL_PROXY", "all_proxy"):
        val = os.environ.get(key)
        if val:
            return val
    for item in (get_system_proxy() or []):
        m = re.search(r'(\d{1,3}(?:\.\d{1,3}){3}):(\d+)', item)
        if m:
            return f"http://{m.group(1)}:{m.group(2)}"
    return None


def _bashws_result(test_id):
    """查询 bash.ws 记录的、实际发起解析的 resolver 列表。失败返回 None。"""
    try:
        resp = requests.get(
            f"https://bash.ws/dnsleak/test/{test_id}?json", timeout=8,
        )
        data = resp.json()
    except Exception:
        return None
    uniq = {}
    for d in data:
        if d.get("type") != "dns":
            continue
        ip = d.get("ip", "")
        country = (d.get("country_name") or d.get("country")
                   or d.get("country_iso") or "")
        uniq[ip] = {"ip": ip, "country": country, "asn": d.get("asn", "")}
    return list(uniq.values())


def _trigger_direct(test_id):
    """直连/OS 路径：用系统 resolver 解析唯一子域名。"""
    hosts = [f"{i}.{test_id}.bash.ws" for i in range(1, LEAK_LOOKUP_COUNT + 1)]

    def _resolve(h):
        try:
            socket.gethostbyname(h)
        except OSError:
            pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=LEAK_LOOKUP_COUNT) as ex:
        list(ex.map(_resolve, hosts))


def _trigger_proxy(test_id, proxy_url):
    """代理路径（Claude 实际走的）：经代理请求唯一子域名，让解析在出口节点发生。"""
    hosts = [f"{i}.{test_id}.bash.ws" for i in range(1, LEAK_LOOKUP_COUNT + 1)]
    proxies = {"http": proxy_url, "https": proxy_url}

    def _fetch(h):
        try:
            requests.get(f"http://{h}", proxies=proxies,
                         timeout=LEAK_HTTP_TIMEOUT)
        except Exception:
            pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=LEAK_LOOKUP_COUNT) as ex:
        list(ex.map(_fetch, hosts))


def _run_leak_path(trigger):
    """跑一条 DNS 出口路径；异常 / 超时 / 无数据时换新 id 自动重试。
    返回 resolver 列表；全部尝试失败返回 None，权威端无记录返回 []。"""
    last = None
    for _ in range(LEAK_RETRIES + 1):
        tid = random.randint(1_000_000, 9_999_999)
        try:
            trigger(tid)
            servers = _bashws_result(tid)
        except Exception:
            servers = None
        if servers:                 # 拿到非空结果才算成功
            return servers
        last = servers              # None（失败）或 []（无记录）
    return last


def dns_leak_test(proxy_url=None):
    """返回 {'direct': [...]|None, 'proxy': [...]|None}，元素含 ip/country/asn。"""
    result = {"direct": None, "proxy": None}
    result["direct"] = _run_leak_path(_trigger_direct)
    if proxy_url:
        result["proxy"] = _run_leak_path(
            lambda tid: _trigger_proxy(tid, proxy_url))
    return result


# ── AI 域名隧道探针（测路由可达性，DNS 出口测试的补充）─────────
# 解析主流国外 AI 域名，测"解析到的 IP 是否经代理隧道可达"。
# 与 DNS 出口测试互补：出口测试看"解析从哪走"，这里看"连接往哪走"。
# 若本地 DNS 被污染、解析出假 IP，隧道 CONNECT 会失败，可暴露污染。
AI_PROBE_DOMAINS = [
    "anthropic.com", "api.anthropic.com", "claude.ai",
    "openai.com", "api.openai.com", "chatgpt.com",
    "gemini.google.com", "x.ai", "perplexity.ai", "huggingface.co",
]
AI_CORE_DOMAINS = {"anthropic.com", "api.anthropic.com", "claude.ai"}
PROBE_TIMEOUT = 5


def _proxy_host_port(proxy_url):
    m = re.search(r'(?:.*@)?([\w.\-]+):(\d+)', proxy_url or "")
    return (m.group(1), int(m.group(2))) if m else None


def _tunnel_reachable(hp, target_ip, port=443, timeout=PROBE_TIMEOUT):
    """经 HTTP 代理 CONNECT 到 target_ip:port，判断是否经隧道可达。"""
    host, pport = hp
    try:
        with socket.create_connection((host, pport), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(f"CONNECT {target_ip}:{port} HTTP/1.1\r\n"
                      f"Host: {target_ip}:{port}\r\n\r\n".encode())
            status_line = s.recv(128).split(b"\r\n", 1)[0]
            return b" 200" in status_line
    except Exception:
        return False


def ai_domain_probe(proxy_url):
    """返回 [{'domain','ip','reachable'}]；无可用代理返回 None。"""
    hp = _proxy_host_port(proxy_url)
    if not hp:
        return None

    def _probe(domain):
        try:
            ip = socket.gethostbyname(domain)
        except OSError:
            return {"domain": domain, "ip": None, "reachable": None}
        return {"domain": domain, "ip": ip,
                "reachable": _tunnel_reachable(hp, ip)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(_probe, AI_PROBE_DOMAINS))


# ── 主程序 ────────────────────────────────────────────────
def main():
    if len(sys.argv) > 1 and sys.argv[1] in ('--version', '-v', '-V'):
        from ipcheck import __version__
        print(f"ipcheck {__version__}")
        return

    leak_enabled = not any(a in ('--no-leak', '--fast') for a in sys.argv[1:])

    pub = get_public_info()
    pub_ok = pub.get("status") == "success"

    leak = {"direct": None, "proxy": None}
    probe = None
    if leak_enabled:
        proxy_url = detect_proxy_url()
        print(f"  {C.GRAY}正在进行真实 DNS 出口测试…       {C.RESET}", end="\r", flush=True)
        leak = dns_leak_test(proxy_url)
        print(f"  {C.GRAY}正在探测 AI 域名隧道可达性…      {C.RESET}", end="\r", flush=True)
        probe = ai_domain_probe(proxy_url)
        print(" " * 50, end="\r")  # 清掉提示行

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

    # 真实 DNS 出口测试结果（以此为准，上面的静态表仅作标注）
    leak_available = (leak.get("direct") is not None
                      or leak.get("proxy") is not None)
    pub_country = pub.get("country") if pub_ok else None

    def _country_eq(a, b):
        if not a or not b:
            return False
        alias = {"us": "united states", "usa": "united states",
                 "uk": "united kingdom", "hk": "hong kong",
                 "tw": "taiwan", "cn": "china"}
        na, nb = a.strip().lower(), b.strip().lower()
        return alias.get(na, na) == alias.get(nb, nb)

    def _render_exit(servers, label):
        if servers is None:
            tbl_row(label, warn("测试失败 / 超时"))
            return []
        if not servers:
            tbl_row(label, warn("无数据"))
            return []
        countries, first = [], True
        for s in servers:
            cc = s["country"] or "未知"
            countries.append(s["country"])
            txt = f"{s['ip']}  {cc}"
            if s["asn"]:
                txt += f"  {s['asn']}"
            tbl_row(label if first else "", bad(txt) if _is_cn_country(cc) else txt)
            first = False
        return countries

    dns_exit_cn = False
    dns_exit_consistent = None
    if leak_enabled:
        direct_cc = _render_exit(leak.get("direct"), "通用DNS出口(直连)")
        proxy_cc = (_render_exit(leak.get("proxy"), "通用DNS出口(代理)")
                    if leak.get("proxy") is not None else [])
        # Claude 走代理路径；无代理时退回直连路径判断
        claude_cc = proxy_cc if leak.get("proxy") is not None else direct_cc
        dns_exit_cn = any(_is_cn_country(c) for c in claude_cc)
        if pub_country and claude_cc:
            dns_exit_consistent = all(_country_eq(c, pub_country) for c in claude_cc)
            if dns_exit_cn:
                verdict = bad("DNS 出口含中国 ✗，真实位置泄露")
            elif dns_exit_consistent:
                verdict = ok(f"一致 ✓（均为 {pub_country}）")
            else:
                verdict = warn(f"出口地与 IP({pub_country}) 不一致，留意")
            tbl_row("DNS 出口一致性", verdict)

    # AI 域名隧道探针（路由可达性，补充信号）
    probe_core_bad = False
    if probe is not None:
        ok_n = sum(1 for p in probe if p["reachable"])
        tbl_row("AI 域名隧道探针", f"{ok_n}/{len(probe)} 经隧道可达")
        for p in probe:
            ip = p["ip"] or "解析失败"
            mark = ok("✓") if p["reachable"] else (
                warn("?") if p["reachable"] is None else bad("✗"))
            tbl_row("", f"{mark} {p['domain']}  {ip}")
        probe_core_bad = any(
            p["domain"] in AI_CORE_DOMAINS and not p["reachable"] for p in probe)

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
    system_proxy = get_system_proxy()
    if system_proxy:
        tbl_row("系统代理", warn(system_proxy[0]))
        for item in system_proxy[1:]:
            tbl_row("", warn(item))
    elif system_proxy == []:
        tbl_row("系统代理", ok("未设置"))
    else:
        tbl_row("系统代理", warn("暂不支持检测"))
    tun_active, tun_details = get_tun_vpn_status()
    if tun_active is True:
        tbl_row("TUN / VPN", warn("疑似开启"))
        for item in tun_details:
            tbl_row("", warn(item))
    elif tun_active is False:
        tbl_row("TUN / VPN", ok("未检测到"))
    else:
        tbl_row("TUN / VPN", warn("无法检测"))
    if pub_ok:
        tbl_row("IP 标记为代理", warn("是 !") if pub.get("proxy")   else ok("否 ✓"))
        tbl_row("机房 / 托管",   warn("是 !") if pub.get("hosting") else ok("否 ✓"))
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
    if leak_enabled and leak_available:
        if dns_exit_cn:
            conclusions.append(warn("! 通用DNS出口含中国，非白名单流量暴露真实位置"))
        elif dns_exit_consistent is False:
            conclusions.append(warn("! 通用DNS出口地与公网 IP 不一致，建议核查"))
        else:
            conclusions.append(ok("✓ 通用DNS出口正常，未泄露"))
        if dns_cn and not dns_exit_cn:
            conclusions.append(ok("✓ 本机虽配国内 DNS，但实际解析未走它（代理已接管）"))
    elif dns_cn:
        conclusions.append(warn("! DNS 配置含国内服务商，可能暴露真实位置（未做出口测试）"))
    elif not dns:
        conclusions.append(warn("- DNS 获取失败，无法评估"))
    else:
        conclusions.append(ok("✓ DNS 配置未检测到国内服务商"))
    if probe is not None:
        unreachable = [p["domain"] for p in probe if not p["reachable"]]
        if probe_core_bad:
            conclusions.append(bad("✗ Claude 核心域名隧道不可达，路由 / DNS 污染异常"))
        elif unreachable:
            conclusions.append(warn(f"! {len(unreachable)} 个 AI 域名隧道不可达（{unreachable[0]} 等）"))
        else:
            conclusions.append(ok("✓ AI 域名均经隧道可达，路由正常"))
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
    # Claude 专用风险：只看 Claude 实际路径承受的信号
    # （IPv6 / 出口 IP 信誉 / 时区一致性 / Claude 核心域名隧道可达性）
    claude_bad = (ipv6_leaked
                  or probe_core_bad
                  or (risk_score is not None and risk_score >= 70)
                  or tz_matched is False)
    # 通用环境风险：影响非白名单流量的卫生问题（通用 DNS 出口、其他 AI 域名不可达）
    env_bad = dns_exit_cn or (probe is not None and any(
        not p["reachable"] and p["domain"] not in AI_CORE_DOMAINS for p in probe))
    if not (leak_enabled and leak_available):
        env_bad = env_bad or dns_cn       # 未做出口测试时回退到静态判断

    tbl_row("结论分析", conclusions[0])
    for c in conclusions[1:]:
        tbl_row("", c)
    tbl_sep()
    tbl_row("Claude 专用风险",
            bad("⚠ 高风险，建议处理") if claude_bad else ok("✓ 低风险，可放心使用"))
    tbl_row("通用环境风险",
            bad("⚠ 高风险（影响非白名单流量）") if env_bad else ok("✓ 低风险"))

    tbl_bot()

    if leak_enabled and leak_available:
        print(f"\n  {C.GRAY}注：「通用DNS出口」测的是非白名单域名(bash.ws)的真实解析出口，"
              f"反映默认 DNS 路径；{C.RESET}")
        print(f"  {C.GRAY}　 Claude 等白名单 AI 域名走专属 DoH，其路由可达性以"
              f"「AI 域名隧道探针」为准。{C.RESET}")
    if IS_WIN and _ZI is None:
        print(f"\n  {C.YELLOW}提示：pip install tzdata  （Windows 时区精确比对所需）{C.RESET}")
    if IS_WIN and not _COLOR:
        print(f"\n  提示：pip install colorama  （启用彩色输出）")
    print()
