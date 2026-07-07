"""
from typing import Optional
config.py
=========
Satu-satunya tempat baca .env dan set default value.
Semua agent import konstanta dari sini — tidak perlu os.getenv() nyebar di
mana-mana dan tidak ada magic string duplikat.

Cara pakai:
    from agents.config import GROQ_API_KEY, GROQ_MAX_CALLS, KOTA_TARGET
    # atau standalone:
    from config import GROQ_API_KEY, GROQ_MAX_CALLS

Untuk mengubah config tanpa edit kode: isi .env (lihat .env.example).
"""

import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ── Groq API ──────────────────────────────────────────────────────────────────
GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL    = "llama-3.1-8b-instant"
GROQ_MAX_CALLS = int(os.getenv("GROQ_MAX_CALLS_PER_RUN", "40"))

# ── Portfolio ─────────────────────────────────────────────────────────────────
PORTFOLIO_URL = os.getenv("PORTFOLIO_URL", "").strip()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# ── Target outreach ───────────────────────────────────────────────────────────
# KOTA_TARGET di .env diisi koma-separated: "Berau,Bulungan,Malinau"
KOTA_TARGET = [
    k.strip()
    for k in os.getenv("KOTA_TARGET", "Berau").split(",")
    if k.strip()
]
DEFAULT_KATEGORI = os.getenv("DEFAULT_KATEGORI", "klinik swasta").strip()

# ── Behavior ──────────────────────────────────────────────────────────────────
# Hari sebelum lead "sent" dianggap perlu di-follow-up
HARI_BATAS_FOLLOWUP = int(os.getenv("HARI_BATAS_FOLLOWUP", "3"))

# Threshold kemiripan nama untuk fuzzy dedup (0.0–1.0)
FUZZY_THRESHOLD = float(os.getenv("FUZZY_THRESHOLD", "0.80"))

# Mode draft default (True = semua pesan masuk draft dulu)
DRAFT_MODE_DEFAULT = os.getenv("DRAFT_MODE_DEFAULT", "false").lower() == "true"

# ── Self-critique pesan (#18) ─────────────────────────────────────────────────
# Aktifkan dengan SELF_CRITIQUE_ENABLED=true di .env
# Menambah 1 Groq API call per pesan — matikan kalau kuota mepet
SELF_CRITIQUE_ENABLED  = os.getenv("SELF_CRITIQUE_ENABLED", "false").lower() == "true"
SELF_CRITIQUE_MIN_SKOR = int(os.getenv("SELF_CRITIQUE_MIN_SKOR", "7"))   # 1-10
SELF_CRITIQUE_MAX_RETRY = int(os.getenv("SELF_CRITIQUE_MAX_RETRY", "2"))  # maks regenerasi

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ── Server ────────────────────────────────────────────────────────────────────
PORT_DASHBOARD = int(os.getenv("PORT_DASHBOARD", "8000"))

# Password dashboard (HTTP Basic Auth). Default kosong = TIDAK ada auth
# (pemakaian lokal, backward compatible). WAJIB diisi sebelum deploy ke VPS.
# Server tetap bind ke 127.0.0.1 saja — untuk VPS akses lewat reverse proxy
# (nginx) dengan HTTPS, jangan expose PORT_DASHBOARD langsung ke internet.
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")

# ── Sub-agent riset (opsional, 1 API call ekstra per lead) ────────────────────
RESEARCH_SUBAGENT_ENABLED = os.getenv("RESEARCH_SUBAGENT_ENABLED", "false").lower() == "true"

# ── Orchestrator (decision loop otonom) ────────────────────────────────────────
ORCHESTRATOR_ENABLED       = os.getenv("ORCHESTRATOR_ENABLED", "false").lower() == "true"
ORCHESTRATOR_CEK_INTERVAL  = int(os.getenv("ORCHESTRATOR_CEK_INTERVAL", "1800"))
ORCHESTRATOR_JAM_BUILD     = os.getenv("ORCHESTRATOR_JAM_BUILD", "06:00").strip()

# ── Laporan harian otomatis (dikirim orchestrator, butuh ORCHESTRATOR_ENABLED=true) ──
LAPORAN_HARIAN_ENABLED = os.getenv("LAPORAN_HARIAN_ENABLED", "true").lower() == "true"
LAPORAN_HARIAN_JAM     = os.getenv("LAPORAN_HARIAN_JAM", "07:00").strip()

# ── Error alert & heartbeat (orchestrator) ──────────────────────────────────────
# Berapa kali error beruntun dalam 10 menit sebelum orchestrator dianggap
# "crash berulang" dan di-pause sementara (butuh cek manual).
ALERT_CRASH_THRESHOLD = int(os.getenv("ALERT_CRASH_THRESHOLD", "3"))
# Kirim "bot masih hidup" kalau sudah sekian jam tanpa aksi nyata (build/followup/
# laporan) — biar user yakin loop belum diam-diam mati.
ALERT_HEARTBEAT_JAM = int(os.getenv("ALERT_HEARTBEAT_JAM", "12"))

# ── Groq client factory ──────────────────────────────────────────────────────
# PENTING: dari VPS/cloud (Railway dkk), request ke api.groq.com lewat
# httpx/urllib default sering kena block Cloudflare (error 1010 — "banned
# based on your browser's signature"). Ini BUKAN soal API key salah — WAF
# Groq nge-flag TLS/HTTP fingerprint library Python sebagai bot. Fix: kirim
# User-Agent yang mirip browser asli. Terverifikasi lolos block via test
# manual dari container Railway (curl tidak ada, dites pakai urllib).
#
# SEMUA pemanggil Groq API (builder.py, agent_loop.py, reply_assistant.py,
# sub_agent_research.py) WAJIB pakai get_groq_client() ini, JANGAN
# instantiate Groq(...) langsung — supaya fix ini otomatis kebawa ke semua
# tempat dan tidak ada yang lupa pas nambah caller baru.
_GROQ_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def get_groq_client(api_key: Optional[str] = None):
    """Bikin instance Groq client dengan User-Agent browser-like terpasang
    (lihat catatan _GROQ_USER_AGENT di atas). Default pakai GROQ_API_KEY
    dari .env kalau api_key tidak dikasih eksplisit."""
    import httpx
    from groq import Groq
    # Paksa IPv4: banyak VPS (termasuk Rumahweb) punya route IPv6 yang
    # terdaftar tapi tidak fungsional. httpx pilih IPv6 (AAAA) duluan ->
    # TCP connect gagal -> groq SDK lempar "Connection error" generik.
    # local_address="0.0.0.0" memaksa socket bind IPv4-only.
    transport = httpx.HTTPTransport(local_address="0.0.0.0", retries=2)
    http_client = httpx.Client(
        transport=transport,
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"User-Agent": _GROQ_USER_AGENT},
    )
    return Groq(
        api_key=api_key or GROQ_API_KEY,
        default_headers={"User-Agent": _GROQ_USER_AGENT},
        http_client=http_client,
    )
