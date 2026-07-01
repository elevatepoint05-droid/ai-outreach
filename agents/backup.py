"""
backup.py
=========
Auto-backup data/outreach.db ke data/backup/ sebelum tiap build run.
Format nama file: outreach_20260630_143012.db

(#13) Dulunya backup sent.json + leads.json terpisah. Sekarang keduanya
sudah pindah ke satu file SQLite (data/outreach.db), jadi cukup backup
satu file itu saja.

Cara pakai:
    from agents import backup
    backup.simpan()      # dipanggil otomatis dari builder.main()

Maksimal 10 backup disimpan (yang lebih lama dihapus otomatis).
"""

import shutil
from datetime import datetime
from pathlib import Path

BASE_DIR    = Path(__file__).resolve().parent.parent
BACKUP_DIR  = BASE_DIR / "data" / "backup"
MAKS_BACKUP = 10
FILE_TARGET = ["outreach.db"]


def simpan() -> None:
    """Backup data/outreach.db ke data/backup/ dengan timestamp."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    cap = datetime.now().strftime("%Y%m%d_%H%M%S")

    for nama_file in FILE_TARGET:
        sumber = BASE_DIR / "data" / nama_file
        if not sumber.exists():
            continue
        ekstensi = sumber.suffix  # ".db" (atau ".json" untuk file lama kalau masih ada)
        tujuan = BACKUP_DIR / f"{sumber.stem}_{cap}{ekstensi}"
        shutil.copy2(sumber, tujuan)

    _hapus_lama()


def _hapus_lama() -> None:
    """Hapus backup paling lama kalau sudah melebihi MAKS_BACKUP per jenis file."""
    for nama_file in FILE_TARGET:
        path = Path(nama_file)
        stem, ekstensi = path.stem, path.suffix
        backups = sorted(BACKUP_DIR.glob(f"{stem}_*{ekstensi}"))
        lebih = backups[: max(0, len(backups) - MAKS_BACKUP)]
        for f in lebih:
            f.unlink(missing_ok=True)
