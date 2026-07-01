@echo off
REM =====================================================================
REM run_daily.bat
REM Jalanin satu siklus harian outreach otomatis.
REM
REM Setup Task Scheduler Windows (sekali saja):
REM   1. Buka Task Scheduler → Create Basic Task
REM   2. Name: "Outreach Daily"
REM   3. Trigger: Daily, jam 08:00
REM   4. Action: Start a program
REM      Program: C:\Users\uwais\Downloads\Project 1\ai-outreach\run_daily.bat
REM   5. Finish
REM
REM Atau lewat CMD (jalankan sebagai Administrator):
REM   schtasks /create /tn "OutreachDaily" /tr "C:\Users\uwais\Downloads\Project 1\ai-outreach\run_daily.bat" /sc daily /st 08:00 /f
REM =====================================================================

cd /d %~dp0
python main.py daily
pause
