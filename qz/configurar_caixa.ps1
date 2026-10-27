# Instala o certificado no QZ Tray deste PC e reinicia o QZ. Pede administrador sozinho.
$ErrorActionPreference = "Stop"
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}
try {
    $cert = Join-Path $PSScriptRoot "digital-certificate.txt"
    if (-not (Test-Path $cert)) { throw "Nao achei qz\digital-certificate.txt. Rode antes: python qz\gerar_certificado.py" }

    $pasta = @("C:\Program Files\QZ Tray", "C:\Program Files (x86)\QZ Tray") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $pasta) { throw "QZ Tray nao esta instalado neste PC (baixe em qz.io/download)." }

    Copy-Item $cert (Join-Path $pasta "override.crt") -Force
    Write-Host "Certificado instalado em $pasta\override.crt"

    Get-Process -Name "qz-tray*" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    $exe = Join-Path $pasta "qz-tray.exe"
    if (Test-Path $exe) { Start-Process $exe; Write-Host "QZ Tray reiniciado." }
    else { Write-Host "Abra o QZ Tray manualmente (menu Iniciar)." }

    Write-Host ""
    Write-Host "PRONTO. Agora abra o /caixa/ no site, marque 'Remember this decision' e clique em Allow."
} catch {
    Write-Host "ERRO: $($_.Exception.Message)" -ForegroundColor Red
}
Read-Host "Pressione Enter para fechar"
