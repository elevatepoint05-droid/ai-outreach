"""
orchestrator.py
================
Decision loop otonom — jalan terus di background, mikir sendiri kapan harus
ngapain, tanpa perlu Windows Task Scheduler atau trigger manual.

BEDA sama sistem lama:
- Sebelumnya: "daily" cuma jalan kalau ada yang manggil (manual/Task Scheduler)
  sekali jalan, terus selesai, berhenti. Tidak ada yang "mikir".
- Sekarang: loop ini jalan terus selama proses bot hidup. Tiap interval
  (default 30 menit), dia CEK KONDISI SISTEM SEKARANG lalu MEMUTUSKAN
  aksi apa yang perlu diambil — bukan asal jalanin command di jam tetap.

Ini BUKAN multi-agent orchestrator kayak sistem enterprise (belum ada
routing ke banyak "agent" spesialis dengan model berbeda-beda). Ini
langkah pertama yang realistis: satu decision loop yang punya beberapa
ATURAN KEPUTUSAN sederhana, dijalankan otomatis, dan bisa berkembang jadi
lebih pintar (nambah aturan baru) seiring waktu.

Aturan keputusan saat ini:
1. Build harian — jalan sekali per hari, di jam yang dikonfigurasi
   (bukan lagi bergantung Task Scheduler terpisah)
2. Follow-up check — tiap interval, cek ada lead yang sudah lewat batas hari
   tapi belum ditandai follow-up

Cara pakai:
    Aktifkan di .env: ORCHESTRATOR_ENABLED=true
    Lalu jalankan bot seperti biasa: python main.py bot
    Orchestrator otomatis jalan di thread terpisah, tidak mengganggu
    polling Telegram.

Kalau ORCHESTRATOR_ENABLED=false (default), sistem berperilaku persis
seperti sebelumnya — perlu trigger manual atau Task Scheduler.
"""

import threading
from datetime import datetime, timedelta
from pathlib import Path

try:
    from . import db, notif
    from .log_setup import buat_logger
    from . import config as cfg
except ImportError:
    import db, notif
    from log_setup import buat_logger
    import config as cfg

log = buat_logger("orchestrator")

BASE_DIR   = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "data" / "orchestrator_state.txt"

_berhenti = threading.Event()


def _baca_state() -> dict:
    """
    Baca kapan terakhir kali tiap aksi dijalankan, biar orchestrator gak
    ngulang build harian berkali-kali di hari yang sama meskipun proses
    di-restart berkali-kali.
    """
    state = {}
    if STATE_PATH.exists():
        for baris in STATE_PATH.read_text().splitlines():
            if "=" in baris:
                k, v = baris.split("=", 1)
                state[k.strip()] = v.strip()
    return state


def _tulis_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    isi = "\n".join(f"{k}={v}" for k, v in state.items())
    STATE_PATH.write_text(isi)


def _sudah_dijalankan_hari_ini(state: dict, key: str) -> bool:
    tanggal_terakhir = state.get(key)
    hari_ini = datetime.now().strftime("%Y-%m-%d")
    return tanggal_terakhir == hari_ini


def _tandai_dijalankan_hari_ini(state: dict, key: str) -> None:
    state[key] = datetime.now().strftime("%Y-%m-%d")
    _tulis_state(state)


def _lewat_jam_target(jam_target_str: str) -> bool:
    """Cek apakah sekarang sudah lewat jam target (format HH:MM) hari ini."""
    try:
        jam, menit = map(int, jam_target_str.split(":"))
    except Exception:
        jam, menit = 6, 0  # fallback jam 6 pagi
    target = datetime.now().replace(hour=jam, minute=menit, second=0, microsecond=0)
    return datetime.now() >= target


def _putuskan_dan_eksekusi() -> list[str]:
    """
    Inti decision loop: cek kondisi sistem sekarang, putuskan aksi apa
    yang perlu diambil, eksekusi, dan return daftar aksi yang dilakukan
    (buat dilaporkan ke Telegram).
    """
    state    = _baca_state()
    aksi_log = []

    # ── KEPUTUSAN 1: Build harian ──────────────────────────────────────────
    # Kondisi: belum pernah build hari ini DAN sudah lewat jam target
    if not _sudah_dijalankan_hari_ini(state, "build_harian") and _lewat_jam_target(cfg.ORCHESTRATOR_JAM_BUILD):
        log.info("[orchestrator] Kondisi terpenuhi: build harian belum jalan, sudah lewat jam target.")
        try:
            from . import tracker, builder
        except ImportError:
            import tracker, builder
        tracker.cek_followup()
        builder.main(mode_draft=cfg.DRAFT_MODE_DEFAULT)
        _tandai_dijalankan_hari_ini(state, "build_harian")
        aksi_log.append(f"✅ Build harian dijalankan otomatis (jadwal {cfg.ORCHESTRATOR_JAM_BUILD})")

    # ── KEPUTUSAN 2: Follow-up check (tiap interval, bukan cuma harian) ────
    # Kondisi: ada lead berstatus "sent" yang sudah lewat HARI_BATAS_FOLLOWUP
    sent = [item for item in db.get_sent() if item.get("status") == "sent"]
    batas = datetime.now() - timedelta(days=cfg.HARI_BATAS_FOLLOWUP)
    perlu_followup = 0
    for item in sent:
        jam_kirim = item.get("jam_kirim")
        if jam_kirim:
            try:
                waktu_kirim = datetime.fromisoformat(jam_kirim.replace("Z", ""))
                if waktu_kirim < batas:
                    perlu_followup += 1
            except Exception:
                pass

    if perlu_followup > 0:
        try:
            from . import tracker
        except ImportError:
            import tracker
        tracker.cek_followup()
        aksi_log.append(f"🔁 {perlu_followup} lead ditandai perlu follow-up")

    return aksi_log


def _loop_utama() -> None:
    """Loop utama yang jalan terus di background thread."""
    log.info(
        f"[orchestrator] Decision loop aktif — cek tiap {cfg.ORCHESTRATOR_CEK_INTERVAL}s, "
        f"build harian target jam {cfg.ORCHESTRATOR_JAM_BUILD}"
    )
    while not _berhenti.is_set():
        try:
            aksi = _putuskan_dan_eksekusi()
            if aksi:
                pesan = "🧠 <b>Orchestrator mengambil aksi:</b>\n\n" + "\n".join(aksi)
                notif.kirim(pesan)
                log.info(f"[orchestrator] Aksi diambil: {aksi}")
        except Exception as e:
            log.warning(f"[orchestrator] Error di decision loop: {e}")

        # Tunggu interval berikutnya, tapi tetap responsif ke sinyal berhenti
        _berhenti.wait(timeout=cfg.ORCHESTRATOR_CEK_INTERVAL)


def mulai_background() -> threading.Thread | None:
    """
    Mulai orchestrator di thread terpisah. Dipanggil dari telegram_bot.py
    supaya jalan bareng polling loop tanpa saling blokir.
    Return: Thread object (atau None kalau orchestrator disabled).
    """
    if not cfg.ORCHESTRATOR_ENABLED:
        log.info("[orchestrator] ORCHESTRATOR_ENABLED=false — decision loop tidak dijalankan.")
        return None
    thread = threading.Thread(target=_loop_utama, daemon=True, name="orchestrator")
    thread.start()
    return thread


def berhenti() -> None:
    """Signal thread orchestrator untuk berhenti (dipanggil saat shutdown)."""
    _berhenti.set()
