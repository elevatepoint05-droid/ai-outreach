"""
tracker.py
==========
Baca tabel `sent` (data/outreach.db, lewat agents/db.py) dan kelola status
tiap lead: pending, sent, replied, bounced, followup_due.

Cara pakai (standalone):
    python agents/tracker.py                          -> tampilkan ringkasan status
    python agents/tracker.py update 6285211234501 sent -> ubah status satu lead
    python agents/tracker.py followup                  -> cek lead yang sudah lama
                                                           dikirimi pesan & belum
                                                           respons, tandai 'followup_due'
    python agents/tracker.py followup 5                 -> sama, batas 5 hari (default 3)

Upgrade dari versi sebelumnya:
- update_status() sekarang mencatat tanggal_kirim saat status diubah jadi "sent"
  (dipakai untuk hitung kapan follow-up harus dikirim).
- cek_followup() = bagian "Observe" dari loop: scan lead yang statusnya "sent"
  tapi sudah lewat N hari tanpa respons, lalu tandai 'followup_due' supaya
  builder.py generate-kan pesan follow-up di run berikutnya.
- (#13) Storage dipindah dari sent.json ke SQLite (data/outreach.db). Fungsi
  muat_sent()/simpan_sent() di file ini sekarang cuma wrapper tipis ke agents/db.py,
  dipertahankan namanya biar main.py & dashboard tidak perlu diubah.
"""

import sys
from datetime import datetime, date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from . import notif, backup, db
    from .log_setup import buat_logger
    from .config import HARI_BATAS_FOLLOWUP as _HARI_DEFAULT
except ImportError:
    import notif, backup, db
    from log_setup import buat_logger
    from config import HARI_BATAS_FOLLOWUP as _HARI_DEFAULT

log = buat_logger("tracker")

# Status yang valid untuk tiap lead di sent.json
STATUS_VALID = {"draft", "pending", "sent", "replied", "bounced", "followup_due"}

# Default jeda hari sebelum lead "sent" dianggap perlu di-follow-up
HARI_BATAS_FOLLOWUP_DEFAULT = _HARI_DEFAULT


def muat_sent() -> list[dict]:
    """(#13) Sekarang baca dari data/outreach.db lewat agents/db.py, bukan sent.json lagi.
    Nama fungsi dipertahankan supaya kode lain (main.py, dashboard) tidak perlu diubah."""
    return db.muat_sent()


def simpan_sent(data: list[dict]) -> None:
    """(#13) Sekarang tulis ke data/outreach.db lewat agents/db.py, bukan sent.json lagi."""
    db.simpan_sent(data)


def hitung_status(sent: list[dict] | None = None) -> dict:
    """Hitung jumlah lead per status. Dipakai juga oleh dashboard lewat /api/data/sent."""
    if sent is None:
        sent = muat_sent()

    hasil = {status: 0 for status in STATUS_VALID}
    for item in sent:
        status = item.get("status", "pending")
        hasil[status] = hasil.get(status, 0) + 1
    hasil["total"] = len(sent)
    return hasil


def update_status(nomor_wa: str, status_baru: str, kirim_notif: bool = True) -> bool:
    """Ubah status satu lead berdasarkan nomor WA. Return True kalau berhasil.

    - Status "sent"    → catat jam_kirim (datetime ISO, bukan cuma tanggal)
    - Status "replied" → catat jam_reply + kirim notif Telegram (#5 + #17)
      (bisa dimatikan lewat kirim_notif=False kalau caller sudah kirim
      notifikasinya sendiri, mis. reply_assistant.py yang langsung kirim
      draft balasan ke chat yang sama)
    """
    if status_baru not in STATUS_VALID:
        log.info(f"[tracker] Status '{status_baru}' tidak valid. Pilih dari: {', '.join(STATUS_VALID)}")
        return False

    sent = muat_sent()
    ditemukan = False
    item_match = None
    for item in sent:
        if item.get("nomor_wa") == nomor_wa:
            item["status"] = status_baru
            sekarang = datetime.now().isoformat(timespec="seconds")
            if status_baru == "sent":
                item["jam_kirim"] = sekarang
            elif status_baru == "replied":
                item["jam_reply"] = sekarang
            item_match = item
            ditemukan = True
            break

    if not ditemukan:
        log.info(f"[tracker] Nomor WA '{nomor_wa}' tidak ditemukan di tabel sent.")
        return False

    backup.simpan()
    simpan_sent(sent)
    log.info(f"[tracker] Status '{nomor_wa}' diubah jadi '{status_baru}'.")

    # Notif Telegram kalau ada yang reply (#17)
    if status_baru == "replied" and item_match and kirim_notif:
        notif.notif_replied(
            nama=item_match.get("nama", ""),
            nomor_wa=nomor_wa,
            kategori=item_match.get("kategori", ""),
        )

    return True


def cek_followup(hari_batas: int = HARI_BATAS_FOLLOWUP_DEFAULT) -> int:
    """
    Bagian 'Observe' dari loop: cari lead berstatus 'sent' yang sudah
    lewat `hari_batas` hari sejak tanggal_kirim tapi belum ada respons,
    lalu tandai 'followup_due'. Lead yang sudah pernah di-follow-up sekali
    (follow_up_count >= 1) tidak ditandai lagi otomatis, biar tidak spam.

    builder.py akan membaca status 'followup_due' ini dan generate pesan
    follow-up di run berikutnya — menutup loop Think -> Act -> Observe.
    """
    sent = muat_sent()
    hari_ini = date.today()
    jumlah = 0

    for item in sent:
        if item.get("status") != "sent":
            continue
        tgl = item.get("jam_kirim") or item.get("tanggal_kirim")
        if not tgl:
            continue
        try:
            # jam_kirim = "2026-06-30T14:30:00", tanggal_kirim lama = "2026-06-30"
            tanggal_kirim = date.fromisoformat(tgl[:10])
        except ValueError:
            continue

        selisih_hari = (hari_ini - tanggal_kirim).days
        if selisih_hari >= hari_batas and item.get("follow_up_count", 0) < 1:
            item["status"] = "followup_due"
            jumlah += 1
            log.info(f"[tracker] '{item.get('nama')}' ditandai followup_due ({selisih_hari} hari belum respons).")

    if jumlah:
        simpan_sent(sent)

    log.info(f"[tracker] Selesai. {jumlah} lead ditandai 'followup_due'.")
    return jumlah


def cetak_ringkasan() -> None:
    sent = muat_sent()
    ringkasan = hitung_status(sent)
    print("[tracker] Ringkasan status lead:")
    print(f"  Total         : {ringkasan['total']}")
    print(f"  Draft         : {ringkasan['draft']}")
    print(f"  Pending       : {ringkasan['pending']}")
    print(f"  Sent          : {ringkasan['sent']}")
    print(f"  Followup due  : {ringkasan['followup_due']}")
    print(f"  Replied       : {ringkasan['replied']}")
    print(f"  Bounced       : {ringkasan['bounced']}")

    # A/B Testing stats
    ab: dict[str, dict[str, int]] = {}
    for item in sent:
        tid = item.get("template_id")
        if not tid:
            continue
        if tid not in ab:
            ab[tid] = {"sent": 0, "replied": 0}
        if item.get("status") in {"sent", "replied"}:
            ab[tid]["sent"] += 1
        if item.get("status") == "replied":
            ab[tid]["replied"] += 1
    if ab:
        print("\n[tracker] A/B Testing:")
        for tid, stat in sorted(ab.items()):
            rate = f"{stat['replied']/stat['sent']*100:.0f}%" if stat["sent"] else "n/a"
            print(f"  Template {tid}: {stat['replied']}/{stat['sent']} replied ({rate})")

    # Rata-rata waktu respons (jam_kirim → jam_reply)
    respons_jam = []
    for item in sent:
        if item.get("jam_kirim") and item.get("jam_reply"):
            try:
                kirim = datetime.fromisoformat(item["jam_kirim"])
                reply = datetime.fromisoformat(item["jam_reply"])
                respons_jam.append((reply - kirim).total_seconds() / 3600)
            except ValueError:
                pass
    if respons_jam:
        avg = sum(respons_jam) / len(respons_jam)
        print(f"\n[tracker] Rata-rata waktu respons: {avg:.1f} jam ({len(respons_jam)} data)")


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "update":
        nomor_wa = sys.argv[2]
        status_baru = sys.argv[3]
        update_status(nomor_wa, status_baru)
    elif len(sys.argv) >= 2 and sys.argv[1] == "followup":
        hari_batas = int(sys.argv[2]) if len(sys.argv) >= 3 else HARI_BATAS_FOLLOWUP_DEFAULT
        cek_followup(hari_batas)
    else:
        cetak_ringkasan()


if __name__ == "__main__":
    main()
