# -*- coding: utf-8 -*-
"""Auto-update simples via GitHub Releases (Windows).

Como funciona:
- Busca o release mais recente em: https://api.github.com/repos/<owner>/<repo>/releases/latest
- Escolhe um asset .exe (ou pelo nome preferido)
- Baixa o exe para %TEMP%
- Cria um .bat temporario que espera o PID finalizar e substitui o exe antigo pelo novo

Observacoes importantes:
- Isso funciona melhor com repo PUBLICO. Para repo privado voce precisaria de token (nao e seguro embutir).
- O executavel em execucao fica bloqueado no Windows, por isso usamos um .bat externo para trocar.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

UA = "MuScanner-Updater/1.0"


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
    """Extrai (major, minor, patch, extra) de algo como 'v1.2.3' ou '1.2.3-beta'.

    Regra:
      - comparamos major/minor/patch como inteiros
      - se empatar, consideramos 'extra' (pre-release) como menor que vazio
    """
    t = (tag or "").strip()
    t = t.lstrip("vV").strip()

    # pega apenas inicio numerico, mas guarda sufixo (beta/rc)
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

    # compara major/minor/patch
    if la[:3] != cu[:3]:
        return la[:3] > cu[:3]

    # se empatar, release final (extra vazio) e considerado mais novo que pre-release
    l_extra, c_extra = la[3], cu[3]
    if not l_extra and c_extra:
        return True
    if l_extra and not c_extra:
        return False

    # fallback: compara string do extra
    return l_extra > c_extra


def pick_asset(release: ReleaseInfo, *, preferred_name: str = "") -> Optional[AssetInfo]:
    assets = release.assets or []
    if not assets:
        return None

    if preferred_name:
        for a in assets:
            if a.name.lower() == preferred_name.lower():
                return a

    # fallback: primeiro .exe
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

    # nome unico para evitar conflito
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


def launch_replace_and_restart(old_exe: Path, new_exe: Path, pid: int) -> None:
    """Troca o EXE e reinicia.

    **Importante (Windows PT/Unicode):**
    Um .bat pode falhar ao lidar com caminhos contendo acentos (ex.: "Área de Trabalho"),
    pois o `cmd.exe` interpreta o arquivo com a codepage atual. Para evitar isso,
    usamos PowerShell, que lida melhor com Unicode e caminhos complexos.
    """
    old_exe = Path(old_exe).resolve()
    new_exe = Path(new_exe).resolve()

    if not old_exe.exists():
        raise RuntimeError(f"Exe atual nao encontrado: {old_exe}")
    if not new_exe.exists():
        raise RuntimeError(f"Novo exe nao encontrado: {new_exe}")

    tmp = Path(tempfile.gettempdir())
    ps1 = tmp / f"muscanner_update_{pid}.ps1"
    log = tmp / f"muscanner_update_{pid}.log"

    # PowerShell: mais confiavel com Unicode + tem Wait-Process
    ps_script = f"""
$ErrorActionPreference = 'SilentlyContinue'

$PidToWait = {pid}
$OldPath = @'{str(old_exe)}'@
$NewPath = @'{str(new_exe)}'@
$LogPath = @'{str(log)}'@

function Log([string]$m) {{
  $ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
  "$ts  $m" | Out-File -FilePath $LogPath -Append -Encoding UTF8
}}

Log "Updater iniciado. PID=$PidToWait"
Log "OLD=$OldPath"
Log "NEW=$NewPath"

try {{
  Wait-Process -Id $PidToWait -Timeout 120 | Out-Null
}} catch {{
  # Se nao existir, segue
}}

# Tenta copiar algumas vezes (antivirus/lock momentaneo)
$ok = $false
for ($i=0; $i -lt 60; $i++) {{
  try {{
    Copy-Item -LiteralPath $NewPath -Destination $OldPath -Force
    $ok = $true
    break
  }} catch {{
    Start-Sleep -Milliseconds 500
  }}
}}

if ($ok) {{
  Log "Copiado com sucesso."
}} else {{
  Log "Falha ao copiar apos varias tentativas."
}}

try {{
  $wd = Split-Path -Parent $OldPath
  Start-Process -FilePath $OldPath -WorkingDirectory $wd
  Log "Reinicio acionado."
}} catch {{
  Log "Falha ao reiniciar."
}}

try {{ Remove-Item -LiteralPath $NewPath -Force }} catch {{}}
try {{ Remove-Item -LiteralPath $PSCommandPath -Force }} catch {{}}
""".strip()

    # UTF-8 e ok pro PowerShell
    ps1.write_text(ps_script, encoding="utf-8")

    creationflags = 0
    try:
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    except Exception:
        creationflags = 0

    # -ExecutionPolicy Bypass evita bloqueio em maquinas com policy restrita
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ps1),
        ],
        close_fds=True,
        creationflags=creationflags,
    )
