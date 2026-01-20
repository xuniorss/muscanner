param(
  [Parameter(Mandatory=$true)][string]$ReleaseVersion,
  [switch]$NoPush,
  [switch]$CreateRelease
)

$ErrorActionPreference = "Stop"

if ($ReleaseVersion -notmatch '^\d+\.\d+\.\d+$') {
  throw "Versao invalida: $ReleaseVersion (use X.Y.Z, ex: 0.1.5)"
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

function Run([string]$cmd) {
  Write-Host ">> $cmd"
  cmd.exe /c $cmd
  if ($LASTEXITCODE -ne 0) { throw "Falhou: $cmd (exit=$LASTEXITCODE)" }
}

# Atualiza APP_VERSION no gui_scanner_pro.py
$gui = Join-Path $repoRoot "gui_scanner_pro.py"
if (!(Test-Path $gui)) { throw "Nao achei: $gui" }

$content = Get-Content $gui -Raw -Encoding UTF8
$updated = [regex]::Replace($content, 'APP_VERSION\s*=\s*["''][^"''\r\n]+["'']', "APP_VERSION = `"$ReleaseVersion`"")
if ($content -notmatch 'APP_VERSION\s*=\s*["''][^"''\r\n]+["'']') {
  Write-Host "Aviso: nao encontrei APP_VERSION para substituir (verifique gui_scanner_pro.py)."
} elseif ($updated -eq $content) {
  Write-Host "APP_VERSION ja esta em $ReleaseVersion (nada para alterar)."
} else {
  Set-Content -Path $gui -Value $updated -Encoding UTF8
  Write-Host "APP_VERSION atualizado para $ReleaseVersion"
}

Run "git add gui_scanner_pro.py .github\workflows\release.yml updater_github.py DEPLOY.md tools 2>nul"

$changes = (git status --porcelain)
if ([string]::IsNullOrWhiteSpace($changes)) {
  Write-Host "Sem mudancas para commit. Continuando (tag/push)."
} else {
  Run "git commit -m ""Release v$ReleaseVersion"""
}

$tag = "v$ReleaseVersion"
$existing = (git tag -l $tag)
if ($existing) { throw "Tag $tag ja existe." }
Run "git tag $tag"

if (-not $NoPush) {
  Run "git push"
  Run "git push origin $tag"
} else {
  Write-Host "NoPush habilitado: nao fiz push."
}

if ($CreateRelease) {
  $gh = Get-Command gh -ErrorAction SilentlyContinue
  if (-not $gh) {
    Write-Host "gh CLI nao encontrado. Instale GitHub CLI ou rode sem -CreateRelease."
  } else {
    Run "gh release create $tag --title ""$tag"" --generate-notes"
  }
}

Write-Host "OK: Release preparada para $tag"
