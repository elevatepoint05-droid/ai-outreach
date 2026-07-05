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
3. Laporan harian — jam target (LAPORAN_HARIAN_JAM), kirim ringkasan
   pending/sent-hari-ini/conversion-rate/revenue ke Telegram

Reliability layer:
- Error alert — kalau siklus decision loop error, kirim notif Telegram
  (traceback singkat, max 300 char), loop tetap lanjut jalan (tidak mati).
- Crash beruntun — kalau error ALERT_CRASH_THRESHOLD kali dalam 10 menit,
  kirim alert "butuh cek manual" dan pause 30 menit sebelum retry.
- Heartbeat — kalau ALERT_HEARTBEAT_JAM jam tanpa aksi nyata sama sekali,
  kirim "bot masih hidup" biar user yakin loop belum diam-diam mati.

Cara pakai:
    Aktifkan di .env: ORCHESTRATOR_ENABLED=true
    Lalu jalankan bot seperti biasa: python main.py bot
    Orchestrator otomatis jalan di thread terpisah, tidak mengganggu
    polling Telegram.

Kalau ORCHESTRATOR_ENABLED=false (default), sistem berperilaku persis
seperti sebelumnya — perlu trigger manual atau Task Scheduler.
"""

import threading
import traceback
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

# ── State error alert & heartbeat (in-memory, khusus umur proses ini) ──────────
# Reset ke kosong tiap proses baru dimulai — sengaja, karena "crash beruntun"
# cuma masuk akal diukur dalam satu sesi proses yang sama.
_crash_times: list[datetime] = []
_last_aksi_at: datetime | None = None
_last_heartbeat: datetime | None = None

ALERT_CRASH_WINDOW_DETIK = 600   # 10 menit — jendela hitung crash beruntun
ALERT_PAUSE_DETIK        = 1800  # 30 menit — durasi pause setelah crash beruntun


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


def _catat_crash_dan_cek_beruntun() -> bool:
    """
    Catat waktu crash sekarang, buang catatan yang sudah lewat dari jendela
    ALERT_CRASH_WINDOW_DETIK, lalu cek apakah jumlah crash dalam jendela itu
    sudah mencapai cfg.ALERT_CRASH_THRESHOLD.

    Return True kalau orchestrator perlu di-pause (crash beruntun terdeteksi).
    """
    global _crash_times
    sekarang = datetime.now()
    _crash_times.append(sekarang)
    batas = sekarang - timedelta(seconds=ALERT_CRASH_WINDOW_DETIK)
    _crash_times = [t for t in _crash_times if t >= batas]
    return len(_crash_times) >= cfg.ALERT_CRASH_THRESHOLD


def _cek_heartbeat() -> None:
    """
    Kirim '💓 Bot masih hidup' kalau sudah ALERT_HEARTBEAT_JAM jam tanpa aksi
    nyata (build/followup/laporan) SEKALIGUS belum pernah heartbeat dalam
    periode idle ini — biar tidak spam heartbeat tiap siklus cek begitu
    ambang batas terlewati.
    """
    global _last_heartbeat
    if _last_aksi_at is None:
        return  # belum ada baseline aktivitas sama sekali, skip dulu

    sekarang  = datetime.now()
    batas_detik = cfg.ALERT_HEARTBEAT_JAM * 3600
    idle_detik  = (sekarang - _last_aksi_at).total_seconds()

    if idle_detik < batas_detik:
        return

    if _last_heartbeat is not None and (sekarang - _last_heartbeat).total_seconds() < batas_detik:
        return  # sudah pernah heartbeat dalam periode idle ini, jangan spam

    notif.kirim(
        "💓 <b>Bot masih hidup</b>\n\n"
        f"Tidak ada aksi baru selama {cfg.ALERT_HEARTBEAT_JAM} jam terakhir.\n"
        f"Last activity: {_last_aksi_at.strftime('%Y-%m-%d %H:%M')}"
    )
    _last_heartbeat = sekarang
    log.info(f"[orchestrator] Heartbeat terkirim (idle sejak {_last_aksi_at}).")


def _cek_laporan_harian(state: dict) -> bool:
    """
    Kirim laporan harian kalau sudah lewat jam target (LAPORAN_HARIAN_JAM)
    dan belum pernah dikirim hari ini. Return True kalau laporan terkirim
    (dipakai caller buat update _last_aksi_at).
    """
    if not getattr(cfg, "LAPORAN_HARIAN_ENABLED", False):
        return False
    if _sudah_dijalankan_hari_ini(state, "laporan_harian"):
        return False
    if not _lewat_jam_target(cfg.LAPORAN_HARIAN_JAM):
        return False

    try:
        from . import telegram_bot
    except ImportError:
        import telegram_bot

    telegram_bot.kirim_laporan_harian()
    _tandai_dijalankan_hari_ini(state, "laporan_harian")
    log.info(f"[orchestrator] Laporan harian terkirim (jadwal {cfg.LAPORAN_HARIAN_JAM}).")
    return True


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

    # ── KEPUTUSAN 3: Laporan harian (jam target, sekali sehari) ────────────
    if _cek_laporan_harian(state):
        aksi_log.append(f"📅 Laporan harian terkirim (jadwal {cfg.LAPORAN_HARIAN_JAM})")

    return aksi_log


def _loop_utama() -> None:
    """Loop utama yang jalan terus di background thread."""
    global _last_aksi_at
    log.info(
        f"[orchestrator] Decision loop aktif — cek tiap {cfg.ORCHESTRATOR_CEK_INTERVAL}s, "
        f"build harian target jam {cfg.ORCHESTRATOR_JAM_BUILD}"
    )
    # Baseline biar heartbeat tidak langsung nembak begitu proses baru start.
    _last_aksi_at = datetime.now()

    while not _berhenti.is_set():
        try:
            aksi = _putuskan_dan_eksekusi()
            if aksi:
                pesan = "🧠 <b>Orchestrator mengambil aksi:</b>\n\n" + "\n".join(aksi)
                notif.kirim(pesan)
                log.info(f"[orchestrator] Aksi diambil: {aksi}")
                _last_aksi_at = datetime.now()

            _cek_heartbeat()

        except Exception as e:
            # Traceback singkat (max 300 char, ambil dari belakang biar
            # baris exception aslinya kebawa, bukan cuma awal stack trace).
            tb_singkat = traceback.format_exc()[-300:]
            notif.kirim(
                f"❌ <b>Orchestrator error</b>\n\n<code>{tb_singkat}</code>"
            )
            log.warning(f"[orchestrator] Error di decision loop: {e}")

            perlu_pause = _catat_crash_dan_cek_beruntun()
            if perlu_pause:
                notif.kirim(
                    "⚠️ <b>Bot crash berulang, butuh cek manual</b>\n\n"
                    f"{cfg.ALERT_CRASH_THRESHOLD}x error dalam "
                    f"{ALERT_CRASH_WINDOW_DETIK // 60} menit terakhir. "
                    f"Loop di-pause {ALERT_PAUSE_DETIK // 60} menit sebelum retry."
                )
                log.warning(
                    f"[orchestrator] Crash beruntun terdeteksi "
                    f"({len(_crash_times)}x) — pause {ALERT_PAUSE_DETIK}s."
                )
                _crash_times.clear()  # mulai hitungan baru setelah pause
                _berhenti.wait(timeout=ALERT_PAUSE_DETIK)
                continue  # langsung ke iterasi berikutnya, skip wait interval normal

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
