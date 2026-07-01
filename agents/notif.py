"""
notif.py
========
Kirim notifikasi Telegram untuk event penting:
- Lead baru reply (status jadi "replied")
- Ringkasan setelah builder selesai run

Setup (sekali saja):
1. Buat bot lewat @BotFather di Telegram → dapat TELEGRAM_BOT_TOKEN
2. Kirim pesan ke bot lo, lalu buka:
   https://api.telegram.org/bot<TOKEN>/getUpdates
   → ambil chat_id dari response
3. Isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID di file .env

Kalau kedua env var kosong, semua fungsi di sini jadi no-op (tidak error,
cukup skip) supaya sistem tetap jalan walau Telegram belum dikonfigurasi.
"""

try:
    import requests as _requests
    _REQUESTS_ADA = True
except ImportError:
    _REQUESTS_ADA = False

try:
    from .config import TELEGRAM_TOKEN as TOKEN, TELEGRAM_CHAT_ID as CHAT_ID
except ImportError:
    from config import TELEGRAM_TOKEN as TOKEN, TELEGRAM_CHAT_ID as CHAT_ID
_AKTIF   = bool(TOKEN and CHAT_ID and _REQUESTS_ADA)


def kirim(pesan: str) -> bool:
    """
    Kirim pesan teks ke Telegram. Return True kalau berhasil.
    Kalau Telegram belum dikonfigurasi, return False tanpa error.
    """
    if not _AKTIF:
        return False
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": pesan, "parse_mode": "HTML"},
            timeout=8,
        )
        return r.ok
    except Exception as e:
        print(f"[notif] Gagal kirim Telegram: {e}")
        return False


def notif_replied(nama: str, nomor_wa: str, kategori: str = "") -> None:
    """Kirim notif saat ada lead yang reply."""
    ikon  = "🏥" if "klinik" in kategori.lower() else "🏨" if "hotel" in kategori.lower() else "💬"
    teks  = (
        f"{ikon} <b>Ada yang reply!</b>\n\n"
        f"<b>{nama}</b>\n"
        f"📱 <code>{nomor_wa}</code>\n"
        + (f"🗂 {kategori}\n" if kategori else "")
        + "\nBuka dashboard untuk follow up."
    )
    if kirim(teks):
        print(f"[notif] Telegram terkirim — {nama} reply.")


def notif_build_selesai(baru: int, diperbarui: int, total_pending: int) -> None:
    """Kirim ringkasan setelah builder selesai."""
    if baru == 0 and diperbarui == 0:
        return
    teks = (
        "✅ <b>Build selesai</b>\n\n"
        f"📝 Pesan baru     : {baru}\n"
        f"🔄 Diperbarui     : {diperbarui}\n"
        f"📬 Total pending  : {total_pending}\n\n"
        "Buka dashboard untuk mulai kirim."
    )
    kirim(teks)


def notif_build_selesai_v2(
    baru: int,
    diperbarui: int,
    total_pending: int,
    breakdown: dict | None = None,
) -> None:
    """Versi upgrade notif_build_selesai dengan breakdown per kategori."""
    if baru == 0 and diperbarui == 0:
        return

    from datetime import datetime
    waktu = datetime.now().strftime("%d %b %Y %H:%M")

    teks = (
        f"✅ <b>Build selesai</b> — {waktu}\n\n"
        f"📝 Pesan baru     : {baru}\n"
        f"🔄 Diperbarui     : {diperbarui}\n"
        f"📬 Total pending  : {total_pending}\n"
    )

    if breakdown:
        klinik  = breakdown.get("klinik", 0)
        hotel   = breakdown.get("hotel", 0)
        lainnya = breakdown.get("lainnya", 0)
        if klinik or hotel or lainnya:
            teks += f"🏥 Klinik: {klinik} | 🏨 Hotel: {hotel} | Lainnya: {lainnya}\n"

    teks += "\n🖥 Buka dashboard: <code>python main.py serve</code>"
    kirim(teks)
