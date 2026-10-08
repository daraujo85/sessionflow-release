# SessionFlow — habilita acesso remoto temporario para o Diego configurar
# esta maquina (worker SessionFlow do Lucas). Rodar como Administrador.
#
# O que faz:
#   1. Habilita o OpenSSH Server (feature nativa do Windows, vem desligada)
#   2. Deixa o servico sshd iniciando sozinho no boot + inicia agora
#   3. Libera a porta 22 no firewall do Windows
#   4. Baixa o ngrok.exe (nao configura token nem abre o tunel — isso e
#      manual, feito pelo Lucas, ver instrucoes no final)
#
# Nada fica exposto de fora ate o Lucas rodar o ngrok manualmente. Ao
# terminar a sessao remota, fechar a janela do ngrok derruba o tunel na
# hora — o SSH em si continua acessivel so dentro da LAN.

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

Write-Host "== 1. Habilitando OpenSSH Server ==" -ForegroundColor Cyan
$capability = Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'
if ($capability.State -ne 'Installed') {
    Add-WindowsCapability -Online -Name $capability.Name
} else {
    Write-Host "OpenSSH Server ja instalado."
}

Write-Host "== 2. Configurando servico sshd (auto-start) ==" -ForegroundColor Cyan
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd

Write-Host "== 3. Liberando porta 22 no firewall ==" -ForegroundColor Cyan
if (-not (Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (sshd)" `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
} else {
    Write-Host "Regra de firewall ja existe."
}

Write-Host "== 4. Baixando ngrok ==" -ForegroundColor Cyan
$ngrokDir = "$env:USERPROFILE\ngrok"
New-Item -ItemType Directory -Force -Path $ngrokDir | Out-Null
$ngrokZip = "$ngrokDir\ngrok.zip"
Invoke-WebRequest -Uri "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip" -OutFile $ngrokZip
Expand-Archive -Path $ngrokZip -DestinationPath $ngrokDir -Force
Remove-Item $ngrokZip

Write-Host ""
Write-Host "== Pronto! Faltam so 2 passos manuais (1x, ~1 min): ==" -ForegroundColor Green
Write-Host "1. Criar conta gratis em https://ngrok.com (se ainda nao tiver)."
Write-Host "2. Rodar, neste mesmo terminal:"
Write-Host "   cd $ngrokDir"
Write-Host "   .\ngrok.exe config add-authtoken <SEU_TOKEN>"
Write-Host "   .\ngrok.exe tcp 22"
Write-Host ""
Write-Host "Vai aparecer algo como 'tcp://X.tcp.ngrok.io:PORTA' — manda esse"
Write-Host "endereco + seu usuario Windows + senha (so enquanto durar a"
Write-Host "configuracao) pro Diego. Quando terminar, so fechar essa janela"
Write-Host "do ngrok que o tunel cai na hora." -ForegroundColor Yellow
