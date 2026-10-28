# Prepara o QZ Tray do PC do caixa para imprimir as notinhas sem pedir
# permissao toda hora. Pede administrador sozinho.
#
#   1. Baixa o certificado DO PROPRIO SITE (o mesmo que a Vercel usa) e
#      instala como override.crt na pasta do QZ Tray.
#   2. Se o site foi bloqueado no QZ ("Block"), guarda e limpa a lista de
#      bloqueados (blocked.dat vira blocked.dat.bak-...).
#   3. Reinicia o QZ Tray.
#
# Uso: dois cliques em configurar_caixa.bat e digite o endereco do sistema
# (ex.: https://cantinho.vercel.app). Tambem aceita: configurar_caixa.bat https://...
param([string]$Site = "")
$ErrorActionPreference = "Stop"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    $argumentos = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($Site) { $argumentos += " -Site `"$Site`"" }
    Start-Process powershell -Verb RunAs -ArgumentList $argumentos
    exit
}

try {
    $pasta = @("C:\Program Files\QZ Tray", "C:\Program Files (x86)\QZ Tray", "$env:LOCALAPPDATA\Programs\QZ Tray") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $pasta) { throw "QZ Tray nao esta instalado neste PC. Baixe e instale em https://qz.io/download e rode este arquivo de novo." }

    # --- 1. o certificado -----------------------------------------------------
    if (-not $Site) {
        Write-Host "Endereco do sistema (ex.: https://cantinho.vercel.app)."
        Write-Host "Deixe em branco para usar o override.crt baixado na engrenagem da impressora."
        $Site = (Read-Host "Endereco").Trim()
    }
    $temporario = Join-Path $env:TEMP "qz-override.crt"
    if ($Site) {
        if ($Site -notmatch "^https?://") { $Site = "https://" + $Site }
        $url = $Site.TrimEnd("/") + "/api/qz/certificado/"
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Write-Host "Baixando o certificado de $url ..."
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $temporario
    } else {
        $baixado = Get-ChildItem (Join-Path $env:USERPROFILE "Downloads") -Filter "override*.crt" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        $local = Join-Path $PSScriptRoot "digital-certificate.txt"
        if ($baixado) { Copy-Item $baixado.FullName $temporario -Force; Write-Host "Usando $($baixado.FullName)" }
        elseif (Test-Path $local) { Copy-Item $local $temporario -Force; Write-Host "Usando $local" }
        else { throw "Nao achei o certificado. Rode de novo e digite o endereco do sistema." }
    }
    $conteudo = Get-Content $temporario -Raw
    if ($conteudo -notmatch "BEGIN CERTIFICATE") {
        throw "O que veio nao e um certificado. Confira o endereco e se as variaveis QZ_CERTIFICADO/QZ_CHAVE_PRIVADA estao na Vercel (com novo deploy)."
    }
    Copy-Item $temporario (Join-Path $pasta "override.crt") -Force
    Write-Host "Certificado instalado em $pasta\override.crt" -ForegroundColor Green

    # --- 2. sites bloqueados no QZ ---------------------------------------------
    foreach ($dados in @((Join-Path $env:APPDATA "qz"), (Join-Path $env:ProgramData "qz"))) {
        $bloqueados = Join-Path $dados "blocked.dat"
        if (Test-Path $bloqueados) {
            $copia = "$bloqueados.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
            Move-Item $bloqueados $copia -Force
            Write-Host "Lista de sites bloqueados do QZ limpa (copia em $copia)."
        }
    }

    # --- 3. reinicia o QZ Tray -------------------------------------------------
    Get-Process -Name "qz-tray*" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    $exe = Join-Path $pasta "qz-tray.exe"
    if (Test-Path $exe) { Start-Process $exe; Write-Host "QZ Tray reiniciado." -ForegroundColor Green }
    else { Write-Host "Abra o QZ Tray manualmente (menu Iniciar)." }

    Write-Host ""
    Write-Host "PRONTO. Agora, no Chrome:"
    Write-Host "  1. Abra o /caixa/ do sistema."
    Write-Host "  2. Se o Chrome perguntar se o site pode acessar este computador / a rede local: Permitir."
    Write-Host "  3. Na janela do QZ: marque 'Remember this decision' e clique em Allow."
    Write-Host "  4. Engrenagem da impressora > escolha a Bematech > Imprimir teste."
} catch {
    Write-Host "ERRO: $($_.Exception.Message)" -ForegroundColor Red
}
Read-Host "Pressione Enter para fechar"
