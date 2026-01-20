#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
import unicodedata
import html
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterator, Optional, Sequence, Tuple, List


# ==========================================================
# UI (Rich opcional)
# ==========================================================

RICH_AVAILABLE = False
console = None

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import box
    from rich.prompt import Prompt, Confirm
    from rich.progress import (
        Progress,
        SpinnerColumn,
        BarColumn,
        TextColumn,
        TimeRemainingColumn,
        MofNCompleteColumn,
    )

    console = Console()
    RICH_AVAILABLE = True
except Exception:
    RICH_AVAILABLE = False
    console = None


def ui_print(msg: str = "") -> None:
    if RICH_AVAILABLE:
        console.print(msg)
    else:
        print(msg)


def ui_rule(title: str) -> None:
    if RICH_AVAILABLE:
        console.rule(title)
    else:
        print("=" * 72)
        print(title)
        print("=" * 72)


def ui_panel(title: str, body: str) -> None:
    if RICH_AVAILABLE:
        console.print(Panel(body, title=title, border_style="cyan"))
    else:
        print(f"[{title}]")
        print(body)


def ask(prompt: str, default: Optional[str] = None) -> str:
    if RICH_AVAILABLE:
        return Prompt.ask(prompt, default=default) if default is not None else Prompt.ask(prompt)
    else:
        if default is not None:
            prompt = f"{prompt} [{default}]: "
        else:
            prompt = f"{prompt}: "
        val = input(prompt).strip()
        return val if val else (default if default is not None else "")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    if RICH_AVAILABLE:
        return Confirm.ask(prompt, default=default)
    else:
        d = "S/n" if default else "s/N"
        while True:
            ans = ask(f"{prompt} ({d})", default="s" if default else "n").lower()
            if ans in ("s", "sim", "y", "yes"):
                return True
            if ans in ("n", "nao", "não", "no"):
                return False
            print("Resposta inválida. Digite 's' ou 'n'.")


def ask_choice(prompt: str, choices: Sequence[Tuple[str, str]], default_key: str) -> str:
    lines = [prompt]
    for k, desc in choices:
        lines.append(f"  {k}) {desc}")
    ui_print("\n" + "\n".join(lines))
    keys = {k.lower() for k, _ in choices}
    while True:
        ans = ask("Escolha", default=default_key).strip().lower()
        if ans in keys:
            return ans
        ui_print(
            f"[red]Opção inválida.[/red] Escolha uma de: {', '.join(sorted(keys))}"
            if RICH_AVAILABLE
            else f"Opção inválida. Escolha uma de: {', '.join(sorted(keys))}"
        )


def make_progress() -> "Progress":
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


# ==========================================================
# Parse multi-termos: {a, b} / a,b / "a, com vírgula", b
# ==========================================================

def parse_queries(raw: str) -> List[str]:
    s = raw.strip()
    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1].strip()
    # usa csv.reader para respeitar aspas
    parts = next(csv.reader([s], skipinitialspace=True))
    queries = [p.strip() for p in parts if p.strip()]
    # fallback: se não separou nada, devolve o original
    return queries if queries else [raw.strip()]


def format_queries(queries: List[str]) -> str:
    if len(queries) == 1:
        return queries[0]
    return " + ".join(queries)


# ==========================================================
# Normalização / Matching
# ==========================================================

def strip_accents(s: str) -> str:
    nf = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in nf if not unicodedata.combining(ch))


def prep(s: str, case_sensitive: bool, ignore_accents: bool) -> str:
    out = s
    if ignore_accents:
        out = strip_accents(out)
    if not case_sensitive:
        out = out.casefold()
    return out


class MatchMode:
    CONTAINS = "c"
    REGEX = "r"
    FUZZY = "f"


@dataclass(frozen=True)
class MatchConfig:
    query: str
    mode: str = MatchMode.CONTAINS
    case_sensitive: bool = False
    ignore_accents: bool = True
    fuzzy_threshold: float = 0.78
    regex_flags: int = 0


def build_single_matcher(cfg: MatchConfig):
    """Matcher para 1 termo: (text: str) -> bool"""
    if cfg.mode == MatchMode.REGEX:
        flags = cfg.regex_flags
        if not cfg.case_sensitive:
            flags |= re.IGNORECASE
        pattern = re.compile(cfg.query, flags=flags)

        def _m(text: str) -> bool:
            return pattern.search(text) is not None

        return _m

    if cfg.mode == MatchMode.FUZZY:
        qn = prep(cfg.query, cfg.case_sensitive, cfg.ignore_accents)

        def _ratio(a: str, b: str) -> float:
            return SequenceMatcher(None, a, b).ratio()

        def _m(text: str) -> bool:
            tn = prep(text, cfg.case_sensitive, cfg.ignore_accents)
            if qn in tn:
                return True
            return _ratio(qn, tn) >= cfg.fuzzy_threshold

        return _m

    # CONTAINS
    qn = prep(cfg.query, cfg.case_sensitive, cfg.ignore_accents)

    def _m(text: str) -> bool:
        tn = prep(text, cfg.case_sensitive, cfg.ignore_accents)
        return qn in tn

    return _m


def build_multi_matcher(queries: List[str], base_cfg: MatchConfig):
    """
    Matcher AND: retorna True somente se TODOS os termos baterem no mesmo texto.
    Ex: linha precisa conter experience E 300.
    """
    matchers = [build_single_matcher(MatchConfig(**{**base_cfg.__dict__, "query": q})) for q in queries]

    def _m(text: str) -> bool:
        return all(m(text) for m in matchers)

    return _m


# ==========================================================
# Walk / candidatos / contagem (progresso real)
# ==========================================================

DEFAULT_IGNORE_DIRS = {
    ".git", ".svn", ".hg",
    "node_modules",
    "__pycache__",
    ".venv", "venv", "env",
    ".idea", ".vscode",
}


def iter_dirs_files(base: Path, recursive: bool, ignore_dirs: set[str]) -> Iterator[Tuple[Path, bool]]:
    if base.is_file():
        yield (base, False)
        return

    if not base.is_dir():
        return

    if not recursive:
        for p in base.iterdir():
            yield (p, p.is_dir())
        return

    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        rootp = Path(root)
        for d in dirs:
            yield (rootp / d, True)
        for f in files:
            yield (rootp / f, False)


def iter_name_candidates(base: Path, recursive: bool, ignore_dirs: set[str], want_dirs: bool, want_files: bool) -> Iterator[Tuple[Path, str]]:
    for p, is_dir in iter_dirs_files(base, recursive=recursive, ignore_dirs=ignore_dirs):
        if is_dir and want_dirs:
            yield (p, "PASTA")
        elif (not is_dir) and want_files:
            yield (p, "ARQUIVO")


def iter_content_candidates(base: Path, recursive: bool, ignore_dirs: set[str], exts: Optional[set[str]]) -> Iterator[Path]:
    for p, is_dir in iter_dirs_files(base, recursive=recursive, ignore_dirs=ignore_dirs):
        if is_dir:
            continue
        if exts is not None and p.suffix.lower() not in exts:
            continue
        yield p


def count_iter(it: Iterator) -> int:
    c = 0
    for _ in it:
        c += 1
    return c


# ==========================================================
# Scan por nomes (pastas/arquivos)
# ==========================================================

@dataclass
class NameHit:
    path: Path
    kind: str  # "PASTA" ou "ARQUIVO"


def scan_names_with_progress(
    base: Path,
    matcher,
    recursive: bool,
    ignore_dirs: set[str],
    want_dirs: bool,
    want_files: bool,
) -> list[NameHit]:
    ui_print("\nContando itens para progresso real..." if not RICH_AVAILABLE else "\n[cyan]Contando itens para progresso real...[/cyan]")
    total = count_iter(iter_name_candidates(base, recursive, ignore_dirs, want_dirs, want_files))

    hits: list[NameHit] = []

    if RICH_AVAILABLE:
        with make_progress() as progress:
            task = progress.add_task("Escaneando nomes", total=total)
            for p, kind in iter_name_candidates(base, recursive, ignore_dirs, want_dirs, want_files):
                if matcher(p.name):
                    hits.append(NameHit(path=p, kind=kind))
                progress.advance(task)
    else:
        scanned = 0
        step = 500 if total >= 5000 else 200
        for p, kind in iter_name_candidates(base, recursive, ignore_dirs, want_dirs, want_files):
            if matcher(p.name):
                hits.append(NameHit(path=p, kind=kind))
            scanned += 1
            if scanned % step == 0 or scanned == total:
                pct = (scanned / total * 100) if total else 100
                print(f"\rEscaneando... {scanned}/{total} ({pct:.1f}%)", end="")
        print()

    return hits


# ==========================================================
# Scan por conteúdo dentro de arquivos
# ==========================================================

def looks_binary(sample: bytes) -> bool:
    if b"\x00" in sample:
        return True
    nontext = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return (len(sample) > 0) and (nontext / len(sample) > 0.12)


ENC_CANDIDATES = ("utf-8", "utf-8-sig", "cp1252", "latin-1")


def detect_encoding_by_sample(sample: bytes, candidates: Sequence[str]) -> Optional[str]:
    for enc in candidates:
        try:
            sample.decode(enc, errors="strict")
            return enc
        except Exception:
            continue
    return None


@dataclass
class ContentHit:
    path: Path
    matches_count: int
    examples: list[Tuple[int, str]]


def scan_file_content(
    file_path: Path,
    matcher,  # agora é multi (AND) também
    max_examples: Optional[int],  # None => todos
    max_file_size_mb: Optional[int] = 50,
) -> Optional[ContentHit]:
    try:
        st = file_path.stat()
    except Exception:
        return None

    if max_file_size_mb is not None and st.st_size > max_file_size_mb * 1024 * 1024:
        return None

    try:
        with file_path.open("rb") as bf:
            sample = bf.read(8192)
            if looks_binary(sample):
                return None
            enc = detect_encoding_by_sample(sample, ENC_CANDIDATES) or "utf-8"
    except Exception:
        return None

    matches = 0
    examples: list[Tuple[int, str]] = []

    # Evita explosão de memória em caso extremo
    HARD_CAP_PER_FILE = 50000

    try:
        with file_path.open("r", encoding=enc, errors="replace", newline="") as tf:
            for i, line in enumerate(tf, start=1):
                # AQUI está o “juntas”: a linha precisa bater em TODOS os termos (AND)
                if matcher(line):
                    matches += 1
                    if max_examples == 0:
                        continue

                    if max_examples is None:
                        if len(examples) >= HARD_CAP_PER_FILE:
                            continue
                        snippet = line.rstrip("\n\r")
                        if len(snippet) > 240:
                            snippet = snippet[:240] + "…"
                        examples.append((i, snippet))
                    else:
                        if len(examples) < max_examples:
                            snippet = line.rstrip("\n\r")
                            if len(snippet) > 240:
                                snippet = snippet[:240] + "…"
                            examples.append((i, snippet))
    except Exception:
        return None

    if matches > 0:
        return ContentHit(path=file_path, matches_count=matches, examples=examples)
    return None


def scan_contents_with_progress(
    base: Path,
    matcher,
    recursive: bool,
    ignore_dirs: set[str],
    exts: Optional[set[str]],
    max_examples: Optional[int],
    max_file_size_mb: Optional[int],
) -> list[ContentHit]:
    ui_print("\nContando arquivos para progresso real..." if not RICH_AVAILABLE else "\n[cyan]Contando arquivos para progresso real...[/cyan]")
    total = count_iter(iter_content_candidates(base, recursive, ignore_dirs, exts))

    hits: list[ContentHit] = []

    if RICH_AVAILABLE:
        with make_progress() as progress:
            task = progress.add_task("Escaneando conteúdo", total=total)
            for p in iter_content_candidates(base, recursive, ignore_dirs, exts):
                hit = scan_file_content(p, matcher, max_examples, max_file_size_mb)
                if hit is not None:
                    hits.append(hit)
                progress.advance(task)
    else:
        scanned = 0
        step = 200 if total < 5000 else 500
        for p in iter_content_candidates(base, recursive, ignore_dirs, exts):
            hit = scan_file_content(p, matcher, max_examples, max_file_size_mb)
            if hit is not None:
                hits.append(hit)
            scanned += 1
            if scanned % step == 0 or scanned == total:
                pct = (scanned / total * 100) if total else 100
                print(f"\rEscaneando... {scanned}/{total} ({pct:.1f}%)", end="")
        print()

    return hits


# ==========================================================
# Export / HTML
# ==========================================================

def export_txt(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_csv_names(path: Path, hits: list[NameHit]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["tipo", "caminho"])
        for h in hits:
            w.writerow([h.kind, str(h.path)])


def export_csv_contents(path: Path, hits: list[ContentHit]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["caminho", "linhas_com_todos_os_termos", "exemplos"])
        for h in hits:
            ex = "; ".join([f"L{ln}: {txt}" for ln, txt in h.examples])
            w.writerow([str(h.path), h.matches_count, ex])


def path_to_file_uri(p: Path) -> str:
    try:
        return p.resolve().as_uri()
    except Exception:
        s = str(p.resolve()).replace("\\", "/")
        if not s.startswith("/"):
            return "file:///" + s
        return "file://" + s


def _html_template(
    *,
    title: str,
    base: Path,
    query: str,
    mode_label: str,
    options: dict,
    total_found: int,
    elapsed_s: float,
    table_head: str,
    table_body: str,
    extra_section: str,
) -> str:
    opts_li = "".join(
        f"<li><b>{html.escape(str(k))}:</b> {html.escape(str(v))}</li>"
        for k, v in options.items()
    )
    gen_time = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg:#0b1020; --card:#121a33; --text:#e8ecff; --muted:#a8b0d6;
      --accent:#5eead4; --border:rgba(255,255,255,.10);
    }}
    body {{
      margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;
      background: radial-gradient(1200px 600px at 20% 0%, #18224a 0%, var(--bg) 55%);
      color:var(--text);
    }}
    .wrap {{ max-width:1100px; margin:0 auto; padding:28px 18px 40px; }}
    h1 {{ margin:0; font-size:26px; }}
    .meta {{ color:var(--muted); font-size:14px; line-height:1.45; }}
    .card {{
      background: linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,.03));
      border:1px solid var(--border); border-radius:14px; padding:16px;
      box-shadow:0 10px 30px rgba(0,0,0,.25);
    }}
    .grid {{ display:grid; grid-template-columns:1.2fr .8fr; gap:12px; }}
    @media (max-width:900px) {{ .grid {{ grid-template-columns:1fr; }} }}
    .pill {{
      display:inline-block; padding:4px 10px; border-radius:999px;
      background:rgba(94,234,212,.12); border:1px solid rgba(94,234,212,.35);
      color:var(--accent); font-size:12px; font-weight:700; letter-spacing:.3px;
    }}
    .filter {{ display:flex; gap:10px; align-items:center; margin-top:10px; }}
    input[type="text"] {{
      width:100%; padding:10px 12px; border-radius:10px; border:1px solid var(--border);
      background:rgba(0,0,0,.20); color:var(--text); outline:none;
    }}
    table {{
      width:100%; border-collapse:collapse; margin-top:10px;
      border-radius:12px; overflow:hidden; border:1px solid var(--border);
    }}
    thead th {{
      text-align:left; font-size:12px; letter-spacing:.35px; text-transform:uppercase;
      color:var(--muted); padding:12px; background:rgba(0,0,0,.18);
      border-bottom:1px solid var(--border);
    }}
    tbody td {{ padding:12px; border-bottom:1px solid var(--border); vertical-align:top; }}
    tbody tr:hover {{ background:rgba(255,255,255,.04); }}
    a {{ color:var(--accent); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    .muted {{ color:var(--muted); }}
    .kind {{ width:110px; font-weight:800; }}
    .count {{ width:130px; text-align:right; font-variant-numeric:tabular-nums; font-weight:800; }}
    .sub {{ margin-top:8px; color:var(--muted); font-size:13px; }}
    details summary {{ cursor:pointer; color:var(--accent); font-weight:700; list-style:none; }}
    details summary::-webkit-details-marker {{ display:none; }}
    ul.examples {{ margin:10px 0 0; padding-left:18px; }}
    ul.examples li {{ margin:6px 0; line-height:1.35; }}
    code {{
      background:rgba(0,0,0,.25); border:1px solid rgba(255,255,255,.10);
      padding:2px 6px; border-radius:8px; color:var(--text);
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="grid">
        <div>
          <div class="pill">{html.escape(mode_label)}</div>
          <h1>{html.escape(title)}</h1>
          <div class="meta">
            Gerado em <b>{html.escape(gen_time)}</b><br/>
            Base: <code>{html.escape(str(base))}</code><br/>
            Consulta: <code>{html.escape(query)}</code><br/>
            Encontrados: <b>{total_found}</b> • Tempo: <b>{elapsed_s:.2f}s</b>
          </div>
          <div class="filter">
            <input id="filter" type="text" placeholder="Filtrar resultados (digite parte do caminho, tipo, etc.)" />
          </div>
          {extra_section}
        </div>
        <div>
          <div class="meta"><b>Opções usadas</b></div>
          <ul class="meta">
            {opts_li if opts_li else "<li class='muted'>Nenhuma opção adicional.</li>"}
          </ul>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:12px;">
      <table id="results">
        <thead>{table_head}</thead>
        <tbody>{table_body}</tbody>
      </table>
    </div>
  </div>

<script>
  const input = document.getElementById('filter');
  const table = document.getElementById('results');
  const rows = Array.from(table.querySelectorAll('tbody tr'));
  function norm(s) {{ return (s || '').toLowerCase(); }}
  input.addEventListener('input', () => {{
    const q = norm(input.value.trim());
    rows.forEach(r => {{
      const text = norm(r.innerText);
      r.style.display = text.includes(q) ? '' : 'none';
    }});
  }});
</script>
</body>
</html>"""


def make_html_report_names(
    *,
    title: str,
    base: Path,
    query: str,
    mode_label: str,
    options: dict,
    hits: list[NameHit],
    elapsed_s: float,
) -> str:
    rows = []
    for h in sorted(hits, key=lambda x: str(x.path).lower()):
        uri = path_to_file_uri(h.path)
        rows.append(
            f"<tr><td class='kind'>{html.escape(h.kind)}</td>"
            f"<td class='path'><a href='{html.escape(uri)}'>{html.escape(str(h.path))}</a></td></tr>"
        )

    return _html_template(
        title=title,
        base=base,
        query=query,
        mode_label=mode_label,
        options=options,
        total_found=len(hits),
        elapsed_s=elapsed_s,
        table_head="<tr><th>Tipo</th><th>Caminho (clicável)</th></tr>",
        table_body="\n".join(rows) if rows else "<tr><td colspan='2'>Nenhum resultado.</td></tr>",
        extra_section="",
    )


def make_html_report_contents(
    *,
    title: str,
    base: Path,
    query: str,
    mode_label: str,
    options: dict,
    hits: list[ContentHit],
    elapsed_s: float,
) -> str:
    hits_sorted = sorted(hits, key=lambda x: (-x.matches_count, str(x.path).lower()))

    rows = []
    for h in hits_sorted:
        uri = path_to_file_uri(h.path)
        if h.examples:
            items = [f"<li><code>L{ln}</code> — {html.escape(txt)}</li>" for ln, txt in h.examples]
            examples_html = (
                "<details class='details'>"
                f"<summary>Ver trechos ({len(h.examples)})</summary>"
                f"<ul class='examples'>{''.join(items)}</ul>"
                "</details>"
            )
        else:
            examples_html = "<span class='muted'>Sem trechos coletados.</span>"

        rows.append(
            "<tr>"
            f"<td class='count'>{h.matches_count}</td>"
            f"<td class='path'><a href='{html.escape(uri)}'>{html.escape(str(h.path))}</a>"
            f"<div class='sub'>{examples_html}</div></td>"
            "</tr>"
        )

    return _html_template(
        title=title,
        base=base,
        query=query,
        mode_label=mode_label,
        options=options,
        total_found=len(hits_sorted),
        elapsed_s=elapsed_s,
        table_head="<tr><th>Linhas com TODOS os termos</th><th>Arquivo (clicável)</th></tr>",
        table_body="\n".join(rows) if rows else "<tr><td colspan='2'>Nenhum resultado.</td></tr>",
        extra_section="<p class='muted'>Este relatório considera match quando uma mesma linha contém todos os termos (AND).</p>",
    )


# ==========================================================
# Impressão
# ==========================================================

def print_name_hits(hits: list[NameHit]) -> None:
    if not hits:
        ui_print("\nNenhum resultado encontrado.")
        return

    if RICH_AVAILABLE:
        table = Table(title=f"Resultados ({len(hits)})", box=box.SIMPLE)
        table.add_column("Tipo", style="cyan", no_wrap=True)
        table.add_column("Caminho", style="white")
        for h in sorted(hits, key=lambda x: str(x.path).lower()):
            table.add_row(h.kind, str(h.path))
        console.print(table)
    else:
        ui_print(f"\nEncontrados: {len(hits)}")
        for h in hits:
            ui_print(f"[{h.kind}] {h.path}")


def print_content_hits(hits: list[ContentHit], show_examples: bool) -> None:
    if not hits:
        ui_print("\nNenhum arquivo com o valor encontrado.")
        return

    hits_sorted = sorted(hits, key=lambda x: (-x.matches_count, str(x.path).lower()))

    if RICH_AVAILABLE and not show_examples:
        table = Table(title=f"Arquivos com match ({len(hits_sorted)})", box=box.SIMPLE)
        table.add_column("Linhas (AND)", justify="right", style="green", no_wrap=True)
        table.add_column("Arquivo", style="white")
        for h in hits_sorted:
            table.add_row(str(h.matches_count), str(h.path))
        console.print(table)
        return

    ui_print(f"\nArquivos com match: {len(hits_sorted)}")
    for h in hits_sorted:
        if show_examples:
            ui_print(
                f"\n[bold cyan][ARQUIVO][/bold cyan] {h.path}  [green](linhas AND: {h.matches_count})[/green]"
                if RICH_AVAILABLE
                else f"\n[ARQUIVO] {h.path} (linhas AND: {h.matches_count})"
            )
            for ln, txt in h.examples:
                ui_print(f"   - Linha {ln}: {txt}")
        else:
            ui_print(f"[ARQUIVO] {h.path}")


# ==========================================================
# Fluxo principal (interativo)
# ==========================================================

def interactive_main() -> int:
    ui_rule("SCANNER — AND (multi-termos juntos) + Progresso real + HTML")

    ui_panel(
        "Como usar multi-termos (mais certeiro)",
        "Você pode pesquisar múltiplos termos que devem aparecer JUNTOS:\n"
        "  - Ex.: {experience, 300}\n"
        "  - Ex.: experience, 300\n"
        "  - Com vírgula no termo: \"experience, senior\", 300\n\n"
        "No modo CONTEÚDO, uma linha só é match se contiver TODOS os termos (AND)."
    )

    raw = ask("Digite o path (arquivo ou pasta)")
    base = Path(raw).expanduser()

    if not base.exists():
        ui_print("\nERRO: Esse path não existe.")
        return 2

    mode = ask_choice(
        "O que você deseja escanear?",
        choices=[
            ("1", "Pastas (nomes de diretórios)"),
            ("2", "Arquivos (nomes de arquivos)"),
            ("3", "Conteúdo dentro dos arquivos (texto)"),
        ],
        default_key="2",
    )

    query_raw = ask("Qual string/termos você quer localizar? (ex.: {Experiência, 300})")
    if not query_raw.strip():
        ui_print("\nERRO: Você precisa informar uma string.")
        return 2

    queries = parse_queries(query_raw)

    match_mode = ask_choice(
        "Modo de busca:",
        choices=[
            (MatchMode.CONTAINS, "Contém (substring) — recomendado"),
            (MatchMode.FUZZY, "Similar (fuzzy) — encontra parecidos"),
            (MatchMode.REGEX, "Regex — avançado (para multi-termos, cada regex precisa bater na linha)"),
        ],
        default_key=MatchMode.CONTAINS,
    )

    case_sensitive = ask_yes_no("Diferenciar maiúsculas/minúsculas?", default=False)

    ignore_accents = True
    if match_mode != MatchMode.REGEX:
        ignore_accents = ask_yes_no("Ignorar acentos? (Experiencia ≈ Experiência)", default=True)

    recursive = True
    if base.is_dir():
        recursive = ask_yes_no("Escanear recursivamente (subpastas)?", default=True)

    ignore_dirs = set(DEFAULT_IGNORE_DIRS)
    if base.is_dir():
        extra_ign = ask("Pastas para ignorar (separadas por vírgula) ou vazio", default="")
        if extra_ign.strip():
            for it in extra_ign.split(","):
                t = it.strip()
                if t:
                    ignore_dirs.add(t)

    fuzzy_threshold = 0.78
    if match_mode == MatchMode.FUZZY:
        t = ask("Nível de similaridade (0.50 a 0.95)", default="0.78")
        try:
            fuzzy_threshold = float(t)
            fuzzy_threshold = max(0.50, min(0.95, fuzzy_threshold))
        except ValueError:
            fuzzy_threshold = 0.78

    regex_flags = 0
    if match_mode == MatchMode.REGEX:
        if ask_yes_no("Regex com DOTALL? ('.' pega quebras de linha)", default=False):
            regex_flags |= re.DOTALL

    base_cfg = MatchConfig(
        query="",  # será substituído termo a termo
        mode=match_mode,
        case_sensitive=case_sensitive,
        ignore_accents=ignore_accents,
        fuzzy_threshold=fuzzy_threshold,
        regex_flags=regex_flags,
    )

    matcher = build_multi_matcher(queries, base_cfg)
    query_display = format_queries(queries)

    start = time.time()

    # ======================================================
    # 1) Pastas / Arquivos por nome (AND no nome)
    # ======================================================
    if mode in ("1", "2"):
        want_dirs = (mode == "1")
        want_files = (mode == "2")

        hits = scan_names_with_progress(
            base=base,
            matcher=matcher,  # AND no nome
            recursive=recursive,
            ignore_dirs=ignore_dirs,
            want_dirs=want_dirs,
            want_files=want_files,
        )

        elapsed = time.time() - start
        print_name_hits(hits)
        ui_print(f"\nTempo: {elapsed:.2f}s")

        if hits and ask_yes_no("Deseja exportar os resultados?", default=False):
            out_kind = ask_choice(
                "Formato:",
                choices=[("1", "TXT"), ("2", "CSV"), ("3", "HTML (clicável)")],
                default_key="3",
            )
            default_name = "relatorio.html" if out_kind == "3" else "resultados.csv"
            out_path = Path(ask("Salvar como (caminho do arquivo)", default=str(Path.cwd() / default_name))).expanduser()

            try:
                if out_kind == "1":
                    lines = [f"{h.kind}\t{h.path}" for h in hits]
                    export_txt(out_path, lines)
                elif out_kind == "2":
                    export_csv_names(out_path, hits)
                else:
                    report = make_html_report_names(
                        title="Relatório do Scanner (AND)",
                        base=base,
                        query=query_display,
                        mode_label="Busca por nomes (AND)",
                        options={
                            "Recursivo": recursive,
                            "Case-sensitive": case_sensitive,
                            "Ignorar acentos": ignore_accents if match_mode != MatchMode.REGEX else "N/A (regex)",
                            "Modo de match": {"c": "contains", "f": "fuzzy", "r": "regex"}[match_mode],
                            "Termos (AND)": query_display,
                            "Pastas ignoradas": ", ".join(sorted(ignore_dirs)) if ignore_dirs else "(nenhuma)",
                        },
                        hits=hits,
                        elapsed_s=elapsed,
                    )
                    out_path.write_text(report, encoding="utf-8")
                ui_print(f"Exportado para: {out_path}")
            except Exception as e:
                ui_print(f"Falha ao exportar: {e}")

        return 0

    # ======================================================
    # 2) Conteúdo dentro dos arquivos (AND na MESMA LINHA)
    # ======================================================

    filt = ask_choice(
        "Buscar conteúdo em quais extensões?",
        choices=[
            ("1", "Padrão (.dat)"),
            ("2", "Qualquer extensão (sem filtro)"),
            ("3", "Lista personalizada (ex.: .txt,.dat,.log)"),
        ],
        default_key="1",
    )

    exts: Optional[set[str]]
    if filt == "1":
        exts = {".dat"}
    elif filt == "2":
        exts = None
    else:
        raw_exts = ask("Digite as extensões (ex.: .txt,.dat) ou deixe vazio para voltar ao .dat", default="")
        if not raw_exts.strip():
            exts = {".dat"}
        else:
            parsed: set[str] = set()
            for part in raw_exts.split(","):
                t = part.strip().lower()
                if not t:
                    continue
                if not t.startswith("."):
                    t = "." + t
                parsed.add(t)
            exts = parsed if parsed else {".dat"}

    show_examples = ask_yes_no("Mostrar exemplos de linhas encontradas na tela?", default=True)

    # padrão: TODOS
    if show_examples:
        ui_print("\n[Obs] 'todos' pode gerar muita saída no terminal." if not RICH_AVAILABLE else "\n[bold yellow]Obs:[/bold yellow] 'todos' pode gerar muita saída no terminal.")
        m = ask("Quantos exemplos por arquivo? (todos / número)", default="todos").strip().lower()
        if m in ("todos", "todo", "all", "*"):
            max_examples = None
        else:
            try:
                n = int(m)
                max_examples = None if n <= 0 else min(200000, n)
            except ValueError:
                max_examples = None
    else:
        max_examples = 0

    lim = ask("Ignorar arquivos maiores que (MB) ou '0' para não limitar", default="50")
    try:
        n = int(lim)
        max_file_size_mb: Optional[int] = None if n == 0 else max(1, n)
    except ValueError:
        max_file_size_mb = 50

    hits = scan_contents_with_progress(
        base=base,
        matcher=matcher,  # AND na mesma linha
        recursive=recursive,
        ignore_dirs=ignore_dirs,
        exts=exts,
        max_examples=max_examples,
        max_file_size_mb=max_file_size_mb,
    )

    elapsed = time.time() - start
    print_content_hits(hits, show_examples=show_examples)
    ui_print(f"\nTempo: {elapsed:.2f}s")

    if hits and ask_yes_no("Deseja exportar os resultados?", default=False):
        out_kind = ask_choice(
            "Formato:",
            choices=[("1", "TXT"), ("2", "CSV"), ("3", "HTML (clicável)")],
            default_key="3",
        )
        default_name = "relatorio.html" if out_kind == "3" else "resultados.csv"
        out_path = Path(ask("Salvar como (caminho do arquivo)", default=str(Path.cwd() / default_name))).expanduser()

        try:
            if out_kind == "1":
                lines = []
                for h in sorted(hits, key=lambda x: (-x.matches_count, str(x.path).lower())):
                    lines.append(f"[ARQUIVO] {h.path} (linhas AND: {h.matches_count})")
                    for ln, txt in h.examples:
                        lines.append(f"  - Linha {ln}: {txt}")
                export_txt(out_path, lines)
            elif out_kind == "2":
                export_csv_contents(out_path, hits)
            else:
                report = make_html_report_contents(
                    title="Relatório do Scanner (AND)",
                    base=base,
                    query=query_display,
                    mode_label="Busca dentro de arquivos (AND na linha)",
                    options={
                        "Recursivo": recursive,
                        "Case-sensitive": case_sensitive,
                        "Ignorar acentos": ignore_accents if match_mode != MatchMode.REGEX else "N/A (regex)",
                        "Modo de match": {"c": "contains", "f": "fuzzy", "r": "regex"}[match_mode],
                        "Termos (AND)": query_display,
                        "Extensões": "qualquer" if exts is None else ", ".join(sorted(exts)),
                        "Limite de tamanho (MB)": "sem limite" if max_file_size_mb is None else max_file_size_mb,
                        "Trechos por arquivo": "todos" if max_examples is None else max_examples,
                        "Pastas ignoradas": ", ".join(sorted(ignore_dirs)) if ignore_dirs else "(nenhuma)",
                    },
                    hits=hits,
                    elapsed_s=elapsed,
                )
                out_path.write_text(report, encoding="utf-8")
            ui_print(f"Exportado para: {out_path}")
        except Exception as e:
            ui_print(f"Falha ao exportar: {e}")

    return 0


# ==========================================================
# CLI opcional (não-interativo)
# ==========================================================

def cli_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Scanner profissional AND (multi-termos juntos) + HTML.")
    parser.add_argument("--path", type=str, help="Path base (arquivo ou pasta).")
    parser.add_argument("--mode", choices=["folders", "files", "content"], help="O que escanear.")
    parser.add_argument("--query", type=str, help="String/termos. Ex: '{experience, 300}'")
    parser.add_argument("--match", choices=["contains", "regex", "fuzzy"], default="contains")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--case", action="store_true")
    parser.add_argument("--no-accents", action="store_true")
    parser.add_argument("--fuzzy-threshold", type=float, default=0.78)
    parser.add_argument("--exts", type=str, default=".dat", help="Content: extensões (ex.: .dat,.txt)")
    parser.add_argument("--all-exts", action="store_true", help="Content: qualquer extensão (sem filtro)")
    parser.add_argument("--max-mb", type=int, default=50, help="0 = sem limite")
    parser.add_argument("--examples", type=str, default="all", help="all/todos/* ou número; 0 = nenhum")
    args = parser.parse_args(argv)

    if not args.path or not args.mode or not args.query:
        return interactive_main()

    base = Path(args.path).expanduser()
    if not base.exists():
        ui_print("ERRO: path não existe.")
        return 2

    queries = parse_queries(args.query)

    mode_map = {"contains": MatchMode.CONTAINS, "regex": MatchMode.REGEX, "fuzzy": MatchMode.FUZZY}
    match_mode = mode_map[args.match]

    base_cfg = MatchConfig(
        query="",
        mode=match_mode,
        case_sensitive=args.case,
        ignore_accents=(not args.no_accents) if match_mode != MatchMode.REGEX else False,
        fuzzy_threshold=args.fuzzy_threshold,
        regex_flags=0,
    )
    matcher = build_multi_matcher(queries, base_cfg)

    ignore_dirs = set(DEFAULT_IGNORE_DIRS)
    recursive = args.recursive if base.is_dir() else False

    start = time.time()

    if args.mode in ("folders", "files"):
        hits = scan_names_with_progress(
            base=base,
            matcher=matcher,
            recursive=recursive,
            ignore_dirs=ignore_dirs,
            want_dirs=(args.mode == "folders"),
            want_files=(args.mode == "files"),
        )
        elapsed = time.time() - start
        print_name_hits(hits)
        ui_print(f"\nTempo: {elapsed:.2f}s")
        return 0

    exts: Optional[set[str]]
    if args.all_exts:
        exts = None
    else:
        parsed: set[str] = set()
        for part in (args.exts or ".dat").split(","):
            t = part.strip().lower()
            if not t:
                continue
            if not t.startswith("."):
                t = "." + t
            parsed.add(t)
        exts = parsed if parsed else {".dat"}

    max_mb = None if args.max_mb == 0 else max(1, args.max_mb)

    ex_arg = (args.examples or "all").strip().lower()
    if ex_arg in ("all", "*", "todos"):
        max_examples: Optional[int] = None
    else:
        try:
            n = int(ex_arg)
            max_examples = 0 if n == 0 else (None if n < 0 else n)
        except ValueError:
            max_examples = None

    hits = scan_contents_with_progress(
        base=base,
        matcher=matcher,
        recursive=recursive,
        ignore_dirs=ignore_dirs,
        exts=exts,
        max_examples=max_examples,
        max_file_size_mb=max_mb,
    )

    elapsed = time.time() - start
    print_content_hits(hits, show_examples=(max_examples != 0))
    ui_print(f"\nTempo: {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(cli_main(sys.argv[1:]))
    except KeyboardInterrupt:
        ui_print("\nInterrompido pelo usuário.")
        sys.exit(130)
