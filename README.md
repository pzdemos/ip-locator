# IP Locator

> Accurate IP geolocation with a warm, creamy-green UI. Single-file Python service, zero dependencies.

[![Live Demo](https://img.shields.io/badge/Live-Demo-0e8c6a?style=flat-square)](https://ip.haoaiganfan.top)
[![Python](https://img.shields.io/badge/Python-3.7+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org)

Self-hosted IP lookup with high accuracy for China IPs (pconline primary, ipapi.co fallback). EN / 中文 toggle, ~30KB single file, standard library only.

## Quick Start
<img width="599" height="387" alt="image" src="https://github.com/user-attachments/assets/9e26db59-eb30-4852-933c-2ed2f3645319" />

```bash
python3 app.py              # http://localhost:8082
PORT=9000 python3 app.py    # custom port
```

## Features

- Dual data sources (pconline + ipapi.co) with smart carrier detection
- EN / 中文 in-page toggle, persisted via localStorage
- Responsive mobile-friendly design

## License

MIT
