# AI Outreach Automation — Context

## Tujuan
Sistem otomatis untuk:
1. Mencari UMKM (terutama di Kalimantan) yang **belum punya website**.
2. Mengirim pesan WhatsApp personal untuk menawarkan jasa pembuatan website.

## Stack
- **Python** (backend logic — scraper, builder, tracker)
- **Vanilla JS/HTML/CSS** (dashboard, tanpa framework/build tool)
- **Groq API** (LLM untuk generate pesan, model: `llama-3.1-8b-instant`)
- Tidak ada database eksternal — storage pakai file JSON (`data/leads.json`, `data/sent.json`)

## Aturan Penting
- **Jangan pakai library yang butuh install ribet** (no Selenium/Playwright/Docker/Node toolchain).
  Cukup `requests`, `beautifulsoup4`, `python-dotenv`, `groq`.
- Kode harus bersih, modular, dan **komentar pakai Bahasa Indonesia**.
- Setiap script di `agents/` harus bisa dijalankan standalone (`python agents/scraper.py`) maupun diimpor dari `main.py`.
- Pesan WA yang digenerate harus **terasa natural**, bukan template kaku — variasikan kalimat pembuka, jangan selalu "Halo, saya melihat...".

## Target Audiens
- UMKM di Kalimantan (kota seperti Berau, Samarinda, Balikpapan, dll).
- Kategori bisnis bervariasi: toko sembako, bengkel, rumah makan, dll.
- Fokus ke bisnis yang terdaftar di Google Maps tapi kolom website-nya kosong.

## Catatan Teknis / Limitasi
- Google Maps modern banyak di-render via JavaScript. Scraping dengan `requests` + `BeautifulSoup` saja **tidak akan selengkap** scraping dengan browser otomatis (Selenium/Playwright).
  Trade-off ini disengaja sesuai aturan "jangan ribet install" — scraper akan ambil apa yang bisa didapat dari HTML/initial data Google Maps, dan kalau hasil terbatas, fallback ke pencarian Google biasa.
- Nomor WA di Google Maps biasanya berupa nomor telepon biasa — perlu normalisasi ke format `62xxxxxxxxxx` sebelum dipakai di link `wa.me`.

## Status Lead (dipakai tracker.py)
- `baru` — lead ditemukan, belum diproses builder
- `pending` — pesan sudah dibuat, belum dikirim
- `sent` — pesan sudah dikirim (tanggal_kirim dicatat otomatis)
- `followup_due` — sudah lewat N hari sejak dikirim, belum ada respons, siap di-follow-up (ditandai tracker.cek_followup(), lalu diproses ulang oleh builder.py jadi pesan follow-up)
- `replied` — UMKM merespon
- `bounced` — nomor tidak valid / tidak bisa dihubungi
- `closed` — deal selesai / tidak lanjut

## Loop Engineering (upgrade)
Alur lengkap sekarang membentuk loop, bukan cuma sekali jalan:
1. `python main.py merge` — gabungkan data/priority_with_phones.json (klinik & hotel prioritas) ke leads.json, tanpa duplikat.
2. `python main.py build` — Think: generate pesan lewat Groq, prioritas klinik/hotel diproses duluan, dibatasi budget GROQ_MAX_CALLS_PER_RUN per run.
3. `python main.py serve` — buka dashboard, klik "Hubungi via WA": Act (buka WA Web) sekaligus Observe (status otomatis jadi `sent` + tanggal_kirim lewat POST /api/update-status, tidak perlu ketik manual lagi).
4. `python main.py followup` — Observe: cek lead `sent` yang sudah lewat 3 hari tanpa respons, tandai `followup_due`.
5. Ulangi `python main.py build` — lead `followup_due` otomatis dapat pesan follow-up baru (bukan pesan pembuka lagi), lalu balik ke langkah 3.

Agent baru: `agents/utils.py` (klasifikasi kategori klinik/hotel/lainnya + urutan prioritas) dan `agents/merge_leads.py`.
