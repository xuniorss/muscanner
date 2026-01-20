# -*- coding: utf-8 -*-
"""Auto-update simples via GitHub Releases (Windows).

Como funciona:
- Busca o release mais recente em: https://api.github.com/repos/<owner>/<repo>/releases/latest
- Escolhe um asset .exe (ou pelo nome preferido)
- Baixa o exe para %TEMP%\MuScannerUpdate
- Aplica a atualizacao via PowerShell (suporta caminhos com Unicode/acentos)

Observacoes:
- Funciona melhor com repo PUBLICO. Para repo privado voce precisaria de token.
- No Windows, o executavel em execucao fica bloqueado; por isso usamos um processo externo
  (PowerShell) para esperar o PID e trocar o arquivo.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

UA = "MuScanner-Updater/1.1"


@dataclass
class AssetInfo:
    name: str
    url: str  # browser_download_url
    size: int


@dataclass
class ReleaseInfo:
    tag_name: str
    name: str
    body: str
    assets: List[AssetInfo]


def _gh_get_json(url: str, *, timeout: int = 12) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw.decode("utf-8", errors="replace"))


def get_latest_release(owner: str, repo: str) -> ReleaseInfo:
    data = _gh_get_json(f"https://api.github.com/repos/{owner}/{repo}/releases/latest")

    assets: List[AssetInfo] = []
    for a in data.get("assets", []) or []:
        assets.append(
            AssetInfo(
                name=str(a.get("name", "")),
                url=str(a.get("browser_download_url", "")),
                size=int(a.get("size", 0) or 0),
            )
        )

    return ReleaseInfo(
        tag_name=str(data.get("tag_name", "")) or str(data.get("name", "")) or "",
        name=str(data.get("name", "")) or "",
        body=str(data.get("body", "")) or "",
        assets=assets,
    )


def _parse_semver(tag: str) -> Tuple[int, int, int, str]:
    """Extrai (major, minor, patch, extra) de algo como 'v1.2.3' ou '1.2.3-beta'."""
    t = (tag or "").strip()
    t = t.lstrip("vV").strip()

    m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$", t)
    if not m:
        return (0, 0, 0, t)

    major = int(m.group(1) or 0)
    minor = int(m.group(2) or 0)
    patch = int(m.group(3) or 0)
    extra = (m.group(4) or "").strip()
    return (major, minor, patch, extra)


def is_newer(latest_tag: str, current_tag: str) -> bool:
    la = _parse_semver(latest_tag)
    cu = _parse_semver(current_tag)

    if la[:3] != cu[:3]:
        return la[:3] > cu[:3]

    l_extra, c_extra = la[3], cu[3]
    if not l_extra and c_extra:
        return True
    if l_extra and not c_extra:
        return False
    return l_extra > c_extra


def pick_asset(release: ReleaseInfo, *, preferred_name: str = "") -> Optional[AssetInfo]:
    assets = release.assets or []
    if not assets:
        return None

    if preferred_name:
        for a in assets:
            if a.name.lower() == preferred_name.lower():
                return a

    for a in assets:
        if a.name.lower().endswith(".exe") and a.url:
            return a

    return None


def download_asset(
    asset: AssetInfo,
    *,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    timeout: int = 30,
) -> Path:
    """Baixa asset para pasta temp e retorna Path."""
    if not asset.url:
        raise RuntimeError("Asset sem URL de download.")

    tmpdir = Path(tempfile.gettempdir()) / "MuScannerUpdate"
    tmpdir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", asset.name or "update.exe")
    dest = tmpdir / f"{int(time.time())}_{safe_name}"

    req = urllib.request.Request(asset.url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress_cb:
                    try:
                        progress_cb(done, total)
                    except Exception:
                        pass

    return dest


_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'

$Pid = [int]$env:MUSCANNER_PID
$Old = $env:MUSCANNER_OLD
$New = $env:MUSCANNER_NEW
$Log = $env:MUSCANNER_LOG
$AppDir = $env:MUSCANNER_APPDIR
$ExeName = $env:MUSCANNER_EXENAME
$ArgsJson = $env:MUSCANNER_ARGS_JSON

function Write-Log([string]$m) {
  try {
    $ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    Add-Content -LiteralPath $Log -Value "$ts  $m"
  } catch { }
}

Write-Log "==== MuScanner Update ===="
Write-Log "PID=$Pid"
Write-Log "OLD=$Old"
Write-Log "NEW=$New"

# Espera o app fechar
try { Wait-Process -Id $Pid -ErrorAction SilentlyContinue } catch { }
Start-Sleep -Milliseconds 300

# Args
$argsList = @()
if ($ArgsJson) {
  try {
    $tmp = $ArgsJson | ConvertFrom-Json
    if ($tmp -is [System.Array]) { $argsList = $tmp }
    elseif ($null -ne $tmp -and "$tmp" -ne '') { $argsList = @([string]$tmp) }
  } catch {
    Write-Log "Falha ao parsear args JSON: $($_.Exception.Message)"
  }
}

$ok = $false
for ($i=1; $i -le 10; $i++) {
  try {
    Copy-Item -LiteralPath $New -Destination $Old -Force
    $ok = $true
    Write-Log "Copy OK (tentativa $i)"
    break
  } catch {
    Write-Log "Copy falhou (tentativa $i): $($_.Exception.Message)"
    Start-Sleep -Seconds 1
  }
}

if ($ok) {
  try {
    Start-Process -FilePath $Old -ArgumentList $argsList | Out-Null
    Write-Log "Iniciado: $Old"
  } catch {
    Write-Log "Falha ao iniciar OLD: $($_.Exception.Message)"
  }
} else {
  # fallback: instala por usuario em LocalAppData
  try {
    $fallbackDir = Join-Path $env:LOCALAPPDATA $AppDir
    New-Item -ItemType Directory -Force -Path $fallbackDir | Out-Null
    $fallbackExe = Join-Path $fallbackDir $ExeName

    $ok2 = $false
    for ($i=1; $i -le 10; $i++) {
      try {
        Copy-Item -LiteralPath $New -Destination $fallbackExe -Force
        $ok2 = $true
        Write-Log "Fallback copy OK (tentativa $i) => $fallbackExe"
        break
      } catch {
        Write-Log "Fallback copy falhou (tentativa $i): $($_.Exception.Message)"
        Start-Sleep -Seconds 1
      }
    }

    if ($ok2) {
      Start-Process -FilePath $fallbackExe -ArgumentList $argsList | Out-Null
      Write-Log "Iniciado fallback: $fallbackExe"
    } else {
      Write-Log "Fallback falhou. Abrindo log."
      Start-Process -FilePath notepad.exe -ArgumentList $Log | Out-Null
    }
  } catch {
    Write-Log "Falha fatal no fallback: $($_.Exception.Message)"
    try { Start-Process -FilePath notepad.exe -ArgumentList $Log | Out-Null } catch { }
  }
}

# limpa
try { Remove-Item -LiteralPath $New -Force -ErrorAction SilentlyContinue } catch { }
"""


def launch_replace_and_restart(
    old_exe: Path,
    new_exe: Path,
    pid: int,
    *,
    app_dirname: str = "MuScanner",
    exe_name: str = "ScannerGUI.exe",
    extra_args: Optional[List[str]] = None,
) -> None:
    """Dispara um processo externo para substituir o exe e reiniciar.

    - Suporta caminhos com Unicode/acentos (PowerShell)
    - Tenta atualizar in-place; se falhar, copia para %LOCALAPPDATA%\<app_dirname>\<exe_name>

    Args:
        old_exe: executavel atual (destino)
        new_exe: executavel baixado (origem)
        pid: PID do processo atual para aguardar encerrar
        app_dirname: pasta de fallback em LocalAppData
        exe_name: nome do exe no fallback
        extra_args: args para repassar ao reiniciar
    """
    if platform.system() != "Windows":
        raise RuntimeError("Auto-update esta disponivel apenas no Windows.")

    old_exe = Path(old_exe).resolve()
    new_exe = Path(new_exe).resolve()

    if not old_exe.exists():
        raise RuntimeError(f"Exe atual nao encontrado: {old_exe}")
    if not new_exe.exists():
        raise RuntimeError(f"Novo exe nao encontrado: {new_exe}")

    tmpdir = Path(tempfile.gettempdir()) / "MuScannerUpdate"
    tmpdir.mkdir(parents=True, exist_ok=True)
    log = tmpdir / "update_log.txt"

    ps1 = tmpdir / f"muscanner_update_{pid}.ps1"
    # escreve com BOM para compatibilidade com PowerShell 5.1
    ps1.write_bytes(b"\xef\xbb\xbf" + _PS_SCRIPT.encode("utf-8"))

    env = dict(os.environ)
    env.update(
        {
            "MUSCANNER_PID": str(pid),
            "MUSCANNER_OLD": str(old_exe),
            "MUSCANNER_NEW": str(new_exe),
            "MUSCANNER_LOG": str(log),
            "MUSCANNER_APPDIR": app_dirname,
            "MUSCANNER_EXENAME": exe_name,
            "MUSCANNER_ARGS_JSON": json.dumps(extra_args or []),
        }
    )

    creationflags = 0
    try:
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    except Exception:
        creationflags = 0

    # Dispara PowerShell em background
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ps1),
        ],
        env=env,
        close_fds=True,
        creationflags=creationflags,
    )
