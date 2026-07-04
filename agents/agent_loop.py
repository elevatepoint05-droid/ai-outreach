"""
agent_loop.py
=============
Loop Engineering beneran — Think → Act → Observe → Done?, AI yang mikir
dan milih tool sendiri, BUKAN if/else hardcoded.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

try:
    from . import db, tool_registry
    from .log_setup import buat_logger
    from . import config as cfg
except ImportError:
    import db, tool_registry
    from log_setup import buat_logger
    import config as cfg

log = buat_logger("agent_loop")

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

MODEL = cfg.GROQ_MODEL if hasattr(cfg, "GROQ_MODEL") else "llama-3.1-8b-instant"

PROMPT_SISTEM = """Kamu adalah AI agent yang mengelola sistem outreach WhatsApp
untuk freelancer web developer di Indonesia. Tugasmu: putuskan SATU aksi paling
tepat untuk diambil sekarang.

Kamu HANYA boleh memilih dari daftar tools yang tersedia.

=== PEMAHAMAN WAJIB ===
- Status "pending" = pesan SUDAH DIBUAT, tinggal dikirim manual. BUKAN berarti perlu di-generate lagi.
- Status "sent" = pesan sudah dikirim, menunggu balasan.
- "leads_belum_diresearch" = lead yang belum punya insight personalisasi di DB.
- "ada_progress: false" di histori = tool sudah jalan tapi tidak ada perubahan -> JANGAN ulangi.

=== URUTAN PRIORITAS IDEAL ===
1. research_lead (kalau ada leads_belum_diresearch > 0) -> insight tersimpan ke DB
2. build_pesan (kalau ada lead baru tanpa pesan) -> pakai insight yang sudah ada
3. cek_followup (kalau ada lead "sent" sudah lama tidak balas)
4. tidak_ada_aksi (kalau semua sudah tertangani)

=== CONTOH KEPUTUSAN BAIK vs BURUK ===

KONDISI: leads_belum_diresearch=5, pending=10, sent=8
BAIK: {"tool": "research_lead", "alasan": "Ada 5 lead belum punya insight, research dulu sebelum build pesan biar lebih personal", "selesai": false}
BURUK: {"tool": "build_pesan", "alasan": "Ada 10 pending"} <- salah, pending = sudah ada pesan

KONDISI: pending=37, sent=12, leads_belum_diresearch=0
BAIK: {"tool": "tidak_ada_aksi", "alasan": "Semua lead sudah punya pesan pending, tidak ada yang perlu dikerjakan otomatis sekarang", "selesai": true}
BURUK: {"tool": "build_pesan", "alasan": "Masih banyak pending"} <- SALAH PAHAM

KONDISI: histori=[cek_followup -> ada_progress:false], sent=5
BAIK: {"tool": "tidak_ada_aksi", "alasan": "cek_followup sudah jalan tapi tidak ada progress, tidak ada aksi lain yang diperlukan", "selesai": true}
BURUK: {"tool": "cek_followup", "alasan": "Masih ada yang sent"} <- DILARANG, ulangi tool yang tidak progress

KONDISI: semua sudah rapi, tidak ada yang perlu dikerjakan
BAIK: {"tool": "tidak_ada_aksi", "alasan": "Sistem sudah rapi", "selesai": true}

KONDISI: histori menunjukkan ada_progress:false berulang, tidak tahu harus ngapain
BAIK: {"tool": "eskalasi", "alasan": "Sudah coba 2 tool berbeda tapi tidak ada progress, butuh keputusan manual", "selesai": true}

=== ATURAN ANTI-PENGULANGAN (WAJIB) ===
Sebelum pilih tool, cek histori. Kalau tool yang sama sudah dipanggil dan
hasilnya "ada_progress": false -> DILARANG pilih tool itu lagi.
Berlaku untuk SEMUA tool: build_pesan, cek_followup, scan_website, research_lead.
Kalau semua opsi sudah dicoba tanpa progress -> pilih eskalasi, bukan nebak-nebak.

=== FORMAT JAWABAN ===
Jawab HANYA dalam format JSON, tidak ada teks lain:
{"tool": "<nama_tool>", "alasan": "<1 kalimat alasan singkat>", "selesai": <true/false>}

"selesai": true kalau tidak perlu putaran berikutnya."""


def _snapshot_kondisi() -> dict:
    """Ambil snapshot kondisi sistem sekarang buat dikasih ke AI."""
    sent = db.get_sent()
    leads = db.get_leads()
    from collections import Counter
    breakdown = Counter(s.get("status", "pending") for s in sent)
    belum_dicek_website = sum(1 for l in leads if not l.get("website_dicek"))
    belum_diresearch = sum(
        1 for l in leads
        if l.get("research_insight") is None
        and not l.get("ada_website")
    )
    return {
        "total_leads": len(leads),
        "breakdown_status": dict(breakdown),
        "leads_belum_dicek_website": belum_dicek_website,
        "leads_belum_diresearch": belum_diresearch,
        "waktu_sekarang": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _think(histori_aksi: list[dict], api_key: str) -> dict:
    """Satu panggilan LLM: kasih kondisi sistem + histori, minta AI pilih tool berikutnya."""
    kondisi = _snapshot_kondisi()
    daftar_tools = tool_registry.daftar_tools_untuk_prompt()

    histori_teks = "Belum ada aksi diambil di putaran ini."
    if histori_aksi:
        baris = [
            f"{i+1}. Tool: {h['tool']} -> Hasil: {json.dumps(h['hasil'], ensure_ascii=False)[:150]}"
            for i, h in enumerate(histori_aksi)
        ]
        histori_teks = "\n".join(baris)

    konteks_user = (
        f"KONDISI SISTEM SEKARANG:\n{json.dumps(kondisi, ensure_ascii=False, indent=2)}\n\n"
        f"TOOLS TERSEDIA:\n{daftar_tools}\n\n"
        f"HISTORI AKSI DI PUTARAN INI:\n{histori_teks}\n\n"
        "Pilih SATU tool untuk dijalankan sekarang."
    )

    client = Groq(api_key=api_key)
    respon = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": PROMPT_SISTEM},
            {"role": "user", "content": konteks_user},
        ],
        temperature=0.3,
        max_tokens=150,
    )
    teks = respon.choices[0].message.content.strip()
    teks = teks.replace("```json", "").replace("```", "").strip()

    try:
        keputusan = json.loads(teks)
        if "tool" not in keputusan:
            raise ValueError("Field 'tool' tidak ada di respons AI")
        return keputusan
    except Exception as e:
        log.warning(f"[agent_loop] Gagal parse keputusan AI: {e}. Raw: {teks[:200]}")
        return {"tool": "tidak_ada_aksi", "alasan": f"Parsing gagal, fallback aman: {e}", "selesai": True}


def jalankan_loop(max_iterasi: int = 5) -> list[dict]:
    """
    Jalankan Think -> Act -> Observe -> Done? sampai AI bilang selesai
    atau max_iterasi tercapai. Tiap putaran otomatis dicatat ke database
    (tabel agent_history) supaya bisa ditampilkan di dashboard.
    """
    import uuid

    api_key = cfg.GROQ_API_KEY
    if not api_key:
        log.warning("[agent_loop] GROQ_API_KEY belum diset, tidak bisa jalankan agent loop.")
        # Bentuk entri ini sengaja disamakan sama entri histori normal (ada key
        # "hasil" berisi dict) supaya format_ringkasan() tidak KeyError waktu
        # baca h["hasil"] — tanpa ini, error asli ketutup jadi "❌ Agent loop
        # error: 'hasil'" yang gak informatif buat user.
        return [{
            "putaran": 0,
            "tool": "error",
            "alasan": "GROQ_API_KEY belum diset di .env",
            "hasil": {"sukses": False, "error": "GROQ_API_KEY belum diset"},
        }]

    run_id  = uuid.uuid4().hex[:8]
    histori: list[dict] = []

    for putaran in range(1, max_iterasi + 1):
        log.info(f"[agent_loop] === Putaran {putaran}/{max_iterasi} (run={run_id}) ===")

        keputusan = _think(histori, api_key)
        nama_tool = keputusan.get("tool", "tidak_ada_aksi")
        alasan    = keputusan.get("alasan", "")
        selesai   = keputusan.get("selesai", False)

        log.info(f"[agent_loop] THINK -> pilih '{nama_tool}': {alasan}")

        # ACT — pass argumen dinamis berdasarkan butuh_args tool (misal 'alasan'
        # buat tool eskalasi, yang sudah ada di JSON keputusan AI).
        tool_info    = tool_registry.TOOLS.get(nama_tool, {})
        butuh_args   = tool_info.get("butuh_args", [])
        kwargs_tool  = {arg: keputusan.get(arg, "") for arg in butuh_args if arg in keputusan}
        hasil_eksekusi = tool_registry.eksekusi_tool(nama_tool, **kwargs_tool)

        entri_histori = {
            "putaran": putaran,
            "tool": nama_tool,
            "alasan": alasan,
            "hasil": hasil_eksekusi,
        }
        histori.append(entri_histori)
        log.info(f"[agent_loop] OBSERVE -> {hasil_eksekusi}")

        try:
            db.log_agent_aksi(
                run_id=run_id, putaran=putaran, tool=nama_tool, alasan=alasan,
                hasil=hasil_eksekusi, sukses=hasil_eksekusi.get("sukses", False),
            )
        except Exception as e:
            log.warning(f"[agent_loop] Gagal catat ke database (tidak fatal): {e}")

        if nama_tool == "tidak_ada_aksi" or selesai:
            log.info(f"[agent_loop] Loop selesai di putaran {putaran} (AI memutuskan selesai).")
            break
    else:
        log.info(f"[agent_loop] Loop berhenti karena mencapai max_iterasi ({max_iterasi}).")

    return histori


def format_ringkasan(histori: list[dict]) -> str:
    """Format histori jadi teks ringkas buat dikirim ke Telegram."""
    if not histori:
        return "Tidak ada aksi yang diambil."

    aksi_nyata = [h for h in histori if h["tool"] not in ("tidak_ada_aksi", "eskalasi")]
    if not aksi_nyata:
        if any(h["tool"] == "eskalasi" for h in histori):
            return "🙋 Agent loop mengeskalasi ke kamu — cek notif eskalasi di atas."
        return "🧠 Agent loop jalan, tapi tidak ada aksi yang diperlukan saat ini."

    baris = ["🧠 <b>Agent loop mengambil keputusan:</b>\n"]
    for h in aksi_nyata:
        status = "✅" if h["hasil"].get("sukses") else "⚠️"
        baris.append(f"{status} <b>{h['tool']}</b> — {h['alasan']}")
    return "\n".join(baris)


def main() -> None:
    args = sys.argv[1:]
    max_iter = 5
    if "--max-iterasi" in args:
        try:
            idx = args.index("--max-iterasi")
            max_iter = int(args[idx + 1])
        except (IndexError, ValueError):
            pass

    print("[agent_loop] Menjalankan Think-Act-Observe loop...")
    histori = jalankan_loop(max_iterasi=max_iter)

    print("\n" + "=" * 60)
    for h in histori:
        print(f"Putaran {h.get('putaran', '?')}: {h.get('tool')} — {h.get('alasan', '')}")
        print(f"  Hasil: {h.get('hasil')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
