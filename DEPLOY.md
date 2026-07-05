# Deploy ke Oracle Cloud Free Tier

Panduan setup bot supaya jalan 24/7 di VPS gratis (Oracle Cloud Always Free), tanpa harus laptop nyala terus.

## 1. Setup akun Oracle Cloud

- Daftar di oracle.com/cloud/free (butuh kartu untuk verifikasi identitas, tapi Always Free resources tidak dikenakan biaya selama tetap di batas free tier).
- Bikin Compute Instance: **Ubuntu 22.04 LTS**, shape **VM.Standard.E2.1.Micro** (1 OCPU / 1 GB RAM — masuk Always Free).
- Download SSH key (`.key` atau `.pem`) saat create instance, dan catat **IP publik** instance-nya.

## 2. SSH ke VPS

```bash
chmod 600 path/to/key.pem   # wajib, SSH nolak key dengan permission longgar
ssh -i path/to/key.pem ubuntu@<IP_PUBLIC>
```

## 3. Setup environment di VPS

```bash
sudo apt update && sudo apt install -y python3-pip python3-venv git sqlite3

cd ~
git clone https://github.com/elevatepoint05-droid/ai-outreach.git
cd ai-outreach
cp .env.example .env
```

Install dependency **dari `requirements.txt`**, bukan manual satu-satu — biar selalu sinkron sama kode:

```bash
pip3 install -r requirements.txt
```

> Catatan: `requirements.txt` isinya `requests`, `beautifulsoup4`, `python-dotenv`, `groq`, `reportlab`.
> Paket `python-dotenv` **wajib** ada — `agents/config.py` langsung `import` itu di baris pertama, kalau
> hilang bot gagal start sama sekali (`ModuleNotFoundError`). `python-telegram-bot` **tidak dipakai** —
> `agents/telegram_bot.py` pakai `requests` polling manual, jadi tidak perlu diinstall.
>
> Kalau Ubuntu lo kena error `externally-managed-environment` saat `pip3 install`, pakai virtualenv:
> `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt` — lalu sesuaikan
> `ExecStart` di systemd unit (langkah 5) ke `venv/bin/python3`.

## 4. Isi `.env`

```bash
nano .env
chmod 600 .env
```

Minimal yang wajib diisi:
- `GROQ_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `PORTFOLIO_URL`

Opsional tapi disarankan untuk mode otonom penuh (biar bot jalan sendiri tanpa perlu trigger manual):
- `ORCHESTRATOR_ENABLED=true`
- `LAPORAN_HARIAN_ENABLED=true` (default sudah true)

`chmod 600 .env` penting — file ini isinya token/API key, jangan bisa dibaca user lain di VPS.

### Data lead (leads.json / outreach.db)

`data/*.db`, `data/*.json` **sengaja tidak masuk git** (ada nomor WA & data bisnis asli — lihat `.gitignore`).
Artinya `git clone` di VPS ini akan mulai dari **database kosong**. Dua opsi:

1. **Copy data yang sudah ada dari laptop** — paling cepat kalau sudah ada leads:
   ```bash
   scp -i path/to/key.pem data/outreach.db ubuntu@<IP_PUBLIC>:~/ai-outreach/data/outreach.db
   ```
2. **Mulai dari nol** — biarkan kosong, jalankan scraper (`python3 main.py scrape`) langsung di VPS.

## 5. Setup systemd service (auto-start + auto-restart)

Buat `/etc/systemd/system/ai-outreach-bot.service`:

```ini
[Unit]
Description=AI Outreach Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/ai-outreach
ExecStart=/usr/bin/python3 /home/ubuntu/ai-outreach/main.py bot
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

> Kalau pakai virtualenv di langkah 3, ganti `ExecStart` jadi
> `/home/ubuntu/ai-outreach/venv/bin/python3 /home/ubuntu/ai-outreach/main.py bot`.

Aktifkan:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ai-outreach-bot
sudo systemctl start ai-outreach-bot
```

## 6. Verifikasi bot jalan

```bash
sudo systemctl status ai-outreach-bot
journalctl -u ai-outreach-bot -f
```

Kirim `/status` ke bot dari Telegram HP — harus ada balasan.

## Optional: Dashboard (`python main.py serve`)

Kalau nanti mau expose dashboard juga di VPS ini (bukan cuma bot Telegram):
- **Wajib** isi `DASHBOARD_PASSWORD` di `.env` dulu — default kosong = tanpa auth, jangan dipakai di VPS publik.
- Server bind ke `127.0.0.1` saja secara default (lihat `agents/config.py`) — untuk akses dari luar, pasang
  reverse proxy (nginx) dengan HTTPS di depannya. Jangan expose `PORT_DASHBOARD` langsung ke internet.
- Bikin systemd unit terpisah (`ai-outreach-dashboard.service`) dengan `ExecStart=... main.py serve`.

## Operasional harian

| Aksi | Command |
|---|---|
| Stop bot | `sudo systemctl stop ai-outreach-bot` |
| Start bot | `sudo systemctl start ai-outreach-bot` |
| Restart bot (setelah `git pull` update kode) | `sudo systemctl restart ai-outreach-bot` |
| Lihat log real-time | `journalctl -u ai-outreach-bot -f` |
| SSH ke VPS | `ssh -i key.pem ubuntu@<IP_PUBLIC>` |
| Update kode | `cd ~/ai-outreach && git pull && sudo systemctl restart ai-outreach-bot` |
