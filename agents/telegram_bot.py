"""
telegram_bot.py
===============
Telegram Bot dua arah untuk AI Outreach — lo bisa kontrol sistem dari HP.

Commands yang tersedia:
    /start   — salam perkenalan + daftar command
    /help    — daftar command
    /status  — ringkasan pipeline (total lead, pending, replied, dll)
    /pending — 5 pesan pending teratas siap kirim (prioritas klinik/hotel duluan)
    /drafts  — draft yang butuh review + approve
    /daily   — trigger followup + build (sama kayak python main.py daily)
    /build   — trigger build saja
    /followup — trigger followup saja
    /kirim <nomor> — tandai lead 'sent' setelah kirim WA manual
    /report  — generate laporan PDF 7 hari terakhir, dikirim langsung ke chat
    /balas <nomor> <pesan> — draft balasan AI untuk lead yang reply
    /orchestrator — cek status decision loop otomatis (ON/OFF)
    /agentloop — trigger Think-Act-Observe loop manual (AI pilih aksi sendiri)

Setup (sekali saja):
    1. Isi TELEGRAM_BOT_TOKEN di .env (dapat dari BotFather)
    2. Jalankan: python main.py get-chatid
       → kirim /start ke bot di Telegram dulu kalau belum
       → copy CHAT_ID yang muncul, isi ke .env
    3. Jalankan bot: python main.py bot
       → biarkan berjalan di background (atau setup Task Scheduler)

Cara pakai:
    python agents/telegram_bot.py       -> jalankan bot langsung
    (atau lewat) python main.py bot     -> jalankan dari main
"""

import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    import requests as _req
    _REQUESTS_ADA = True
except ImportError:
    _REQUESTS_ADA = False

try:
    from .config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
    from .log_setup import buat_logger
    from . import db
except ImportError:
    from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
    from log_setup import buat_logger
    import db

log = buat_logger("telegram_bot")

BASE_DIR   = Path(__file__).resolve().parent.parent
SENT_PATH  = BASE_DIR / "data" / "sent.json"
LEADS_PATH = BASE_DIR / "data" / "leads.json"


# ── API helpers ───────────────────────────────────────────────────────────────

def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"


def _get_updates(offset: int | None = None) -> list[dict]:
    try:
        r = _req.get(
            _api_url("getUpdates"),
            params={"timeout": 25, "offset": offset},
            timeout=30,
        )
        return r.json().get("result", [])
    except Exception:
        return []


def kirim(chat_id: int | str, teks: str) -> None:
    """Kirim pesan ke chat_id tertentu."""
    try:
        _req.post(
            _api_url("sendMessage"),
            json={
                "chat_id": chat_id,
                "text": teks,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception as e:
        log.warning(f"[telegram_bot] Gagal kirim pesan: {e}")


# ── Baca data lokal ───────────────────────────────────────────────────────────

def _baca_sent() -> list[dict]:
    return db.muat_sent()


def _baca_leads() -> list[dict]:
    return db.muat_leads()


# ── Command handlers ──────────────────────────────────────────────────────────

def _buat_wa_link(nomor_wa: str, pesan: str) -> str:
    """
    Bikin wa.me deep link — tap dari HP langsung buka WA app dengan
    nomor dan pesan sudah ke-draft. Universal: app native di HP, WA Web di desktop.
    """
    import urllib.parse
    nomor_bersih = nomor_wa.lstrip("+").replace(" ", "").replace("-", "")
    return f"https://wa.me/{nomor_bersih}?text={urllib.parse.quote(pesan or '')}"


def handle_status(chat_id: int) -> None:
    sent  = _baca_sent()
    leads = _baca_leads()
    hitung = Counter(s.get("status", "pending") for s in sent)

    teks = (
        f"📊 <b>Status Pipeline</b>\n"
        f"<i>{datetime.now().strftime('%d %b %Y %H:%M')}</i>\n\n"
        f"👥 Total leads    : {len(leads)}\n"
        f"✏️  Draft          : {hitung.get('draft', 0)}\n"
        f"📬 Pending        : {hitung.get('pending', 0)}\n"
        f"✅ Sent           : {hitung.get('sent', 0)}\n"
        f"💬 Replied        : {hitung.get('replied', 0)}\n"
        f"🔁 Follow Up Due  : {hitung.get('followup_due', 0)}\n"
        f"❌ Bounced        : {hitung.get('bounced', 0)}\n\n"
    )

    # Breakdown prioritas
    klinik_p = sum(1 for s in sent if s.get("status") in {"pending","draft"} and s.get("kategori_group") == "klinik")
    hotel_p  = sum(1 for s in sent if s.get("status") in {"pending","draft"} and s.get("kategori_group") == "hotel")
    if klinik_p or hotel_p:
        teks += f"Prioritas: 🏥 {klinik_p} klinik | 🏨 {hotel_p} hotel"

    kirim(chat_id, teks)


def handle_pending(chat_id: int) -> None:
    sent = _baca_sent()

    # Urutkan: klinik/hotel duluan, lalu lainnya
    pending = [s for s in sent if s.get("status") in {"pending", "followup_due"}]
    pending.sort(key=lambda s: (
        0 if s.get("kategori_group") in {"klinik", "hotel"} else 1
    ))
    pending = pending[:5]

    if not pending:
        kirim(chat_id, "📭 Tidak ada pesan pending saat ini.\n\nJalankan /build untuk generate pesan baru.")
        return

    total = sum(1 for s in sent if s.get("status") in {"pending", "followup_due"})
    teks  = f"📬 <b>{total} pending — 5 teratas:</b>\n\n"
    for i, p in enumerate(pending, 1):
        grup = p.get("kategori_group", "")
        ikon = "🏥" if grup == "klinik" else "🏨" if grup == "hotel" else "💼"
        fu   = " 🔁" if p.get("status") == "followup_due" else ""
        wa_link = _buat_wa_link(p.get("nomor_wa", ""), p.get("pesan", ""))
        teks += (
            f"{i}. {ikon} <b>{p.get('nama', '?')}</b>{fu}\n"
            f"   <i>{(p.get('pesan') or '')[:80]}...</i>\n"
            f"   👉 <a href=\"{wa_link}\">Buka & Kirim WA</a>\n"
            f"   Habis kirim: <code>/kirim {p.get('nomor_wa', '')}</code>\n\n"
        )
    kirim(chat_id, teks)


def handle_drafts(chat_id: int) -> None:
    sent   = _baca_sent()
    drafts = [s for s in sent if s.get("status") == "draft"][:5]

    if not drafts:
        kirim(chat_id, "✏️ Tidak ada draft saat ini.\n\nJalankan /build --draft untuk generate draft.")
        return

    total = sum(1 for s in sent if s.get("status") == "draft")
    teks  = f"✏️ <b>{total} draft — 5 teratas:</b>\n\n"
    for i, d in enumerate(drafts, 1):
        skor = d.get("skor_pesan", 0)
        skor_str = f" [skor: {skor}/10]" if skor else ""
        teks += (
            f"{i}. <b>{d.get('nama', '?')}</b>{skor_str}\n"
            f"   <i>{(d.get('pesan') or '')[:90]}...</i>\n\n"
        )
    teks += "💡 Approve draft di dashboard: <code>python main.py serve</code>"
    kirim(chat_id, teks)


def _jalankan_command(chat_id: int, perintah: str) -> None:
    """Jalankan main.py command lewat subprocess dan kirim hasilnya ke Telegram."""
    kirim(chat_id, f"⏳ Menjalankan <code>{perintah}</code>...")
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "main.py")] + perintah.split(),
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(BASE_DIR),
        )
        output = (result.stdout or "").strip()
        if not output:
            output = (result.stderr or "Tidak ada output").strip()
        # Potong kalau terlalu panjang buat Telegram (max 4096 chars)
        if len(output) > 1500:
            output = output[-1500:]
        kirim(chat_id, f"✅ Selesai.\n\n<code>{output}</code>")
    except subprocess.TimeoutExpired:
        kirim(chat_id, "⚠️ Timeout — proses terlalu lama. Cek laptop lo.")
    except Exception as e:
        kirim(chat_id, f"❌ Error: {e}")


def handle_agent_loop(chat_id: int) -> None:
    """Trigger Think-Act-Observe loop manual dari HP — AI pilih aksi sendiri."""
    kirim(chat_id, "🧠 Menjalankan agent loop (AI sedang berpikir)...")
    try:
        try:
            from . import agent_loop
        except ImportError:
            from agents import agent_loop
        histori = agent_loop.jalankan_loop(max_iterasi=5)
        ringkasan = agent_loop.format_ringkasan(histori)
        kirim(chat_id, ringkasan)
    except Exception as e:
        kirim(chat_id, f"❌ Agent loop error: {e}")


def handle_orchestrator_status(chat_id: int) -> None:
    """Cek status orchestrator (aktif/tidak, kapan terakhir ambil aksi)."""
    try:
        from . import orchestrator
        from . import config as cfg
    except ImportError:
        from agents import orchestrator
        from agents import config as cfg

    if not cfg.ORCHESTRATOR_ENABLED:
        kirim(
            chat_id,
            "🧠 <b>Orchestrator: OFF</b>\n\n"
            "Sistem masih pakai mode manual (trigger command sendiri).\n"
            "Aktifkan di .env: <code>ORCHESTRATOR_ENABLED=true</code>, restart bot."
        )
        return

    state = orchestrator._baca_state()
    build_terakhir = state.get("build_harian", "belum pernah")

    teks = (
        "🧠 <b>Orchestrator: ON</b>\n\n"
        f"Cek interval: setiap {cfg.ORCHESTRATOR_CEK_INTERVAL // 60} menit\n"
        f"Jadwal build harian: {cfg.ORCHESTRATOR_JAM_BUILD}\n"
        f"Build terakhir dijalankan: {build_terakhir}\n\n"
        "<i>Bot akan otomatis build + cek follow-up tanpa perlu Task Scheduler, "
        "selama proses ini tetap jalan.</i>"
    )
    kirim(chat_id, teks)


def handle_daily(chat_id: int) -> None:
    _jalankan_command(chat_id, "daily")


def handle_build(chat_id: int) -> None:
    _jalankan_command(chat_id, "build")


def handle_followup(chat_id: int) -> None:
    _jalankan_command(chat_id, "followup")


def handle_report(chat_id: int) -> None:
    kirim(chat_id, "⏳ Membuat laporan PDF...")
    try:
        try:
            from . import report
        except ImportError:
            from agents import report
        path = report.generate(hari=7)
        with open(path, "rb") as f:
            _req.post(
                _api_url("sendDocument"),
                data={"chat_id": chat_id, "caption": "📊 Laporan outreach 7 hari terakhir"},
                files={"document": (path.name, f, "application/pdf")},
                timeout=30,
            )
    except Exception as e:
        kirim(chat_id, f"❌ Gagal membuat laporan: {e}")


def handle_balas(chat_id: int, args: str) -> None:
    parts = args.strip().split(maxsplit=1)
    if len(parts) < 2:
        kirim(chat_id, "⚠️ Format: <code>/balas 628xxxxxxxxx pesan yang mereka kirim</code>\n\nContoh: <code>/balas 6281234567 halo boleh minta info harga</code>")
        return

    nomor_wa, pesan_masuk = parts[0], parts[1]
    kirim(chat_id, "⏳ Menganalisis balasan & bikin draft respon...")

    try:
        try:
            from . import reply_assistant
        except ImportError:
            from agents import reply_assistant
        hasil = reply_assistant.proses_balasan(nomor_wa, pesan_masuk)
    except Exception as e:
        kirim(chat_id, f"❌ Error: {e}")
        return

    if hasil.get("error"):
        kirim(chat_id, f"❌ {hasil['error']}")
        return

    nama = (hasil.get("lead") or {}).get("nama", nomor_wa)
    wa_link = _buat_wa_link(nomor_wa, hasil["draft"])

    teks = (
        f"💬 <b>Draft balasan untuk {nama}</b>\n"
        f"<i>Terdeteksi: {hasil['jenis_terdeteksi']}</i>\n\n"
        f"{hasil['draft']}\n\n"
        f"👉 <a href=\"{wa_link}\">Buka & Kirim Balasan</a>\n\n"
        f"<i>Status sudah ditandai 'replied' otomatis.</i>"
    )
    kirim(chat_id, teks)


def handle_cari(chat_id: int, args: str) -> None:
    """
    /cari <keyword> — cari lead by nama/kota/kategori/nomor WA.
    Cocok dipakai pas gak ada laptop, langsung dari HP cari lead tertentu.
    """
    keyword = args.strip().lower()
    if not keyword:
        kirim(chat_id, "⚠️ Format: <code>/cari klinik berau</code> atau <code>/cari 6281234567</code>")
        return

    sent = _baca_sent()
    kata_kunci = keyword.split()

    def cocok(item: dict) -> bool:
        gabungan = " ".join(str(item.get(k, "")) for k in
                             ["nama", "kota", "kategori", "kategori_group", "nomor_wa"]).lower()
        return all(k in gabungan for k in kata_kunci)

    hasil = [s for s in sent if cocok(s)]

    if not hasil:
        kirim(chat_id, f"🔍 Tidak ada hasil untuk \"{keyword}\".")
        return

    total = len(hasil)
    hasil = hasil[:8]

    teks = f"🔍 <b>{total} hasil untuk \"{keyword}\"</b>" + (" (8 teratas)" if total > 8 else "") + ":\n\n"
    for i, h in enumerate(hasil, 1):
        status = h.get("status", "?")
        badge = {
            "pending": "📬", "sent": "✅", "replied": "💬",
            "draft": "✏️", "bounced": "❌", "followup_due": "🔁",
        }.get(status, "•")
        teks += (
            f"{i}. {badge} <b>{h.get('nama', '?')}</b> — {h.get('kota', '-')}\n"
            f"   📱 <code>{h.get('nomor_wa', '-')}</code> · status: {status}\n\n"
        )
    kirim(chat_id, teks)


def handle_bounced(chat_id: int, args: str) -> None:
    """
    /bounced <nomor_wa> — tandai lead sebagai bounced (nomor gak valid/gagal kirim).
    """
    nomor_wa = args.strip()
    if not nomor_wa:
        kirim(chat_id, "⚠️ Format: <code>/bounced 628xxxxxxxxx</code>\n\nCopy nomor dari hasil /cari atau /pending.")
        return

    try:
        from . import tracker
    except ImportError:
        from agents import tracker

    berhasil = tracker.update_status(nomor_wa, "bounced")
    if berhasil:
        kirim(chat_id, f"❌ <code>{nomor_wa}</code> ditandai <b>bounced</b> (nomor gak valid/gagal kirim).")
    else:
        kirim(chat_id, f"⚠️ Nomor <code>{nomor_wa}</code> tidak ditemukan di sistem.")


def handle_ratecheck(chat_id: int) -> None:
    """Cek manual kecepatan pengiriman WA sekarang (rate_guard)."""
    try:
        from . import rate_guard
    except ImportError:
        from agents import rate_guard

    status = rate_guard.cek_kecepatan_kirim()
    emoji = {"aman": "✅", "waspada": "⚡", "bahaya": "🚨"}.get(status["level"], "❓")

    teks = (
        f"{emoji} <b>Rate Check — Kecepatan Kirim WA</b>\n\n"
        f"{status['pesan']}\n\n"
        f"<i>Threshold: max {rate_guard.BATAS_PER_JAM}/jam, "
        f"{rate_guard.BATAS_PER_HARI}/hari sebelum 'waspada'.</i>"
    )
    kirim(chat_id, teks)


def handle_kirim(chat_id: int, args: str) -> None:
    """
    /kirim <nomor_wa> — tandai lead sebagai 'sent' setelah lo kirim WA manual.
    Sekaligus cek kecepatan pengiriman (rate_guard).
    """
    nomor_wa = args.strip()
    if not nomor_wa:
        kirim(chat_id, "⚠️ Format: <code>/kirim 628xxxxxxxxx</code>\n\nCopy nomor dari daftar /pending.")
        return

    try:
        from . import tracker, rate_guard
    except ImportError:
        from agents import tracker, rate_guard

    berhasil = tracker.update_status(nomor_wa, "sent")
    if not berhasil:
        kirim(chat_id, f"❌ Nomor <code>{nomor_wa}</code> tidak ditemukan di sistem.")
        return

    rate_guard.catat_kirim(nomor_wa)
    status_rate = rate_guard.cek_kecepatan_kirim()

    teks = f"✅ <code>{nomor_wa}</code> ditandai <b>sent</b>."
    if status_rate["level"] != "aman":
        teks += f"\n\n{status_rate['pesan']}"
    kirim(chat_id, teks)


HELP_TEXT = (
    "🤖 <b>AI Outreach Bot</b>\n"
    "<i>Kontrol sistem outreach dari HP</i>\n\n"
    "/status    — ringkasan pipeline\n"
    "/pending   — 5 pesan siap kirim (prioritas klinik/hotel)\n"
    "/drafts    — draft butuh review\n"
    "/daily     — followup + build (siklus harian)\n"
    "/build     — generate pesan baru saja\n"
    "/followup  — tandai lead yang perlu follow-up\n"
    "/kirim <nomor> — tandai sent setelah kirim WA manual\n"
    "/ratecheck — cek kecepatan kirim WA (resiko kena restriksi)\n"
    "/cari <keyword> — cari lead by nama/kota/kategori/nomor\n"
    "/bounced <nomor> — tandai nomor gak valid/gagal kirim\n"
    "/report — laporan PDF 7 hari terakhir (langsung dikirim ke chat)\n"
    "/balas <nomor> <pesan> — draft balasan AI untuk lead yang reply\n"
    "/orchestrator — cek status decision loop otomatis\n"
    "/agentloop — trigger Think-Act-Observe loop manual (AI pilih aksi sendiri)\n"
    "/help      — tampilkan ini"
)

_COMMANDS: dict[str, callable] = {
    "/status":   handle_status,
    "/pending":  handle_pending,
    "/drafts":   handle_drafts,
    "/daily":    handle_daily,
    "/build":    handle_build,
    "/followup": handle_followup,
    "/report":   handle_report,
    "/ratecheck": handle_ratecheck,
    "/orchestrator": handle_orchestrator_status,
    "/agentloop": handle_agent_loop,
}


# ── Main polling loop ─────────────────────────────────────────────────────────

def get_chat_id() -> str | None:
    """
    Helper: ambil chat_id dari update terbaru.
    User harus sudah kirim pesan ke bot duluan.
    """
    if not _REQUESTS_ADA:
        print("[telegram_bot] requests belum terinstall.")
        return None
    if not TELEGRAM_TOKEN:
        print("[telegram_bot] TELEGRAM_BOT_TOKEN belum diset di .env")
        return None
    updates = _get_updates()
    if not updates:
        return None
    for u in updates:
        chat = u.get("message", {}).get("chat", {})
        if chat.get("id"):
            return str(chat["id"])
    return None


def run_polling() -> None:
    """Jalankan bot dengan long-polling. Blokir sampai Ctrl+C."""
    if not _REQUESTS_ADA:
        log.warning("[telegram_bot] Library 'requests' tidak ditemukan.")
        return
    if not TELEGRAM_TOKEN:
        log.warning("[telegram_bot] TELEGRAM_BOT_TOKEN belum diset di .env")
        return
    if not TELEGRAM_CHAT_ID:
        log.warning("[telegram_bot] TELEGRAM_CHAT_ID belum diset. Jalankan: python main.py get-chatid")
        return

    log.info(f"[telegram_bot] Bot aktif — menunggu perintah dari HP...")

    # Mulai orchestrator decision loop di background (kalau diaktifkan di .env)
    try:
        from . import orchestrator
    except ImportError:
        from agents import orchestrator
    thread_orchestrator = orchestrator.mulai_background()

    # Kirim notif ke HP bahwa bot baru nyala
    status_orkestrator = " + Orchestrator otomatis aktif 🧠" if thread_orchestrator else ""
    kirim(TELEGRAM_CHAT_ID, f"🟢 <b>AI Outreach Bot online</b>{status_orkestrator}\n\nKetik /help untuk daftar command.")

    offset = None
    while True:
        try:
            updates = _get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                msg     = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                teks    = (msg.get("text") or "").strip()

                # Security: hanya respons ke TELEGRAM_CHAT_ID yang terdaftar
                if str(chat_id) != str(TELEGRAM_CHAT_ID):
                    log.warning(f"[telegram_bot] Pesan dari chat_id asing: {chat_id} — diabaikan.")
                    continue

                if not teks:
                    continue

                cmd = teks.split()[0].lower()
                if cmd in {"/start", "/help"}:
                    kirim(chat_id, HELP_TEXT)
                elif cmd == "/kirim":
                    args = teks[len(cmd):].strip()
                    handle_kirim(chat_id, args)
                elif cmd == "/balas":
                    args = teks[len(cmd):].strip()
                    handle_balas(chat_id, args)
                elif cmd == "/cari":
                    args = teks[len(cmd):].strip()
                    handle_cari(chat_id, args)
                elif cmd == "/bounced":
                    args = teks[len(cmd):].strip()
                    handle_bounced(chat_id, args)
                elif cmd in _COMMANDS:
                    _COMMANDS[cmd](chat_id)
                else:
                    kirim(chat_id, f"❓ Command tidak dikenal.\n\nKetik /help untuk daftar command.")

            time.sleep(2)

        except KeyboardInterrupt:
            log.info("[telegram_bot] Bot dihentikan.")
            if thread_orchestrator:
                orchestrator.berhenti()
                log.info("[telegram_bot] Orchestrator dihentikan.")
            kirim(TELEGRAM_CHAT_ID, "🔴 Bot dihentikan.")
            break
        except Exception as e:
            log.warning(f"[telegram_bot] Error di loop polling: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run_polling()
