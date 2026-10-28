@echo off
REM Instala o certificado do site no QZ Tray deste PC (ver configurar_caixa.ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configurar_caixa.ps1" %*
