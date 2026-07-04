"""
tool_registry.py
=================
Daftar "tools" yang bisa dipilih oleh agent loop.
"""

from pathlib import Path
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


def _tool_research_lead() -> dict:
    """
    Riset lead yang belum punya insight personalisasi (max 3 per panggilan).
    Hasil disimpan ke DB supaya build_pesan tinggal ambil, tidak perlu
    generate ulang lewat Groq tiap kali — hemat API call.
    """
    try:
        from . import sub_agent_research
    except ImportError:
        import sub_agent_research

    leads = db.get_leads_belum_diresearch(limit=3)
    jumlah_diresearch = 0
    jumlah_dapat_insight = 0
    for lead in leads:
        insight = sub_agent_research.riset_lead(lead)
        jumlah_diresearch += 1
        if insight:
            db.simpan_research_insight(lead.get("nomor_wa"), insight)
            jumlah_dapat_insight += 1

    # ada_progress: True hanya kalau ada insight baru yang benar-benar tersimpan
    # ke DB. Kalau tidak ada perubahan nyata, agent loop tidak akan mengulang.
    return {
        "jumlah_diresearch": jumlah_diresearch,
        "jumlah_dapat_insight": jumlah_dapat_insight,
        "ada_progress": jumlah_dapat_insight > 0,
    }


def _tool_cek_keamanan_sistem() -> dict:
    """
    Security check — versi khusus keamanan dari cek_kesehatan_sistem.
    Sifatnya 'ketat tapi fleksibel': scan dan LAPORKAN semua celah yang
    ketemu (ketat), tapi TIDAK block/ubah apapun secara otomatis.
    """
    import subprocess
    temuan = []

    try:
        from . import config as cfg
    except ImportError:
        import config as cfg

    base_dir = Path(__file__).resolve().parent.parent

    # 1. Cek .env pernah ke-commit ke git history
    try:
        hasil_git = subprocess.run(
            ["git", "-C", str(base_dir), "log", "--all", "--oneline", "--", ".env"],
            capture_output=True, text=True, timeout=10,
        )
        if hasil_git.returncode == 0 and hasil_git.stdout.strip():
            temuan.append({
                "level": "KRITIS",
                "masalah": ".env pernah ke-commit ke git history! Secret (API key, bot token) "
                           "sudah tersimpan permanen di history, bahkan kalau file dihapus sekarang.",
                "saran": "Rotate SEMUA secret (Groq API key, Telegram bot token) segera, "
                         "lalu pertimbangkan bersihkan git history (git filter-repo).",
            })
    except Exception:
        pass

    # 2. Cek .env ada di .gitignore
    gitignore_path = base_dir / ".gitignore"
    if gitignore_path.exists():
        isi_gitignore = gitignore_path.read_text()
        if ".env" not in isi_gitignore:
            temuan.append({
                "level": "TINGGI",
                "masalah": ".env TIDAK ada di .gitignore — resiko ke-commit tanpa sengaja.",
                "saran": "Tambahkan baris '.env' ke file .gitignore.",
            })
    else:
        temuan.append({
            "level": "TINGGI",
            "masalah": ".gitignore tidak ditemukan sama sekali.",
            "saran": "Buat .gitignore, minimal exclude .env dan file data (leads.json, outreach.db).",
        })

    # 3. Cek DASHBOARD_PASSWORD
    dashboard_password = getattr(cfg, "DASHBOARD_PASSWORD", None)
    if dashboard_password == "" or dashboard_password is None:
        temuan.append({
            "level": "SEDANG",
            "masalah": "Dashboard belum diproteksi password. Aman untuk pemakaian lokal "
                       "di laptop sendiri, TAPI WAJIB diisi sebelum deploy ke VPS/server publik.",
            "saran": "Set DASHBOARD_PASSWORD di .env sebelum deploy ke server yang bisa "
                     "diakses dari internet.",
        })

    # 4. Cek secret hardcoded di kode (bukan di .env)
    try:
        import re
        pola_secret = re.compile(r'gsk_[A-Za-z0-9]{20,}|[0-9]{8,10}:[A-Za-z0-9_-]{30,}')
        file_dicek = ["main.py"] + [str(p) for p in (base_dir / "agents").glob("*.py")]
        for nama_file in file_dicek:
            path_file = base_dir / nama_file if not nama_file.startswith(str(base_dir)) else Path(nama_file)
            if path_file.exists():
                isi = path_file.read_text(errors="ignore")
                if pola_secret.search(isi):
                    temuan.append({
                        "level": "KRITIS",
                        "masalah": f"Kemungkinan API key/token ter-hardcode langsung di {path_file.name}.",
                        "saran": f"Cek isi {path_file.name}, pastikan semua secret diambil dari os.getenv().",
                    })
    except Exception:
        pass

    return {
        "jumlah_temuan": len(temuan),
        "temuan": temuan,
        "aman": len(temuan) == 0,
    }


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


def _tool_eskalasi(alasan: str = "") -> dict:
    """
    Eskalasi — AI 'nyerah dengan elegan' dan minta keputusan manusia,
    BUKAN nebak-nebak terus. Beda dari tidak_ada_aksi (artinya 'semua
    beres'), eskalasi artinya 'saya tidak yakin/stuck, butuh manusia'.
    """
    try:
        from . import notif
    except ImportError:
        import notif

    alasan_final = alasan.strip() if alasan else "AI tidak menyebutkan alasan spesifik."

    try:
        notif.kirim(
            f"🙋 <b>Agent Loop Eskalasi</b>\n\n"
            f"AI memilih untuk TIDAK melanjutkan otomatis dan minta keputusan kamu:\n\n"
            f"{alasan_final}\n\n"
            f"<i>Ini bukan error — ini AI yang 'jujur' bilang tidak yakin, "
            f"daripada asal nebak aksi.</i>"
        )
    except Exception:
        pass

    return {"dieskalasi": True, "alasan": alasan_final}


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
    "research_lead": {
        "deskripsi": "Riset lead yang belum punya insight personalisasi — panggil "
                     "sebelum build_pesan supaya pesan lebih personal. Max 3 lead per "
                     "panggilan, hemat API call karena insight disimpan ke DB dan tidak "
                     "perlu di-generate ulang.",
        "fungsi": _tool_research_lead,
        "butuh_args": [],
        "kategori": "aksi",
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
    "cek_keamanan_sistem": {
        "deskripsi": "Security check — cek celah keamanan spesifik (secret ke-expose "
                     "di git history, .env tidak di-gitignore, dashboard tanpa password, "
                     "API key hardcoded di kode). Beda dari cek_kesehatan_sistem yang "
                     "fokus ke bug fungsional, ini khusus fokus ke resiko keamanan. "
                     "Jalankan ini terutama sebelum rencana deploy ke server/VPS.",
        "fungsi": _tool_cek_keamanan_sistem,
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
    "eskalasi": {
        "deskripsi": "Pilih ini kalau kamu TIDAK YAKIN aksi apa yang tepat, atau "
                     "sudah coba beberapa tool tapi tidak ada progress (ada_progress: "
                     "false berulang), atau menemukan situasi yang di luar aturan yang "
                     "diberikan. LEBIH BAIK eskalasi dan tanya user daripada asal menebak "
                     "atau mengulang tool yang sama tanpa hasil. Sertakan alasan spesifik "
                     "di parameter 'alasan'.",
        "fungsi": _tool_eskalasi,
        "butuh_args": ["alasan"],
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
