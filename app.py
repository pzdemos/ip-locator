#!/usr/bin/env python3
"""IP 查询服务（奶油绿设计 · pconline 国内主源 + ipapi.co 国际兜底 · 中英双语）。
  /             -> 静态 HTML（SPA，默认英文）
  /api/lookup   -> JSON，参数 ?ip= 可选
  /api/history  -> JSON
  /history      -> 历史记录页面
"""
import os
import json
import time
import socket
import threading
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", 8082))
LOG_DIR = os.environ.get("LOG_DIR", "/root/common/ip-lookup")
LOG_FILE = os.path.join(LOG_DIR, "queries.jsonl")
BG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bg.png")

# 限流：同 visitor+IP 60s 内复用缓存；同 visitor 60s 内最多 15 次
CACHE_TTL = 60
RATE_LIMIT = 15
RATE_WINDOW = 60
_cache = {}      # key: (visitor, target_ip) -> (timestamp, result)
_rate = {}       # visitor -> [timestamps...]
_lock = threading.Lock()


def check_rate(visitor):
    """返回 True 表示放行，False 表示超限。"""
    now = time.time()
    with _lock:
        ts = _rate.get(visitor, [])
        ts = [x for x in ts if now - x < RATE_WINDOW]
        if len(ts) >= RATE_LIMIT:
            _rate[visitor] = ts
            return False
        ts.append(now)
        _rate[visitor] = ts
        return True


def cache_get(visitor, target):
    with _lock:
        v = _cache.get((visitor, target))
        if v and time.time() - v[0] < CACHE_TTL:
            return v[1]
    return None


def notify_bark(msg):
    """缓存超限时通过 Bark 推送告警。失败静默。"""
    try:
        url = "https://api.day.app/rfTC6UmEwpBnhFt5fzBHMG/" + urllib.parse.quote("IP-Lookup 缓存超限: " + msg)
        urllib.request.urlopen(url, timeout=5).read()
    except Exception:
        pass


def cache_set(visitor, target, result):
    with _lock:
        if len(_cache) > 2000:  # 防止内存膨胀，粗略清理
            cutoff = time.time() - CACHE_TTL
            for k in list(_cache.keys()):
                if _cache[k][0] < cutoff:
                    del _cache[k]
            if len(_cache) > 2000:  # 清理后仍超限 → 告警
                notify_bark("清理后仍剩 %d 条" % len(_cache))
        _cache[(visitor, target)] = (time.time(), result)

CARRIERS = ["移动", "联通", "电信", "铁通", "广电", "长城", "教育网", "鹏博士"]


def is_valid_ip(s):
    try:
        socket.inet_aton(s)
        return True
    except (OSError, ValueError):
        return False


def client_ip(handler):
    xff = handler.headers.get("X-Forwarded-For", "")
    if xff:
        first = xff.split(",")[0].strip()
        if is_valid_ip(first):
            return first
    return handler.client_address[0]


def extract_isp(addr):
    for c in CARRIERS:
        if c in addr:
            return c
    return ""


def _get(url, timeout=6, ua="ip-lookup/1.0"):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def lookup_pconline(ip):
    try:
        raw = _get("https://whois.pconline.com.cn/ipJson.jsp?ip=" + ip + "&json=true")
        d = json.loads(raw.decode("gbk", errors="replace").strip())
        if d.get("err") and not d.get("addr"):
            return None
        addr = d.get("addr", "").strip()
        pro = d.get("pro", "").strip()
        city = d.get("city", "").strip()
        if not pro and not city:
            return {"status": "success", "query": ip, "country": addr.replace(" ", "") or "Unknown",
                    "countryCode": "", "region": "", "city": "", "addr": addr, "isp": "", "org": "",
                    "as": "", "lat": None, "lon": None, "timezone": None, "mobile": False, "proxy": False,
                    "hosting": False, "source": "pconline"}
        return {"status": "success", "query": d.get("ip", ip), "country": "中国", "countryCode": "CN",
                "region": pro, "city": city, "addr": addr, "isp": extract_isp(addr), "org": "", "as": "",
                "lat": None, "lon": None, "timezone": "Asia/Shanghai", "mobile": False, "proxy": False,
                "hosting": False, "source": "pconline"}
    except Exception:
        return None


def lookup_ipapi(ip):
    try:
        d = json.loads(_get("https://ipapi.co/" + ip + "/json/", timeout=8).decode())
        if d.get("error"):
            return None
        return {"status": "success", "query": d.get("ip", ip), "country": d.get("country_name"),
                "countryCode": d.get("country_code"), "region": d.get("region"), "city": d.get("city"),
                "addr": "", "isp": d.get("org"), "org": d.get("org"), "as": d.get("asn"),
                "lat": d.get("latitude"), "lon": d.get("longitude"), "timezone": d.get("timezone"),
                "mobile": False, "proxy": False, "hosting": False, "source": "ipapi.co"}
    except Exception:
        return None


def lookup(ip):
    ip = (ip or "").strip()
    if ip and not is_valid_ip(ip):
        return {"status": "fail", "message": "Invalid IP format"}
    if not ip:
        return {"status": "fail", "message": "No IP"}
    # pconline 仅对国内 IP 精确；国外 IP 数据不完整（无城市/ISP/坐标），降级 ipapi.co
    r = lookup_pconline(ip)
    if r and r.get("countryCode") == "CN" and (r.get("city") or r.get("region")):
        return r
    r = lookup_ipapi(ip)
    if r:
        return r
    # ipapi 也失败时，兜底返回 pconline 的至少有国家信息的结果
    if r and r.get("country"):
        return r
    return {"status": "fail", "message": "All sources unreachable"}


def log_query(visitor, target, result):
    try:
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "visitor": visitor, "target": target,
               "status": result.get("status"), "country": result.get("country"),
               "countryCode": result.get("countryCode"), "region": result.get("region"),
               "city": result.get("city"), "addr": result.get("addr"), "isp": result.get("isp"),
               "as": result.get("as"), "source": result.get("source"), "mobile": result.get("mobile"),
               "proxy": result.get("proxy"), "hosting": result.get("hosting")}
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_history(limit=200):
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        return [json.loads(x) for x in lines if x.strip()]
    except FileNotFoundError:
        return []
    except Exception:
        return []


INDEX_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1.0,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#f4efe8">
<title>IP Lookup</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{font-size:16px;-webkit-font-smoothing:antialiased}
body{font-family:"Space Grotesk","IBM Plex Sans","Avenir Next",-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;min-height:100vh;background:#f4efe8;color:#2d2218;position:relative;overflow-x:hidden;padding:24px 20px}
.bg-image{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);width:100%;height:auto;opacity:.32;z-index:0;pointer-events:none;display:block;-webkit-mask-image:radial-gradient(ellipse 75% 75% at center,#000 35%,transparent 78%);mask-image:radial-gradient(ellipse 75% 75% at center,#000 35%,transparent 78%)}
.bg-gradient{position:fixed;inset:0;background:radial-gradient(circle at 12% 8%,#fff8ef 0%,#f4efe8 55%);pointer-events:none;z-index:0}
.bg-shape{position:fixed;z-index:0;border-radius:999px;filter:blur(26px);opacity:.5}
.shape-a{width:280px;height:280px;right:8%;top:7%;background:#80d8be}
.shape-b{width:340px;height:340px;left:-80px;bottom:-90px;background:#f0c99d}
.bg-grid{position:fixed;inset:0;background-image:linear-gradient(rgba(219,205,191,.3) 1px,transparent 1px),linear-gradient(90deg,rgba(219,205,191,.3) 1px,transparent 1px);background-size:50px 50px;pointer-events:none;z-index:0}
.wrap{position:relative;z-index:1;max-width:560px;margin:0 auto;animation:fadeInUp .6s ease-out}
@keyframes fadeInUp{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:none}}
.lang-switch{position:absolute;top:-4px;right:0;display:flex;gap:4px;z-index:2}
.lang-btn{padding:5px 11px;font-size:12px;font-weight:600;border:1px solid #dbcdbf;background:#fffdf8;color:#7f6a56;border-radius:8px;cursor:pointer;transition:all .2s;font-family:inherit}
.lang-btn:hover{border-color:#0e8c6a;color:#0e8c6a}
.lang-btn.active{background:#0e8c6a;color:#fff;border-color:#0e8c6a}
.card{background:linear-gradient(140deg,#fffdf8 0%,#f8f4ed 100%);backdrop-filter:blur(20px);border-radius:16px;padding:36px;box-shadow:0 18px 38px rgba(40,24,9,.12);border:1px solid #dbcdbf;margin-bottom:18px}
.logo{text-align:center;margin-bottom:28px}
.logo-icon{width:52px;height:52px;margin:0 auto 14px;background:linear-gradient(140deg,#0e8c6a 0%,#066a52 100%);border-radius:13px;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 12px rgba(14,140,106,.3)}
.logo-icon svg{width:26px;height:26px;fill:#fff}
.logo h1{font-size:22px;font-weight:700;color:#2d2218;letter-spacing:.01em;margin-bottom:4px}
.logo p{font-size:13px;color:#7f6a56}
.search{display:flex;gap:10px;margin-bottom:8px}
.search-input-wrap{position:relative;flex:1}
.search-input{width:100%;padding:14px 16px 14px 44px;font-size:15px;border:1.5px solid #dbcdbf;border-radius:12px;background:#fffdf8;transition:all .2s;outline:none;color:#2d2218;font-family:inherit}
.search-input:hover{border-color:#d7c6b4;background:#f8f4ed}
.search-input:focus{border-color:#0e8c6a;background:#fff;box-shadow:0 0 0 3px rgba(14,140,106,.1)}
.search-input::placeholder{color:#7f6a56}
.search-icon{position:absolute;left:14px;top:50%;transform:translateY(-50%);width:20px;height:20px;fill:#7f6a56;pointer-events:none}
.btn{padding:14px 22px;font-size:15px;font-weight:600;color:#fff;background:linear-gradient(140deg,#0e8c6a 0%,#066a52 100%);border:none;border-radius:12px;cursor:pointer;transition:all .2s;box-shadow:0 4px 12px rgba(14,140,106,.3);font-family:inherit;white-space:nowrap}
.btn:hover{transform:translateY(-1px);box-shadow:0 6px 16px rgba(14,140,106,.4)}
.btn:active{transform:translateY(0)}
.hint{text-align:center;font-size:12px;color:#7f6a56;margin-bottom:20px}
.result-area{min-height:120px}
.loading{text-align:center;padding:40px 0;color:#7f6a56}
.spinner{display:inline-block;width:32px;height:32px;border:3px solid #dbcdbf;border-top-color:#0e8c6a;border-radius:50%;animation:spin .8s linear infinite;margin-bottom:10px}
@keyframes spin{to{transform:rotate(360deg)}}
.err{text-align:center;color:#af3b2c;padding:30px 0}
.ip-block{text-align:center;margin-bottom:22px}
.flag{font-size:48px;line-height:1;margin-bottom:8px;filter:drop-shadow(0 4px 8px rgba(40,24,9,.15))}
.ip-val{font-size:28px;font-weight:700;color:#2d2218;letter-spacing:-.5px;font-variant-numeric:tabular-nums;cursor:pointer;display:inline-block;position:relative;transition:color .2s}
.ip-val:hover{color:#0e8c6a}
.ip-val .copied{position:absolute;left:50%;top:-22px;transform:translateX(-50%);font-size:11px;color:#066a52;background:#fffdf8;padding:2px 10px;border-radius:6px;border:1px solid #dbcdbf;opacity:0;transition:opacity .2s;white-space:nowrap}
.ip-val.copied .copied{opacity:1}
.loc-line{font-size:15px;color:#7f6a56;margin-top:6px}
.loc-line b{color:#2d2218;font-weight:600}
.badges{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-bottom:20px}
.badge{padding:5px 13px;border-radius:20px;font-size:12px;font-weight:600;background:rgba(14,140,106,.1);color:#0e8c6a;border:1px solid rgba(14,140,106,.2)}
.badge.proxy{background:rgba(175,59,44,.1);color:#af3b2c;border-color:rgba(175,59,44,.2)}
.divider{height:1px;background:#dbcdbf;margin:20px 0}
.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.field{background:#fffdf8;border:1px solid #dbcdbf;border-radius:11px;padding:13px 15px}
.field .k{font-size:11px;color:#7f6a56;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}
.field .v{font-size:14px;color:#2d2218;word-break:break-all;font-variant-numeric:tabular-nums}
.field .v a{color:#0e8c6a;text-decoration:none}
.field .v a:hover{text-decoration:underline}
.src-tag{text-align:center;font-size:11px;color:#7f6a56;margin-top:16px}
.src-tag code{background:rgba(14,140,106,.08);color:#0e8c6a;padding:1px 7px;border-radius:5px}
@media(max-width:480px){.card{padding:26px 18px}.flag{font-size:40px}.ip-val{font-size:24px}.search{flex-direction:column}.btn{width:100%}}
</style></head><body>
<div class="bg-gradient"></div><div class="bg-shape shape-a"></div><div class="bg-shape shape-b"></div><div class="bg-grid"></div><img class="bg-image" src="/bg" alt="">

<div class="wrap">
  <div class="lang-switch">
    <button class="lang-btn" data-lang="en">EN</button>
    <button class="lang-btn" data-lang="zh">中文</button>
  </div>
  <div class="card">
    <div class="logo">
      <div class="logo-icon"><svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93C7.05 19.44 4 16.08 4 12c0-.61.08-1.21.21-1.78L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41C17.93 5.78 20 8.65 20 12c0 2.08-.81 3.98-2.1 5.39z"/></svg></div>
      <h1 data-i18n="title">IP Lookup</h1>
      <p data-i18n="tagline">Geolocation · ISP · Network Info</p>
    </div>

    <div class="search">
      <div class="search-input-wrap">
        <svg class="search-icon" viewBox="0 0 24 24"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>
        <input id="q" class="search-input" type="text" data-i18n-ph="ph_input" placeholder="Enter an IP address (empty = yours)" autocomplete="off" autofocus>
      </div>
      <button id="go" class="btn" data-i18n="btn_query">Lookup</button>
    </div>
    <div class="hint" id="hint" data-i18n="hint">Accurate for China IPs · Sources: pconline / ipapi.co</div>
  </div>

  <div class="card result-area" id="result"><div class="loading"><div class="spinner"></div><br><span data-i18n="loading">Loading…</span></div></div>
</div>

<script>
var I18N={
  en:{title:"IP Lookup",tagline:"Geolocation · ISP · Network Info",ph_input:"Enter an IP address (empty = yours)",btn_query:"Lookup",hint:"Accurate for China IPs · Sources: pconline / ipapi.co",loading:"Loading…",searching:"Looking up…",failed:"Lookup failed",neterr:"Network error, please retry",f_country:"Country / Region",f_region:"Region / State",f_city:"City",f_isp:"ISP",f_addr:"Location",f_org:"Org / ASN",f_tz:"Timezone",f_coord:"Coordinates",f_map:"Map",view_map:"View on OpenStreetMap →",source:"Source",badge_proxy:"Proxy / VPN",your_ip:"Your IP:",copied:"Copied",unknown:"Unknown"},
  zh:{title:"IP 查询",tagline:"归属地 · 运营商 · 地理信息",ph_input:"输入 IP 地址（留空查自己）",btn_query:"查询",hint:"国内 IP 精确定位 · 数据来源 pconline / ipapi.co",loading:"加载中…",searching:"查询中…",failed:"查询失败",neterr:"网络错误，请重试",f_country:"国家 / 地区",f_region:"省 / 州",f_city:"城市",f_isp:"运营商",f_addr:"归属地",f_org:"组织 / ASN",f_tz:"时区",f_coord:"经纬度",f_map:"地图",view_map:"在 OpenStreetMap 查看 →",source:"数据来源",badge_proxy:"代理 / VPN",your_ip:"你的 IP：",copied:"已复制",unknown:"未知"}
};
var ISP_MAP={"移动":"China Mobile","联通":"China Unicom","电信":"China Telecom","铁通":"China Tietong","广电":"China Broadcast","长城":"Great Wall Broadband","教育网":"CERNET","鹏博士":"Dr. Peng"};
var curLang=localStorage.getItem("ip-lang")||"en";
function t(k){return (I18N[curLang]||I18N.en)[k]||k}
function H(n){return document.getElementById(n)}
function flag(cc){if(!cc||cc.length!==2)return "";var A=0x1F1E6-65;return String.fromCodePoint(A+cc.charCodeAt(0),A+cc.charCodeAt(1))}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function mapCountry(c){return (curLang==="en"&&c==="中国")?"China":(curLang==="zh"&&c==="China")?"中国":c}
function mapIsp(s){if(!s)return s;if(curLang==="en")return ISP_MAP[s]||s;return s}
function applyLang(lang){
  curLang=lang;localStorage.setItem("ip-lang",lang);
  document.documentElement.lang=lang==="zh"?"zh-CN":"en";
  document.querySelectorAll("[data-i18n]").forEach(function(el){var v=I18N[lang][el.getAttribute("data-i18n")];if(v)el.textContent=v});
  document.querySelectorAll("[data-i18n-ph]").forEach(function(el){var v=I18N[lang][el.getAttribute("data-i18n-ph")];if(v)el.placeholder=v});
  document.querySelectorAll(".lang-btn").forEach(function(b){b.classList.toggle("active",b.getAttribute("data-lang")===lang)});
}
document.querySelectorAll(".lang-btn").forEach(function(b){b.addEventListener("click",function(){applyLang(b.getAttribute("data-lang"));if(lastResult)render(lastResult)})});
var lastResult=null;
function render(d){
  lastResult=d;
  if(d.status!=="success"){H("result").innerHTML='<div class="err">'+esc(d.message||t("failed"))+'</div>';return}
  var country=mapCountry(d.country);
  var loc=[country,mapCountry(d.region),mapCountry(d.city)].filter(function(x){return x}).join(curLang==="zh"?" · ":" · ");
  var isp=mapIsp(d.isp);
  var org=[mapIsp(d.org),d.as].filter(function(x){return x}).join(" · ");
  var addr=curLang==="en"?mapIsp(d.addr):d.addr;
  var badges="";
  if(d.proxy)badges+='<span class="badge proxy">'+t("badge_proxy")+'</span>';
  var fields=[
    f(t("f_country"),(country||t("unknown"))+(d.countryCode?" ("+d.countryCode+")":"")),
    f(t("f_region"),mapCountry(d.region)||"-"),f(t("f_city"),mapCountry(d.city)||"-"),
    f(t("f_isp"),isp||"-"),f(t("f_addr"),addr||"-"),
    f(t("f_org"),org||"-"),f(t("f_tz"),d.timezone||"-"),
    f(t("f_coord"),d.lat&&d.lon?(d.lat+", "+d.lon):"-")
  ].join("");
  var map=(d.lat&&d.lon)?'<div class="field" style="grid-column:1/-1"><div class="k">'+t("f_map")+'</div><div class="v"><a target="_blank" href="https://www.openstreetmap.org/?mlat='+d.lat+'&mlon='+d.lon+'#map=10/'+d.lat+'/'+d.lon+'">'+t("view_map")+'</a></div></div>':"";
  H("result").innerHTML='<div class="ip-block"><div class="flag">'+(flag(d.countryCode)||"🌐")+'</div><div class="ip-val" id="ipv">'+esc(d.query)+'<span class="copied">'+t("copied")+'</span></div><div class="loc-line"><b>'+esc(loc||t("unknown"))+'</b></div></div>'+(badges?'<div class="badges">'+badges+'</div>':'')+'<div class="divider"></div><div class="fields">'+fields+map+'</div><div class="src-tag">'+t("source")+' <code>'+esc(d.source||"?")+'</code></div>';
  var ie=H("ipv");if(ie)ie.addEventListener("click",function(){copy(d.query,ie)});
  function f(k,v){return '<div class="field"><div class="k">'+k+'</div><div class="v">'+esc(v)+'</div></div>'}
}
function copy(txt,el){navigator.clipboard.writeText(txt).then(function(){if(el){el.classList.add("copied");setTimeout(function(){el.classList.remove("copied")},1200)}})}
function doLookup(){
  var ip=H("q").value.trim();
  H("result").innerHTML='<div class="loading"><div class="spinner"></div><br>'+t("searching")+'</div>';
  fetch("/api/lookup"+(ip?("?ip="+encodeURIComponent(ip)):"")).then(function(r){return r.json()}).then(function(d){render(d);if(d.status==="success"&&!ip)H("hint").textContent=t("your_ip")+" "+d.query}).catch(function(){H("result").innerHTML='<div class="err">'+t("neterr")+'</div>'})
}
H("go").addEventListener("click",doLookup);
H("q").addEventListener("keydown",function(e){if(e.key==="Enter")doLookup()});
applyLang(curLang);doLookup();
</script>
</body></html>"""


HISTORY_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1.0,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#f4efe8">
<title>Query History</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{font-size:16px;-webkit-font-smoothing:antialiased}
body{font-family:"Space Grotesk","IBM Plex Sans","Avenir Next",-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;min-height:100vh;background:#f4efe8;color:#2d2218;padding:24px 20px;position:relative;overflow-x:hidden}
.bg-image{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);width:100%;height:auto;opacity:.26;z-index:0;pointer-events:none;display:block;-webkit-mask-image:radial-gradient(ellipse 75% 75% at center,#000 35%,transparent 78%);mask-image:radial-gradient(ellipse 75% 75% at center,#000 35%,transparent 78%)}
.bg-gradient{position:fixed;inset:0;background:radial-gradient(circle at 12% 8%,#fff8ef 0%,#f4efe8 55%);pointer-events:none;z-index:0}
.bg-shape{position:fixed;z-index:0;border-radius:999px;filter:blur(26px);opacity:.45}
.shape-a{width:260px;height:260px;right:6%;top:5%;background:#80d8be}
.shape-b{width:320px;height:320px;left:-70px;bottom:-80px;background:#f0c99d}
.bg-grid{position:fixed;inset:0;background-image:linear-gradient(rgba(219,205,191,.3) 1px,transparent 1px),linear-gradient(90deg,rgba(219,205,191,.3) 1px,transparent 1px);background-size:50px 50px;pointer-events:none;z-index:0}
.wrap{position:relative;z-index:1;max-width:1080px;margin:0 auto;animation:fadeIn .5s ease-out}
@keyframes fadeIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:12px}
header h1{font-size:22px;font-weight:700;color:#2d2218;display:flex;align-items:center;gap:10px}
header h1 .icon{width:38px;height:38px;background:linear-gradient(140deg,#0e8c6a,#066a52);border-radius:10px;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 10px rgba(14,140,106,.25)}
header h1 .icon svg{width:20px;height:20px;fill:#fff}
.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.stat{font-size:13px;color:#7f6a56;margin-right:6px}
.lang-switch{display:flex;gap:4px}
.lang-btn{padding:5px 11px;font-size:12px;font-weight:600;border:1px solid #dbcdbf;background:#fffdf8;color:#7f6a56;border-radius:8px;cursor:pointer;transition:all .2s;font-family:inherit}
.lang-btn:hover{border-color:#0e8c6a;color:#0e8c6a}
.lang-btn.active{background:#0e8c6a;color:#fff;border-color:#0e8c6a}
.btn{padding:8px 16px;font-size:13px;font-weight:600;font-family:inherit;border-radius:10px;cursor:pointer;transition:.2s;text-decoration:none;border:1.5px solid #dbcdbf;background:#fffdf8;color:#7f6a56}
.btn:hover{border-color:#0e8c6a;color:#0e8c6a;background:#f8f4ed}
.btn-primary{background:linear-gradient(140deg,#0e8c6a,#066a52);color:#fff;border:none;box-shadow:0 4px 10px rgba(14,140,106,.25)}
.btn-primary:hover{transform:translateY(-1px);color:#fff;background:linear-gradient(140deg,#0e8c6a,#066a52)}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
.sc{background:linear-gradient(140deg,#fffdf8,#f8f4ed);border:1px solid #dbcdbf;border-radius:13px;padding:16px;box-shadow:0 6px 16px rgba(40,24,9,.06)}
.sc .n{font-size:24px;font-weight:700;color:#0e8c6a;font-variant-numeric:tabular-nums}
.sc .l{font-size:11px;color:#7f6a56;margin-top:3px;text-transform:uppercase;letter-spacing:.5px}
.table-card{background:linear-gradient(140deg,#fffdf8,#f8f4ed);border:1px solid #dbcdbf;border-radius:16px;overflow:hidden;box-shadow:0 18px 38px rgba(40,24,9,.1)}
table{width:100%;border-collapse:collapse}
th{text-align:left;color:#7f6a56;padding:13px 14px;border-bottom:1.5px solid #dbcdbf;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px;background:rgba(255,253,248,.6)}
td{padding:11px 14px;border-bottom:1px solid rgba(219,205,191,.4);font-size:13px;vertical-align:top}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(14,140,106,.04)}
.mono{font-variant-numeric:tabular-nums;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.loc{color:#0e8c6a;font-weight:500}
.empty{text-align:center;color:#7f6a56;padding:60px 20px}
.flag{font-size:15px;margin-right:3px}
.loading{text-align:center;padding:50px;color:#7f6a56}
.spinner{display:inline-block;width:28px;height:28px;border:3px solid #dbcdbf;border-top-color:#0e8c6a;border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
footer{text-align:center;color:#7f6a56;font-size:11px;margin-top:24px}
@media(max-width:640px){.hide-sm{display:none}td,th{padding:8px 10px}}
</style></head><body>
<div class="bg-gradient"></div><div class="bg-shape shape-a"></div><div class="bg-shape shape-b"></div><div class="bg-grid"></div><img class="bg-image" src="/bg" alt="">

<div class="wrap">
  <header>
    <h1><span class="icon"><svg viewBox="0 0 24 24"><path d="M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/></svg></span><span data-i18n="title">Query History</span></h1>
    <div class="actions">
      <div class="lang-switch">
        <button class="lang-btn" data-lang="en">EN</button>
        <button class="lang-btn" data-lang="zh">中文</button>
      </div>
      <span class="stat" id="stat">--</span>
      <a class="btn" href="/" data-i18n="back">Back</a>
      <button class="btn btn-primary" id="refresh" data-i18n="refresh">Refresh</button>
    </div>
  </header>

  <div class="summary" id="summary"></div>
  <div id="twrap"><div class="loading"><div class="spinner"></div><br><span data-i18n="loading">Loading…</span></div></div>
  <footer data-i18n="footer">Query log persisted server-side as JSONL</footer>
</div>
<script>
var I18N={
  en:{title:"Query History",loading:"Loading…",empty:"No queries yet",failed:"Failed to load",stat_total:"Total Queries",stat_visitors:"Unique Visitors",stat_proxy:"Proxy / VPN",stat_top:"Top Region",th_time:"Time",th_visitor:"Visitor IP",th_target:"Query IP",th_region:"Region",th_isp:"ISP / AS",th_source:"Source",back:"Back",refresh:"Refresh",n_records:function(n){return n+" records"},footer:"Query log persisted server-side as JSONL",china:"China"},
  zh:{title:"查询历史",loading:"加载中…",empty:"暂无查询记录",failed:"加载失败",stat_total:"总查询",stat_visitors:"独立访客",stat_proxy:"代理/VPN",stat_top:"Top 地区",th_time:"时间",th_visitor:"访客 IP",th_target:"查询 IP",th_region:"地区",th_isp:"运营商 / AS",th_source:"来源",back:"返回查询",refresh:"刷新",n_records:function(n){return "共 "+n+" 条"},footer:"查询记录持久化存储于服务端 JSONL 文件",china:"中国"}
};
var ISP_MAP={"移动":"China Mobile","联通":"China Unicom","电信":"China Telecom","铁通":"China Tietong","广电":"China Broadcast","长城":"Great Wall Broadband","教育网":"CERNET","鹏博士":"Dr. Peng"};
var curLang=localStorage.getItem("ip-lang")||"en";
function t(k){var v=(I18N[curLang]||I18N.en)[k];return typeof v==="function"?v:v||k}
function tf(k){var v=(I18N[curLang]||I18N.en)[k];return typeof v==="function"?v:function(){return k}}
function H(n){return document.getElementById(n)}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function flag(cc){if(!cc||cc.length!==2)return "";var A=0x1F1E6-65;return String.fromCodePoint(A+cc.charCodeAt(0),A+cc.charCodeAt(1))}
function mapCountry(c){if(!c)return c;if(curLang==="en")return c==="中国"?"China":c;return c==="China"?"中国":c}
function mapIsp(s){if(!s)return s;if(curLang==="en")return ISP_MAP[s]||s;return s}
function applyLang(lang){
  curLang=lang;localStorage.setItem("ip-lang",lang);
  document.documentElement.lang=lang==="zh"?"zh-CN":"en";
  document.querySelectorAll("[data-i18n]").forEach(function(el){var k=el.getAttribute("data-i18n");var v=(I18N[lang]||I18N.en)[k];if(typeof v==="string")el.textContent=v});
  document.querySelectorAll(".lang-btn").forEach(function(b){b.classList.toggle("active",b.getAttribute("data-lang")===lang)});
}
document.querySelectorAll(".lang-btn").forEach(function(b){b.addEventListener("click",function(){applyLang(b.getAttribute("data-lang"));load()})});
function load(){
  H("twrap").innerHTML='<div class="loading"><div class="spinner"></div><br>'+t("loading")+'</div>';
  fetch("/api/history?limit=300").then(function(r){return r.json()}).then(function(d){
    var recs=(d.records||[]).reverse();
    H("stat").textContent=tf("n_records")(recs.length);
    if(!recs.length){H("twrap").innerHTML='<div class="table-card"><div class="empty">'+t("empty")+'</div></div>';H("summary").innerHTML="";return}
    var countries={},visitors={},proxy=0;
    recs.forEach(function(r){var c=mapCountry(r.country);if(c)countries[c]=(countries[c]||0)+1;if(r.visitor)visitors[r.visitor]=1;if(r.proxy)proxy++});
    var topC=Object.keys(countries).sort(function(a,b){return countries[b]-countries[a]})[0];
    function sc(l,v){return '<div class="sc"><div class="n">'+esc(v)+'</div><div class="l">'+l+'</div></div>'}
    H("summary").innerHTML=[sc(t("stat_total"),recs.length),sc(t("stat_visitors"),Object.keys(visitors).length),sc(t("stat_proxy"),proxy),sc(t("stat_top"),topC||"-")].join("");
    var rows=recs.map(function(r){
      var loc=[mapCountry(r.country),mapCountry(r.region),mapCountry(r.city)].filter(function(x){return x}).join(" / ");
      var isp=curLang==="en"?mapIsp(r.addr||r.isp):(r.addr||r.isp);
      return "<tr><td class='mono'>"+esc(r.ts||"-")+"</td><td class='mono hide-sm'>"+esc(r.visitor||"-")+"</td><td class='mono'>"+esc(r.target||"-")+"</td><td class='loc'>"+(r.countryCode?flag(r.countryCode)+" ":"")+esc(loc||"-")+"</td><td class='hide-sm'>"+esc(isp||"-")+(r.as?"<br><small style='color:#7f6a56'>"+esc(r.as)+"</small>":"")+"</td><td>"+esc(r.source||"-")+"</td></tr>";
    }).join("");
    H("twrap").innerHTML='<div class="table-card"><table><thead><tr><th>'+t("th_time")+'</th><th class="hide-sm">'+t("th_visitor")+'</th><th>'+t("th_target")+'</th><th>'+t("th_region")+'</th><th class="hide-sm">'+t("th_isp")+'</th><th>'+t("th_source")+'</th></tr></thead><tbody>'+rows+'</tbody></table></div>';
  }).catch(function(){H("twrap").innerHTML='<div class="table-card"><div class="empty">'+t("failed")+'</div></div>'})
}
H("refresh").addEventListener("click",load);
applyLang(curLang);load();
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode())
        elif parsed.path == "/api/lookup":
            qs = urllib.parse.parse_qs(parsed.query)
            ip = (qs.get("ip", [""])[0]).strip()
            visitor = client_ip(self)
            if not ip:
                ip = visitor
            if not check_rate(visitor):
                self._send(429, "application/json; charset=utf-8",
                           json.dumps({"status": "fail", "message": "Too many requests, slow down"}).encode())
                return
            cached = cache_get(visitor, ip)
            if cached:
                self._send(200, "application/json; charset=utf-8", json.dumps(cached).encode())
                return
            result = lookup(ip)
            log_query(visitor, ip, result)
            cache_set(visitor, ip, result)
            self._send(200, "application/json; charset=utf-8", json.dumps(result).encode())
        elif parsed.path == "/api/history":
            qs = urllib.parse.parse_qs(parsed.query)
            limit = int(qs.get("limit", ["200"])[0])
            data = json.dumps({"records": read_history(limit)}).encode()
            self._send(200, "application/json; charset=utf-8", data)
        elif parsed.path == "/history":
            self._send(200, "text/html; charset=utf-8", HISTORY_HTML.encode())
        elif parsed.path == "/bg":
            try:
                with open(BG_FILE, "rb") as f:
                    data = f.read()
                self._send(200, "image/png", data, cache="public, max-age=86400")
            except Exception:
                self.send_error(404)
        else:
            self.send_error(404)

    def _send(self, code, ctype, data, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("OK http://127.0.0.1:%d" % PORT)
    srv.serve_forever()
