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
    """Cria e executa um .bat que espera o processo acabar, substitui e reinicia."""
    old_exe = Path(old_exe).resolve()
    new_exe = Path(new_exe).resolve()

    if not old_exe.exists():
        raise RuntimeError(f"Exe atual nao encontrado: {old_exe}")
    if not new_exe.exists():
        raise RuntimeError(f"Novo exe nao encontrado: {new_exe}")

    bat = Path(tempfile.gettempdir()) / f"muscanner_update_{pid}.bat"

    # Importante: usar CRLF ajuda no cmd do Windows; mas o cmd aceita LF tambem.
    script = f"""@echo off
setlocal
set "PID={pid}"
set "OLD={old_exe}"
set "NEW={new_exe}"

:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait
)

REM Tenta substituir
copy /Y "%NEW%" "%OLD%" >nul

REM Reinicia
start "" "%OLD%"

REM Limpa
del "%NEW%" >nul 2>nul
del "%~f0" >nul 2>nul
endlocal
"""

    bat.write_text(script, encoding="utf-8")

    # Executa o .bat destacado
    creationflags = 0
    try:
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    except Exception:
        creationflags = 0

    subprocess.Popen(
        ["cmd.exe", "/c", str(bat)],
        close_fds=True,
        creationflags=creationflags,
    )
