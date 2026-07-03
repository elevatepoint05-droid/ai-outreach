"""
tool_registry.py
=================
Daftar "tools" yang bisa dipilih oleh agent loop.
"""

from typing import Any, Callable

try:
    from . import db
    from .log_setup import buat_logger
except ImportError:
    import db
    from log_setup import buat_logger

log = buat_logger("tool_registry")


def _hitung_pending_dan_draft() -> int:
    """Helper: hitung lead yang statusnya pending/draft (belum sukses dikirim)."""
    sent = db.get_sent()
    return sum(1 for s in sent if s.get("status") in ("pending", "draft"))


def _tool_cek_status() -> dict:
    """Baca ringkasan status sistem sekarang — aman, tidak ubah apapun."""
    sent = db.get_sent()
    leads = db.get_leads()
    from collections import Counter
    breakdown = Counter(s.get("status", "pending") for s in sent)
    return {
        "total_leads": len(leads),
        "breakdown_status": dict(breakdown),
    }


def _tool_build_pesan(mode_draft: bool = False) -> dict:
    """Generate pesan WA baru untuk lead pending, pakai Groq."""
    try:
        from . import builder
    except ImportError:
        import builder
    sebelum = _hitung_pending_dan_draft()
    # kirim_notif=False: agent_loop.py sudah punya format_ringkasan() sendiri
    # yang melaporkan hasil akhir ke user, jadi tidak perlu notif individual
    # tiap kali tool ini dipanggil (hindari spam "Build selesai" berulang).
    builder.main(mode_draft=mode_draft, kirim_notif=False)
    sesudah = _hitung_pending_dan_draft()
    # ada_progress: True kalau jumlah pending berubah sebelum vs sesudah.
    # False artinya build_pesan tidak menghasilkan progress nyata — biar AI bisa
    # lihat eksplisit dari histori dan tidak mengulang tool yang sama sia-sia.
    return {
        "pesan_baru_dibuat": True,
        "pending_sebelum": sebelum,
        "pending_sesudah": sesudah,
        "ada_progress": sebelum != sesudah,
    }


def _tool_cek_followup() -> dict:
    """Tandai lead yang sudah lewat batas hari belum respons jadi followup_due."""
    try:
        from . import tracker
    except ImportError:
        import tracker
    # ada_progress: True kalau jumlah lead followup_due bertambah/berubah.
    # Kalau tetap sama, artinya cek_followup tidak menandai apa-apa baru — biar
    # AI lihat eksplisit dari histori dan tidak mengulang tool ini sia-sia.
    sebelum = sum(1 for s in db.get_sent() if s.get("status") == "followup_due")
    jumlah = tracker.cek_followup()
    sesudah = sum(1 for s in db.get_sent() if s.get("status") == "followup_due")
    return {
        "jumlah_ditandai_followup": jumlah,
        "followup_due_sebelum": sebelum,
        "followup_due_sesudah": sesudah,
        "ada_progress": sebelum != sesudah,
    }


def _tool_scan_website(force: bool = False) -> dict:
    """Cek lead mana yang sebenarnya sudah punya website (biar tidak ditawari lagi)."""
    try:
        from . import cek_website
    except ImportError:
        import cek_website
    # ada_progress: True kalau jumlah lead yang belum dicek websitenya berkurang.
    # Kalau tetap sama, artinya scan tidak memproses lead baru — sinyal buat AI
    # supaya tidak memilih scan_website berulang tanpa hasil.
    sebelum = sum(1 for l in db.get_leads() if not l.get("website_dicek"))
    cek_website.main(dry_run=False, force=force)
    sesudah = sum(1 for l in db.get_leads() if not l.get("website_dicek"))
    return {
        "scan_selesai": True,
        "belum_dicek_sebelum": sebelum,
        "belum_dicek_sesudah": sesudah,
        "ada_progress": sebelum != sesudah,
    }


def _tool_generate_report(hari: int = 7) -> dict:
    """Bikin laporan PDF ringkasan performa outreach N hari terakhir."""
    try:
        from . import report
    except ImportError:
        import report
    path = report.generate(hari=hari)
    return {"laporan_dibuat": str(path)}


def _tool_cek_kesehatan_sistem() -> dict:
    """
    Self-diagnostic — cek beberapa pola masalah umum di sistem, hal-hal
    yang biasanya baru ketauan pas user 'kepo' manual.
    """
    masalah_ditemukan = []

    try:
        from . import config as cfg
    except ImportError:
        import config as cfg

    portfolio = getattr(cfg, "PORTFOLIO_URL", "")
    pola_placeholder = ["contoh.website", "example.com", "yourwebsite", "placeholder", "domain-anda"]
    if portfolio and any(p in portfolio.lower() for p in pola_placeholder):
        masalah_ditemukan.append(
            f"PORTFOLIO_URL berisi placeholder ('{portfolio}'), bukan link asli — "
            f"pesan yang dikirim ke lead akan menyertakan link palsu."
        )

    try:
        histori_terbaru = db.get_agent_history(limit=5)
        if len(histori_terbaru) >= 3:
            run_id_terbaru = histori_terbaru[0].get("run_id")
            entri_run_sama = [h for h in histori_terbaru if h.get("run_id") == run_id_terbaru]
            tools_dipanggil = [h.get("tool") for h in entri_run_sama]
            for t in set(tools_dipanggil):
                if tools_dipanggil.count(t) >= 3 and t != "tidak_ada_aksi":
                    masalah_ditemukan.append(
                        f"Tool '{t}' terpanggil {tools_dipanggil.count(t)}x berturut-turut "
                        f"di run terakhir — kemungkinan ada loop pengulangan yang tidak semestinya."
                    )
    except Exception:
        pass

    if not getattr(cfg, "GROQ_API_KEY", ""):
        masalah_ditemukan.append("GROQ_API_KEY kosong — fitur generate pesan/laporan tidak akan berfungsi.")

    try:
        from . import tracker
        status_valid = tracker.STATUS_VALID
    except Exception:
        status_valid = {"draft", "pending", "sent", "replied", "bounced", "followup_due"}

    sent = db.get_sent()
    status_aneh = set(s.get("status") for s in sent if s.get("status") not in status_valid)
    if status_aneh:
        masalah_ditemukan.append(f"Ada lead dengan status tidak dikenal: {status_aneh}")

    # Kirim notif Telegram langsung kalau ada masalah — TIDAK bergantung ke
    # histori agent_loop yang dipotong 150 char, jadi detail masalah utuh
    # sampai ke user. Kalau sistem sehat, sengaja TIDAK kirim apa-apa biar
    # tidak spam tiap /agentloop jalan dan semua baik-baik saja.
    if masalah_ditemukan:
        try:
            from . import notif
        except ImportError:
            import notif
        baris = [f"{i}. {m}" for i, m in enumerate(masalah_ditemukan, 1)]
        teks = (
            "⚠️ <b>Sistem Health Check</b> — Ditemukan masalah:\n\n"
            + "\n".join(baris)
            + "\n\nIni butuh dibenerin manual (edit .env atau cek kode)."
        )
        try:
            notif.kirim(teks)
        except Exception as e:
            log.warning(f"[tool_registry] Gagal kirim notif health check: {e}")

    return {
        "jumlah_masalah": len(masalah_ditemukan),
        "masalah": masalah_ditemukan,
        "sistem_sehat": len(masalah_ditemukan) == 0,
    }


def _tool_tidak_ada_aksi() -> dict:
    """Tidak melakukan apa-apa — dipilih kalau kondisi sistem sudah baik."""
    return {"info": "Tidak ada aksi yang diperlukan saat ini."}


TOOLS: dict[str, dict[str, Any]] = {
    "cek_status": {
        "deskripsi": "Baca ringkasan kondisi sistem sekarang (jumlah lead per status). "
                     "Pakai ini kalau butuh info sebelum mutusin aksi lain.",
        "fungsi": _tool_cek_status,
        "butuh_args": [],
        "kategori": "aman",
    },
    "build_pesan": {
        "deskripsi": "Generate pesan WhatsApp baru untuk lead yang masih pending "
                     "(belum pernah dikirimi pesan). Pakai Groq API, ada biaya kuota.",
        "fungsi": _tool_build_pesan,
        "butuh_args": [],
        "kategori": "aksi",
    },
    "cek_followup": {
        "deskripsi": "Cek lead yang sudah dikirimi pesan tapi lebih dari beberapa hari "
                     "belum ada respons, tandai perlu di-follow-up.",
        "fungsi": _tool_cek_followup,
        "butuh_args": [],
        "kategori": "aksi",
    },
    "scan_website": {
        "deskripsi": "Cek lead mana yang ternyata sudah punya website sendiri, biar "
                     "tidak dikirimi pesan penawaran yang tidak relevan.",
        "fungsi": _tool_scan_website,
        "butuh_args": [],
        "kategori": "aksi",
    },
    "generate_report": {
        "deskripsi": "Bikin laporan PDF ringkasan performa outreach untuk periode tertentu.",
        "fungsi": _tool_generate_report,
        "butuh_args": [],
        "kategori": "aman",
    },
    "cek_kesehatan_sistem": {
        "deskripsi": "Self-diagnostic — cek apakah ada masalah tersembunyi di sistem "
                     "(placeholder link yang belum diisi, tool yang loop tanpa progress, "
                     "API key kosong, status data tidak dikenal). Jalankan ini SEBELUM "
                     "memilih tool lain kalau baru pertama kali putaran, atau kalau curiga "
                     "ada yang tidak beres.",
        "fungsi": _tool_cek_kesehatan_sistem,
        "butuh_args": [],
        "kategori": "aman",
    },
    "tidak_ada_aksi": {
        "deskripsi": "Pilih ini kalau setelah dicek, tidak ada yang perlu dilakukan sekarang.",
        "fungsi": _tool_tidak_ada_aksi,
        "butuh_args": [],
        "kategori": "aman",
    },
}


def daftar_tools_untuk_prompt() -> str:
    """Format daftar tools jadi teks yang bisa dimasukkan ke prompt AI."""
    baris = []
    for nama, info in TOOLS.items():
        baris.append(f"- {nama}: {info['deskripsi']}")
    return "\n".join(baris)


def eksekusi_tool(nama_tool: str, **kwargs) -> dict:
    """Eksekusi tool berdasarkan nama."""
    if nama_tool not in TOOLS:
        return {"error": f"Tool '{nama_tool}' tidak ada di registry. Tools tersedia: {list(TOOLS.keys())}"}

    tool = TOOLS[nama_tool]
    try:
        hasil = tool["fungsi"](**kwargs)
        return {"sukses": True, "hasil": hasil}
    except Exception as e:
        log.warning(f"[tool_registry] Tool '{nama_tool}' gagal dieksekusi: {e}")
        return {"sukses": False, "error": str(e)}
