# IP Locator

> Accurate IP geolocation with a warm, creamy-green UI. Single-file Python service, zero dependencies.

[![Live Demo](https://img.shields.io/badge/Live-Demo-0e8c6a?style=flat-square)](https://ip.haoaiganfan.top)
[![Python](https://img.shields.io/badge/Python-3.7+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![No Dependencies](https://img.shields.io/badge/Dependencies-Zero-lightgrey?style=flat-square)]()

Self-hosted IP lookup service with **high accuracy for China IPs** by combining multiple data sources. Designed with a soft, elegant aesthetic — not the usual dark dashboard.

## ✨ Features

- **Dual data sources** — pconline (primary, accurate for mainland China) + ipapi.co (fallback, global)
- **Smart carrier detection** — recognizes China Mobile / Unicom / Telecom etc.
- **EN / 中文** — toggle in-page, preference persisted via `localStorage`
- **Query history** — server-side JSONL log with a dedicated analytics page
- **Self-contained** — one file, ~30KB, standard library only (no pip install)
- **Responsive** — works on desktop and mobile

## 🌐 Live Demo

| Page | URL |
|------|-----|
| Lookup | <https://ip.haoaiganfan.top> |
| History | <https://ip.haoaiganfan.top/history> |

## 📸 Screenshot

<!-- Replace the placeholder below with a real screenshot -->
<img src="https://ip.haoaiganfan.top/" width="560" alt="IP Locator interface">

## 🚀 Quick Start

```bash
# Run directly (listens on 127.0.0.1:8082 by default)
python3 app.py

# Custom port
PORT=9000 python3 app.py
```

Open <http://localhost:8082>.

### Production (systemd + nginx)

```bash
# 1. systemd unit
cat > /etc/systemd/system/ip-lookup.service <<'EOF'
[Unit]
Description=IP Lookup Service
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /path/to/app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now ip-lookup.service

# 2. nginx reverse proxy
location / {
    proxy_pass http://127.0.0.1:8082;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

## 📡 API

### `GET /api/lookup`

Query geolocation for an IP. Omit `ip` to look up the caller's IP.

```
GET /api/lookup?ip=8.8.8.8
GET /api/lookup            # auto-detect visitor IP
```

```json
{
  "status": "success",
  "query": "117.167.198.52",
  "country": "中国",
  "countryCode": "CN",
  "region": "江西省",
  "city": "吉安市",
  "addr": "江西省吉安市 移动",
  "isp": "移动",
  "lat": null,
  "lon": null,
  "timezone": "Asia/Shanghai",
  "source": "pconline"
}
```

### `GET /api/history?limit=200`

Returns recent query records (newest last).

### `GET /history`

HTML page with summary cards and a full query log table.

## 🎨 Design

| Token | Value |
|-------|-------|
| Background | `#f4efe8` (cream) |
| Accent | `#0e8c6a` → `#066a52` (emerald gradient) |
| Text | `#2d2218` |
| Aesthetic | blurred blobs, subtle grid, glass card |

## 🗂 Project Layout

```
ip-lookup/
├── app.py            # the entire service (HTML + CSS + JS + backend)
├── queries.jsonl     # runtime query log (auto-created, gitignored)
└── server.log        # runtime log (auto-created, gitignored)
```

## 📝 Notes

- **Rate limits** (upstream): pconline is generous; ipapi.co allows ~1000/day per IP on the free tier.
- **Privacy**: queries are logged to `queries.jsonl` (visitor IP, target IP, region). Disable by editing `log_query()`.
- **Accuracy**: mainland China IPs resolve to city + carrier level via pconline; international IPs fall back to ipapi.co.

## 📄 License

MIT
