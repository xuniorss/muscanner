# -*- coding: utf-8 -*-
"""Scanner GUI (Desktop) - interface grafica para scanner3.py.

Recursos:
- Visual mais moderno (ttkbootstrap, tema claro "premium")
- Scan por nomes (pastas/arquivos) e por conteudo
- Exportar CSV/HTML (usa export_csv_* e make_html_report_* do scanner3.py)
- Limitar tamanho maximo do arquivo (MB) (0 = sem limite)
- Trechos por arquivo (0 = nenhum) ou Todos (padrao)
- Durante o scan: desabilita inputs e reabilita ao finalizar
- Barra de status com tempo decorrido e ETA (estimativa)

Requisitos:
  pip install ttkbootstrap

Observacao:
  Este arquivo deve ficar na mesma pasta do scanner3.py.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox

import ttkbootstrap as tb
from ttkbootstrap.constants import *

import scanner3 as s3
import updater_github as upd


APP_TITLE = "Scanner GUI"
# Atualize este numero quando publicar uma nova versao no GitHub Releases.
# (Layout premium claro)
APP_VERSION = "0.1.3"

# --- Auto-update (GitHub Releases) ---
# Recomenda-se repo PUBLICO (ou entao voce precisara de token e isso nao e seguro embutir no exe).
GITHUB_OWNER = "xuniorss"
GITHUB_REPO = "muscanner"
# Nome do asset (arquivo) que voce vai anexar no Release. Ex.: ScannerGUI.exe
GITHUB_ASSET_NAME = "ScannerGUI.exe"
# Tema "premium" (claro). Outras boas opcoes: "cosmo", "litera", "minty".
THEME = "flatly"


def _fmt_hhmmss(seconds: Optional[float]) -> str:
    if seconds is None or seconds == float("inf") or seconds < 0:
        return "--:--"
    s = int(round(seconds))
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{ss:02d}"
    return f"{m:02d}:{ss:02d}"


def open_path(path: Path) -> None:
    try:
        if platform.system() == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        messagebox.showwarning("Aviso", f"Nao foi possivel abrir: {path}")


def safe_set_state(widget: Any, state: str) -> None:
    """Tenta alterar state de widgets ttk; ignora se nao suportado."""
    try:
        widget.configure(state=state)
    except Exception:
        try:
            widget["state"] = state
        except Exception:
            pass


class ScannerGUI(tb.Window):
    def __init__(self):
        super().__init__(themename=THEME)
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry("1150x700")
        self.minsize(980, 620)

        # Fonte e ajustes de estilo (visual mais "premium")
        try:
            # Importante: nomes de fonte com espaco precisam de chaves no Tcl;
            # caso contrario o Tk tenta interpretar "UI" como tamanho e falha.
            self.option_add("*Font", "{Segoe UI} 10")
        except Exception:
            pass

        # Paleta clara "premium" (nao e dark, mas evita branco estourado)
        self._apply_premium_light_theme()
        try:
            self.style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
            self.style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        except Exception:
            pass

        # Estado
        self._scan_thread: Optional[threading.Thread] = None
        self._stop_flag = False
        self._is_scanning = False

        # Animacao (spinner simples)
        self._anim_job: Optional[str] = None
        self._anim_i: int = 0

        # Progresso / ETA
        self._scan_start_wall: Optional[float] = None
        self._scan_total: int = 0
        self._scan_done: int = 0
        self._scan_phase: str = ""

        self.last_mode: Optional[str] = None  # folders/files/content
        self.last_queries: List[str] = []
        self.last_base: Optional[Path] = None
        self.last_hits_names: List[s3.NameHit] = []
        self.last_hits_content: List[s3.ContentHit] = []
        self.last_elapsed: float = 0.0
        self.last_options: Dict[str, Any] = {}

        self._form_widgets: List[Any] = []

        # Status bar vars (criados no _build_ui)
        self.status_msg_var: tk.StringVar
        self.status_stats_var: tk.StringVar

        self._build_ui()

    def _apply_premium_light_theme(self) -> None:
        """Tema claro com visual mais "premium".

        - Evita branco estourado (fundo levemente cinza)
        - Usa "cards" brancos para contraste
        - Ajusta tipografia e espacamentos
        """

        pal = {
            "bg": "#F3F5F8",          # fundo geral (nao e branco puro)
            "card": "#FFFFFF",        # superfícies / cards
            "border": "#E3E8EF",      # bordas suaves
            "text": "#0F172A",        # texto principal
            "muted": "#64748B",       # texto secundario
            "select": "#DBEAFE",      # selecao (azul suave)
        }
        self._pal = pal

        # Janela e base
        try:
            self.configure(background=pal["bg"])
        except Exception:
            pass

        try:
            # Containers
            self.style.configure("App.TFrame", background=pal["bg"])
            self.style.configure("Card.TFrame", background=pal["card"], borderwidth=1, relief="solid")

            # Separators mais discretos
            self.style.configure("TSeparator", background=pal["border"])

            # Labels
            self.style.configure("TLabel", background=pal["bg"], foreground=pal["text"], font=("Segoe UI", 10))
            self.style.configure("Muted.TLabel", background=pal["bg"], foreground=pal["muted"], font=("Segoe UI", 10))
            self.style.configure("Card.TLabel", background=pal["card"], foreground=pal["text"], font=("Segoe UI", 10))
            self.style.configure("CardMuted.TLabel", background=pal["card"], foreground=pal["muted"], font=("Segoe UI", 10))
            self.style.configure("Title.TLabel", background=pal["bg"], foreground=pal["text"], font=("Segoe UI", 20, "bold"))
            self.style.configure("Subtitle.TLabel", background=pal["bg"], foreground=pal["muted"], font=("Segoe UI", 10))

            # Labelframes como "cards"
            self.style.configure(
                "Card.TLabelframe",
                background=pal["card"],
                borderwidth=1,
                relief="solid",
                bordercolor=pal["border"],
                lightcolor=pal["border"],
                darkcolor=pal["border"],
            )
            self.style.configure(
                "Card.TLabelframe.Label",
                background=pal["bg"],
                foreground=pal["text"],
                font=("Segoe UI", 11, "bold"),
            )

            # Inputs (mais "chunky" e confortavel)
            self.style.configure("TEntry", padding=(10, 8))
            self.style.configure("TCombobox", padding=(10, 7))
            self.style.configure("TSpinbox", padding=(8, 7))

            # Checks dentro de cards
            self.style.configure("TCheckbutton", background=pal["card"], foreground=pal["text"])

            # Notebook (tabs maiores)
            self.style.configure("TNotebook", background=pal["bg"], borderwidth=0)
            self.style.configure("TNotebook.Tab", padding=(18, 10), font=("Segoe UI", 10, "bold"))
            self.style.map(
                "TNotebook.Tab",
                background=[("selected", pal["card"]), ("!selected", pal["bg"])],
                foreground=[("selected", pal["text"]), ("!selected", pal["muted"])],
            )

            # Treeview (tabela) em card
            self.style.configure(
                "Treeview",
                background=pal["card"],
                fieldbackground=pal["card"],
                foreground=pal["text"],
                bordercolor=pal["border"],
                lightcolor=pal["border"],
                darkcolor=pal["border"],
                rowheight=30,
            )
            self.style.map(
                "Treeview",
                background=[("selected", pal["select"])],
                foreground=[("selected", pal["text"])],
            )

            # Botoes (um pouco mais altos)
            self.style.configure("TButton", padding=(14, 10), font=("Segoe UI", 10, "bold"))
            self.style.configure("PrimaryBig.TButton", padding=(18, 12), font=("Segoe UI", 11, "bold"))

            # Status bar
            self.style.configure("Status.TFrame", background=pal["card"], borderwidth=1, relief="solid")
            self.style.configure("Status.TLabel", background=pal["card"], foreground=pal["muted"], font=("Segoe UI", 10))

        except Exception:
            # Se algo nao suportar no tema/OS, nao queremos derrubar o app
            pass

    def _start_activity_animation(self) -> None:
        """Animacao simples (spinner) durante o scan."""
        self._stop_activity_animation()
        frames = ["|", "/", "-", "\\"]

        def tick() -> None:
            if not self._is_scanning:
                return
            self._anim_i = (self._anim_i + 1) % len(frames)
            self.badge_var.set(f"{frames[self._anim_i]} Escaneando")
            self._anim_job = self.after(140, tick)

        self._anim_job = self.after(140, tick)

    def _stop_activity_animation(self) -> None:
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        root = tb.Frame(self, padding=(18, 16), style="App.TFrame")
        root.pack(fill=BOTH, expand=YES)

        # ---- Header "premium" ----
        header = tb.Frame(root, style="App.TFrame")
        header.pack(fill=X, pady=(0, 12))

        left = tb.Frame(header, style="App.TFrame")
        left.pack(side=LEFT, fill=X, expand=YES)

        title_row = tb.Frame(left, style="App.TFrame")
        title_row.pack(anchor=W)
        tb.Label(title_row, text=APP_TITLE, style="Title.TLabel").pack(side=LEFT, anchor=W)
        tb.Label(
            title_row,
            text=f"v{APP_VERSION}",
            bootstyle="primary-inverse",
            padding=(10, 4),
        ).pack(side=LEFT, padx=(10, 0), pady=(6, 0))

        tb.Label(
            left,
            text="Busca por nomes e conteudo • multi-termos (AND)",
            style="Subtitle.TLabel",
        ).pack(anchor=W, pady=(4, 0))

        right = tb.Frame(header, style="App.TFrame")
        right.pack(side=RIGHT)

        # Botao de atualizacao (baixa do GitHub Releases)
        self.btn_update = self._reg(
            tb.Button(
                right,
                text="Atualizar",
                command=self.check_updates,
                bootstyle="success-outline",
            )
        )
        self.btn_update.pack(anchor=E)

        self.badge_var = tk.StringVar(value="Pronto")
        self.lbl_badge = tb.Label(
            right,
            textvariable=self.badge_var,
            bootstyle="secondary-inverse",
            padding=(10, 4),
        )
        self.lbl_badge.pack(anchor=E, pady=(8, 0))

        tb.Separator(root).pack(fill=X, pady=(0, 12))

        self.notebook = tb.Notebook(root)
        self.notebook.pack(fill=BOTH, expand=YES)

        self.tab_search = tb.Frame(self.notebook, padding=12, style="App.TFrame")
        self.tab_results = tb.Frame(self.notebook, padding=12, style="App.TFrame")
        self.notebook.add(self.tab_search, text="Busca")
        self.notebook.add(self.tab_results, text="Resultados")

        self._build_search_tab()
        self._build_results_tab()

        # ---- Status bar (progresso + ETA) ----
        tb.Separator(root).pack(fill=X, pady=(12, 0))
        status = tb.Frame(root, padding=(10, 8), style="Status.TFrame")
        status.pack(fill=X)

        self.status_msg_var = tk.StringVar(value="Pronto.")
        self.status_stats_var = tk.StringVar(value="")

        tb.Label(status, textvariable=self.status_msg_var, style="Status.TLabel").pack(side=LEFT)
        self.progress = tb.Progressbar(status, mode="determinate", bootstyle="striped")
        self.progress.pack(side=LEFT, fill=X, expand=YES, padx=12)
        tb.Label(status, textvariable=self.status_stats_var, style="Status.TLabel").pack(side=RIGHT)

    def _reg(self, w: Any) -> Any:
        self._form_widgets.append(w)
        return w

    def _build_search_tab(self) -> None:
        # ---------- Entrada ----------
        lf_in = tb.Labelframe(self.tab_search, text="Entrada", padding=12, style="Card.TLabelframe")
        lf_in.pack(fill=X)
        lf_in.columnconfigure(1, weight=1)

        self.path_var = tk.StringVar()
        tb.Label(lf_in, text="Caminho (arquivo ou pasta):", style="Card.TLabel").grid(row=0, column=0, sticky=W)
        self.ent_path = self._reg(tb.Entry(lf_in, textvariable=self.path_var))
        self.ent_path.grid(row=0, column=1, sticky=EW, padx=8)
        self.btn_pick_folder = self._reg(tb.Button(lf_in, text="Pasta", command=self.pick_folder, bootstyle="secondary"))
        self.btn_pick_folder.grid(row=0, column=2, padx=(0, 6))
        self.btn_pick_file = self._reg(tb.Button(lf_in, text="Arquivo", command=self.pick_file, bootstyle="secondary"))
        self.btn_pick_file.grid(row=0, column=3)

        self.mode_var = tk.StringVar(value="content")
        self.match_var = tk.StringVar(value="contains")

        tb.Label(lf_in, text="Modo:", style="Card.TLabel").grid(row=1, column=0, sticky=W, pady=(10, 0))
        self.cmb_mode = self._reg(
            tb.Combobox(
                lf_in,
                textvariable=self.mode_var,
                values=["folders", "files", "content"],
                state="readonly",
                width=14,
            )
        )
        self.cmb_mode.grid(row=1, column=1, sticky=W, padx=8, pady=(10, 0))

        tb.Label(lf_in, text="Match:", style="Card.TLabel").grid(row=1, column=2, sticky=E, pady=(10, 0))
        self.cmb_match = self._reg(
            tb.Combobox(
                lf_in,
                textvariable=self.match_var,
                values=["contains", "fuzzy", "regex"],
                state="readonly",
                width=12,
            )
        )
        self.cmb_match.grid(row=1, column=3, sticky=W, pady=(10, 0))

        # label em 2 linhas (evita "cortar")
        tb.Label(lf_in, text="Termos\n(ex.: {experience, 300}):", justify=LEFT, style="Card.TLabel").grid(
            row=2, column=0, sticky=W, pady=(12, 0)
        )
        self.query_var = tk.StringVar()
        self.ent_terms = self._reg(tb.Entry(lf_in, textvariable=self.query_var))
        self.ent_terms.grid(row=2, column=1, columnspan=3, sticky=EW, padx=8, pady=(12, 0))

        # ---------- Opcoes ----------
        lf_opt = tb.Labelframe(self.tab_search, text="Opcoes", padding=12, style="Card.TLabelframe")
        lf_opt.pack(fill=X, pady=(12, 0))

        self.recursive_var = tk.BooleanVar(value=True)
        self.case_var = tk.BooleanVar(value=False)
        self.accents_var = tk.BooleanVar(value=True)

        self.chk_recursive = self._reg(tb.Checkbutton(lf_opt, text="Recursivo", variable=self.recursive_var, bootstyle="round-toggle"))
        self.chk_case = self._reg(tb.Checkbutton(lf_opt, text="Case-sensitive", variable=self.case_var, bootstyle="round-toggle"))
        self.chk_accents = self._reg(tb.Checkbutton(lf_opt, text="Ignorar acentos", variable=self.accents_var, bootstyle="round-toggle"))

        self.chk_recursive.pack(side=LEFT, padx=(0, 16))
        self.chk_case.pack(side=LEFT, padx=(0, 16))
        self.chk_accents.pack(side=LEFT, padx=(0, 16))

        tb.Label(lf_opt, text="(Em regex: ignorar acentos fica desativado)", style="CardMuted.TLabel").pack(side=LEFT)

        # ---------- Conteudo ----------
        self.lf_content = tb.Labelframe(
            self.tab_search,
            text="Opcoes de conteudo (apenas no modo: content)",
            padding=12,
            style="Card.TLabelframe",
        )
        self.lf_content.pack(fill=X, pady=(12, 0))
        self.lf_content.columnconfigure(1, weight=1)

        self.exts_var = tk.StringVar(value="")
        tb.Label(self.lf_content, text="Extensoes (csv):", style="Card.TLabel").grid(row=0, column=0, sticky=W)
        self.ent_exts = self._reg(tb.Entry(self.lf_content, textvariable=self.exts_var))
        self.ent_exts.grid(row=0, column=1, sticky=EW, padx=8)
        tb.Label(self.lf_content, text="Ex.: .txt,.py (vazio = qualquer)", style="CardMuted.TLabel").grid(
            row=0, column=2, sticky=W
        )

        # trechos
        self.max_examples_var = tk.IntVar(value=3)
        self.all_examples_var = tk.BooleanVar(value=True)  # "Todos" por padrao

        tb.Label(self.lf_content, text="Trechos por arquivo:", style="Card.TLabel").grid(row=1, column=0, sticky=W, pady=(10, 0))
        self.spin_examples = self._reg(
            tb.Spinbox(
                self.lf_content,
                from_=0,
                to=999999,
                textvariable=self.max_examples_var,
                width=10,
            )
        )
        self.spin_examples.grid(row=1, column=1, sticky=W, padx=8, pady=(10, 0))

        self.chk_all_examples = self._reg(
            tb.Checkbutton(
                self.lf_content,
                text="Todos",
                variable=self.all_examples_var,
                bootstyle="round-toggle",
                command=self._sync_examples_controls,
            )
        )
        self.chk_all_examples.grid(row=1, column=2, sticky=W, pady=(10, 0))

        tb.Label(self.lf_content, text="(0 = nenhum)", style="CardMuted.TLabel").grid(row=1, column=3, sticky=W, pady=(10, 0))

        # limite de tamanho
        self.limit_size_var = tk.BooleanVar(value=True)
        self.max_mb_var = tk.IntVar(value=50)

        self.chk_limit_size = self._reg(
            tb.Checkbutton(
                self.lf_content,
                text="Ignorar arquivos maiores que (MB):",
                variable=self.limit_size_var,
                bootstyle="round-toggle",
                command=self._sync_size_controls,
            )
        )
        self.chk_limit_size.grid(row=2, column=0, sticky=W, pady=(10, 0))

        self.spin_max_mb = self._reg(
            tb.Spinbox(
                self.lf_content,
                from_=0,
                to=200000,
                textvariable=self.max_mb_var,
                width=10,
            )
        )
        self.spin_max_mb.grid(row=2, column=1, sticky=W, padx=8, pady=(10, 0))
        tb.Label(self.lf_content, text="(0 = sem limite)", style="CardMuted.TLabel").grid(row=2, column=2, sticky=W, pady=(10, 0))

        # ---------- Footer / Scan ----------
        footer = tb.Frame(self.tab_search, style="App.TFrame")
        footer.pack(fill=X, pady=(14, 0))

        # Acoes principais com uma paleta mais "clean"
        self.btn_scan = self._reg(tb.Button(footer, text="Scan", command=self.start_scan, bootstyle="primary", style="PrimaryBig.TButton"))
        self.btn_stop = tb.Button(footer, text="Parar", command=self.stop_scan, bootstyle="danger-outline", state="disabled")
        self.btn_scan.pack(side=LEFT)
        self.btn_stop.pack(side=LEFT, padx=8)

        tb.Label(footer, text="Progresso e ETA na barra inferior", style="Muted.TLabel").pack(side=RIGHT)

        # callbacks
        self.mode_var.trace_add("write", lambda *_: self._sync_mode_controls())
        self.match_var.trace_add("write", lambda *_: self._sync_match_controls())

        # inicial
        self._sync_examples_controls()
        self._sync_size_controls()
        self._sync_mode_controls()
        self._sync_match_controls()

    def _build_results_tab(self) -> None:
        top = tb.Frame(self.tab_results, style="App.TFrame")
        top.pack(fill=X, pady=(0, 10))

        self.btn_export_csv = tb.Button(top, text="Exportar CSV", command=self.export_csv, bootstyle="primary-outline", state="disabled")
        self.btn_export_html = tb.Button(top, text="Exportar HTML", command=self.export_html, bootstyle="primary-outline", state="disabled")
        self.btn_open_selected = tb.Button(top, text="Abrir selecionado", command=self.open_selected, bootstyle="secondary", state="disabled")
        self.btn_clear = tb.Button(top, text="Limpar", command=self.clear_results, bootstyle="secondary")

        self.btn_export_csv.pack(side=LEFT)
        self.btn_export_html.pack(side=LEFT, padx=8)
        self.btn_open_selected.pack(side=LEFT, padx=8)
        self.btn_clear.pack(side=LEFT)

        self.res_summary = tk.StringVar(value="Sem resultados.")
        tb.Label(top, textvariable=self.res_summary, style="Muted.TLabel").pack(side=RIGHT)

        body = tb.Frame(self.tab_results, style="App.TFrame")
        body.pack(fill=BOTH, expand=YES)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)
        # area esquerda: tabela em "card"
        left = tb.Frame(body, style="Card.TFrame", padding=8)
        left.grid(row=0, column=0, sticky="nsew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.tree = tb.Treeview(left, columns=("c1", "path"), show="headings")
        self.tree.heading("c1", text="Info")
        self.tree.heading("path", text="Caminho")
        self.tree.column("c1", width=150, anchor=E)
        self.tree.column("path", width=720, anchor=W)

        yscroll = tb.Scrollbar(left, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")

        # detalhes (card)
        detail = tb.Labelframe(body, text="Detalhes", padding=10, style="Card.TLabelframe")
        detail.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        detail.rowconfigure(0, weight=1)
        detail.columnconfigure(0, weight=1)

        # Detalhes em estilo claro (mais "premium")
        pal = getattr(self, "_pal", {"card": "#FFFFFF", "text": "#111827", "border": "#E5E7EB"})
        self.txt_detail = tk.Text(
            detail,
            wrap="word",
            height=10,
            background=pal["card"],
            foreground=pal["text"],
            insertbackground=pal["text"],
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=pal["border"],
            highlightcolor=pal["border"],
        )
        try:
            self.txt_detail.configure(padx=10, pady=8)
        except Exception:
            pass
        self.txt_detail.grid(row=0, column=0, sticky="nsew")
        dscroll = tb.Scrollbar(detail, orient=VERTICAL, command=self.txt_detail.yview)
        self.txt_detail.configure(yscrollcommand=dscroll.set)
        dscroll.grid(row=0, column=1, sticky="ns")

        self._iid_to_obj: Dict[str, Any] = {}
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda _e: self.open_selected())

    # ---------------- Sync UI ----------------
    def _sync_examples_controls(self) -> None:
        # Se "Todos" marcado => desabilita input de trechos por arquivo
        if self.all_examples_var.get():
            safe_set_state(self.spin_examples, "disabled")
        else:
            safe_set_state(self.spin_examples, "normal")

    def _sync_size_controls(self) -> None:
        if self.limit_size_var.get():
            safe_set_state(self.spin_max_mb, "normal")
        else:
            safe_set_state(self.spin_max_mb, "disabled")

    def _sync_mode_controls(self) -> None:
        is_content = (self.mode_var.get() == "content")
        # habilita/desabilita todos widgets dentro do grupo de conteudo
        for w in (self.ent_exts, self.spin_examples, self.chk_all_examples, self.chk_limit_size, self.spin_max_mb):
            safe_set_state(w, "normal" if is_content else "disabled")
        # se nao for content, ainda assim mantenha o spin_examples coerente (quando voltar)
        if is_content:
            self._sync_examples_controls()
            self._sync_size_controls()

    def _sync_match_controls(self) -> None:
        # Em regex, ignorar acentos nao faz sentido (e no scanner3 ele desativa)
        is_regex = (self.match_var.get() == "regex")
        if is_regex:
            self.accents_var.set(False)
            safe_set_state(self.chk_accents, "disabled")
        else:
            safe_set_state(self.chk_accents, "normal")

    # ---------------- Browse ----------------
    def pick_folder(self) -> None:
        p = filedialog.askdirectory()
        if p:
            self.path_var.set(p)

    def pick_file(self) -> None:
        p = filedialog.askopenfilename()
        if p:
            self.path_var.set(p)

    # ---------------- Scan flow ----------------
    def set_form_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for w in self._form_widgets:
            safe_set_state(w, state)
        # botoes do scan
        safe_set_state(self.btn_scan, "normal" if enabled else "disabled")
        safe_set_state(self.btn_stop, "disabled" if enabled else "normal")

    def start_scan(self) -> None:
        if self._scan_thread and self._scan_thread.is_alive():
            return

        raw_path = self.path_var.get().strip()
        raw_query = self.query_var.get().strip()
        if not raw_path:
            messagebox.showerror("Erro", "Informe um caminho (arquivo ou pasta).")
            return
        if not raw_query:
            messagebox.showerror("Erro", "Informe os termos de busca.")
            return

        base = Path(raw_path).expanduser()
        if not base.exists():
            messagebox.showerror("Erro", "Esse caminho nao existe.")
            return

        # limpar
        self.clear_results()

        self._stop_flag = False
        self._is_scanning = True
        self.last_elapsed = 0.0

        self.badge_var.set("Preparando")
        try:
            self.lbl_badge.configure(bootstyle="info-inverse")
        except Exception:
            pass
        self.status_msg_var.set("Preparando...")
        self.status_stats_var.set("")

        # Animacao de atividade (spinner) + feedback visual
        self._start_activity_animation()

        # Enquanto contamos/paramos etc: indeterminado
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)

        # Reset ETA
        self._scan_start_wall = None
        self._scan_total = 0
        self._scan_done = 0
        self._scan_phase = ""

        self.set_form_enabled(False)
        self.btn_export_csv.configure(state="disabled")
        self.btn_export_html.configure(state="disabled")
        self.btn_open_selected.configure(state="disabled")

        self._scan_thread = threading.Thread(target=self._run_scan, daemon=True)
        self._scan_thread.start()

    def stop_scan(self) -> None:
        self._stop_flag = True
        self.badge_var.set("Parando")
        try:
            self.lbl_badge.configure(bootstyle="danger-inverse")
        except Exception:
            pass
        self.status_msg_var.set("Parando...")

    def _run_scan(self) -> None:
        try:
            base = Path(self.path_var.get().strip()).expanduser()
            mode = self.mode_var.get().strip()
            match_ui = self.match_var.get().strip()

            queries = s3.parse_queries(self.query_var.get().strip())
            query_display = s3.format_queries(queries)

            match_map = {
                "contains": s3.MatchMode.CONTAINS,
                "fuzzy": s3.MatchMode.FUZZY,
                "regex": s3.MatchMode.REGEX,
            }
            match_mode = match_map.get(match_ui, s3.MatchMode.CONTAINS)

            base_cfg = s3.MatchConfig(
                query="",
                mode=match_mode,
                case_sensitive=bool(self.case_var.get()),
                ignore_accents=bool(self.accents_var.get()) if match_mode != s3.MatchMode.REGEX else False,
                fuzzy_threshold=0.78,
                regex_flags=0,
            )

            matcher = s3.build_multi_matcher(queries, base_cfg)
            recursive = bool(self.recursive_var.get()) if base.is_dir() else False
            ignore_dirs = set(s3.DEFAULT_IGNORE_DIRS)

            start = time.time()

            if mode in ("folders", "files"):
                want_dirs = (mode == "folders")
                want_files = (mode == "files")

                # contar
                total = s3.count_iter(s3.iter_name_candidates(base, recursive, ignore_dirs, want_dirs, want_files))
                self._ui_set_progress(total, "Escaneando nomes...")

                last_ui = time.monotonic()

                hits: List[s3.NameHit] = []
                done = 0
                for p, kind in s3.iter_name_candidates(base, recursive, ignore_dirs, want_dirs, want_files):
                    if self._stop_flag:
                        break
                    if matcher(p.name):
                        hits.append(s3.NameHit(path=p, kind=kind))
                    done += 1
                    now = time.monotonic()
                    if done == total or (now - last_ui) >= 0.15:
                        last_ui = now
                        self._ui_progress(done, total)

                elapsed = time.time() - start
                self._ui_finish_names(
                    base=base,
                    mode=mode,
                    queries=queries,
                    query_display=query_display,
                    hits=hits,
                    elapsed=elapsed,
                    options={
                        "Recursivo": recursive,
                        "Case-sensitive": base_cfg.case_sensitive,
                        "Ignorar acentos": base_cfg.ignore_accents if match_mode != s3.MatchMode.REGEX else "N/A (regex)",
                        "Modo de match": match_ui,
                        "Termos (AND)": query_display,
                        "Pastas ignoradas": ", ".join(sorted(ignore_dirs)) if ignore_dirs else "(nenhuma)",
                    },
                    stopped=self._stop_flag,
                )
                return

            # mode == content
            raw_exts = self.exts_var.get().strip()
            exts: Optional[set[str]] = None
            if raw_exts:
                parsed: set[str] = set()
                for part in raw_exts.split(","):
                    t = part.strip().lower()
                    if not t:
                        continue
                    if not t.startswith("."):
                        t = "." + t
                    parsed.add(t)
                exts = parsed if parsed else None

            # trechos
            max_examples: Optional[int]
            if self.all_examples_var.get():
                max_examples = None
            else:
                v = int(self.max_examples_var.get())
                max_examples = v

            # limite tamanho
            max_mb: Optional[int] = None
            if self.limit_size_var.get():
                v = int(self.max_mb_var.get())
                max_mb = None if v <= 0 else v
            else:
                max_mb = None

            total = s3.count_iter(s3.iter_content_candidates(base, recursive, ignore_dirs, exts))
            self._ui_set_progress(total, "Escaneando conteudo...")

            last_ui = time.monotonic()

            hits_c: List[s3.ContentHit] = []
            done = 0
            for p in s3.iter_content_candidates(base, recursive, ignore_dirs, exts):
                if self._stop_flag:
                    break
                hit = s3.scan_file_content(p, matcher, max_examples=max_examples, max_file_size_mb=max_mb)
                if hit is not None:
                    hits_c.append(hit)
                done += 1
                now = time.monotonic()
                if done == total or (now - last_ui) >= 0.15:
                    last_ui = now
                    self._ui_progress(done, total)

            elapsed = time.time() - start
            self._ui_finish_content(
                base=base,
                mode=mode,
                queries=queries,
                query_display=query_display,
                hits=hits_c,
                elapsed=elapsed,
                options={
                    "Recursivo": recursive,
                    "Case-sensitive": base_cfg.case_sensitive,
                    "Ignorar acentos": base_cfg.ignore_accents if match_mode != s3.MatchMode.REGEX else "N/A (regex)",
                    "Modo de match": match_ui,
                    "Termos (AND)": query_display,
                    "Extensoes": ", ".join(sorted(exts)) if exts else "(qualquer)",
                    "Trechos por arquivo": "Todos" if max_examples is None else max_examples,
                    "Ignorar arquivos > (MB)": "Sem limite" if max_mb is None else max_mb,
                    "Pastas ignoradas": ", ".join(sorted(ignore_dirs)) if ignore_dirs else "(nenhuma)",
                },
                stopped=self._stop_flag,
            )

        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Erro", str(e)))
        finally:
            self.after(0, self._ui_done)

    # ---------------- UI updates from thread ----------------
    def _ui_set_progress(self, total: int, status: str) -> None:
        def _() -> None:
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=max(1, total))
            self.progress["value"] = 0
            self._scan_start_wall = time.time()
            self._scan_total = int(total)
            self._scan_done = 0
            self._scan_phase = status
            self.badge_var.set("Escaneando")
            self.status_msg_var.set(status)
            self.status_stats_var.set(f"0/{max(1, total)} • 00:00 • ETA --:--")
        self.after(0, _)

    def _ui_progress(self, done: int, total: int) -> None:
        def _() -> None:
            self.progress["value"] = done
            self._scan_done = int(done)
            self._scan_total = int(total)

            start = self._scan_start_wall or time.time()
            elapsed = max(0.0, time.time() - start)

            eta: Optional[float] = None
            # ETA so fica confiavel depois de alguns itens
            if done >= 5 and elapsed >= 0.8 and total > 0 and done <= total:
                rate = done / elapsed
                if rate > 0:
                    eta = (total - done) / rate

            self.status_stats_var.set(
                f"{done}/{total} • {_fmt_hhmmss(elapsed)} • ETA {_fmt_hhmmss(eta)}"
            )
        self.after(0, _)

    def _ui_finish_names(
        self,
        *,
        base: Path,
        mode: str,
        queries: List[str],
        query_display: str,
        hits: List[s3.NameHit],
        elapsed: float,
        options: Dict[str, Any],
        stopped: bool,
    ) -> None:
        def _() -> None:
            self.last_mode = mode
            self.last_queries = queries
            self.last_base = base
            self.last_hits_names = hits
            self.last_hits_content = []
            self.last_elapsed = elapsed
            self.last_options = options

            self._populate_tree_names(hits)
            self.notebook.select(self.tab_results)

            label = "Parado" if stopped else "Concluido"
            self.res_summary.set(f"{label}: {len(hits)} encontrados em {elapsed:.2f}s")
        self.after(0, _)

    def _ui_finish_content(
        self,
        *,
        base: Path,
        mode: str,
        queries: List[str],
        query_display: str,
        hits: List[s3.ContentHit],
        elapsed: float,
        options: Dict[str, Any],
        stopped: bool,
    ) -> None:
        def _() -> None:
            self.last_mode = mode
            self.last_queries = queries
            self.last_base = base
            self.last_hits_content = hits
            self.last_hits_names = []
            self.last_elapsed = elapsed
            self.last_options = options

            self._populate_tree_content(hits)
            self.notebook.select(self.tab_results)

            label = "Parado" if stopped else "Concluido"
            self.res_summary.set(f"{label}: {len(hits)} arquivos com match em {elapsed:.2f}s")
        self.after(0, _)

    def _ui_done(self) -> None:
        self._is_scanning = False
        self._stop_activity_animation()
        self.progress.stop()
        self.badge_var.set("Pronto" if not self._stop_flag else "Parado")
        try:
            self.lbl_badge.configure(bootstyle="secondary-inverse" if not self._stop_flag else "warning-inverse")
        except Exception:
            pass
        self.status_msg_var.set("Pronto." if not self._stop_flag else "Parado.")
        # Mantem um resumo do tempo final (se houver)
        if self.last_elapsed > 0:
            self.status_stats_var.set(f"Tempo: {_fmt_hhmmss(self.last_elapsed)}")
        else:
            self.status_stats_var.set("")
        self.set_form_enabled(True)

        has_results = bool(self.last_hits_names or self.last_hits_content)
        self.btn_export_csv.configure(state="normal" if has_results else "disabled")
        self.btn_export_html.configure(state="normal" if has_results else "disabled")
        self.btn_open_selected.configure(state="normal" if has_results else "disabled")

    # ---------------- Results UI ----------------
    def clear_results(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._iid_to_obj.clear()
        self.txt_detail.configure(state="normal")
        self.txt_detail.delete("1.0", END)
        self.txt_detail.configure(state="normal")
        self.res_summary.set("Sem resultados.")

    def _populate_tree_names(self, hits: List[s3.NameHit]) -> None:
        # headings
        self.tree.heading("c1", text="Tipo")
        self.tree.heading("path", text="Caminho")
        self.tree.column("c1", width=140, anchor=W)

        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._iid_to_obj.clear()

        for h in sorted(hits, key=lambda x: str(x.path).lower()):
            iid = self.tree.insert("", END, values=(h.kind, str(h.path)))
            self._iid_to_obj[iid] = h

    def _populate_tree_content(self, hits: List[s3.ContentHit]) -> None:
        self.tree.heading("c1", text="Linhas")
        self.tree.heading("path", text="Arquivo")
        self.tree.column("c1", width=90, anchor=E)

        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._iid_to_obj.clear()

        hits_sorted = sorted(hits, key=lambda x: (-x.matches_count, str(x.path).lower()))
        for h in hits_sorted:
            iid = self.tree.insert("", END, values=(h.matches_count, str(h.path)))
            self._iid_to_obj[iid] = h

    def on_select(self, _event: Any) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        obj = self._iid_to_obj.get(iid)
        if obj is None:
            return

        self.txt_detail.configure(state="normal")
        self.txt_detail.delete("1.0", END)

        if isinstance(obj, s3.NameHit):
            self.txt_detail.insert(END, f"Tipo: {obj.kind}\n")
            self.txt_detail.insert(END, f"Caminho: {obj.path}\n")
        else:
            # ContentHit
            self.txt_detail.insert(END, f"Arquivo: {obj.path}\n")
            self.txt_detail.insert(END, f"Linhas com TODOS os termos: {obj.matches_count}\n\n")
            if obj.examples:
                self.txt_detail.insert(END, "Trechos:\n")
                for ln, txt in obj.examples[:500]:
                    self.txt_detail.insert(END, f"  - L{ln}: {txt}\n")
            else:
                self.txt_detail.insert(END, "Sem trechos coletados.\n")

        self.txt_detail.configure(state="normal")

    def open_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        obj = self._iid_to_obj.get(iid)
        if obj is None:
            return

        p = obj.path if hasattr(obj, "path") else None
        if p is None:
            return
        if p.exists():
            open_path(p)

    # ---------------- Export ----------------
    def export_csv(self) -> None:
        if not (self.last_hits_names or self.last_hits_content):
            return
        base = self.last_base or Path.cwd()
        ts = time.strftime("%Y%m%d-%H%M%S")
        default_name = f"resultados-{ts}.csv"

        out = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialdir=str(base if base.is_dir() else base.parent),
            initialfile=default_name,
            filetypes=[("CSV", "*.csv"), ("Todos", "*.*")],
        )
        if not out:
            return
        outp = Path(out)

        try:
            if self.last_mode in ("folders", "files"):
                s3.export_csv_names(outp, self.last_hits_names)
            else:
                s3.export_csv_contents(outp, self.last_hits_content)
            messagebox.showinfo("OK", f"CSV gerado em:\n{outp}")
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def export_html(self) -> None:
        if not (self.last_hits_names or self.last_hits_content):
            return
        base = self.last_base or Path.cwd()
        ts = time.strftime("%Y%m%d-%H%M%S")
        default_name = f"relatorio-{ts}.html"

        out = filedialog.asksaveasfilename(
            defaultextension=".html",
            initialdir=str(base if base.is_dir() else base.parent),
            initialfile=default_name,
            filetypes=[("HTML", "*.html"), ("Todos", "*.*")],
        )
        if not out:
            return
        outp = Path(out)

        try:
            if self.last_mode in ("folders", "files"):
                report = s3.make_html_report_names(
                    title="Relatorio do Scanner (GUI)",
                    base=self.last_base or Path.cwd(),
                    query=s3.format_queries(self.last_queries),
                    mode_label="Busca por nomes (AND)",
                    options=self.last_options,
                    hits=self.last_hits_names,
                    elapsed_s=self.last_elapsed,
                )
            else:
                report = s3.make_html_report_contents(
                    title="Relatorio do Scanner (GUI)",
                    base=self.last_base or Path.cwd(),
                    query=s3.format_queries(self.last_queries),
                    mode_label="Busca por conteudo (AND)",
                    options=self.last_options,
                    hits=self.last_hits_content,
                    elapsed_s=self.last_elapsed,
                )
            outp.write_text(report, encoding="utf-8")
            messagebox.showinfo("OK", f"HTML gerado em:\n{outp}")
            # opcional: abrir automaticamente
            # open_path(outp)
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    # ---------------- Update (GitHub Releases) ----------------
    def _ask_yesno_ts(self, title: str, msg: str) -> bool:
        """Pergunta yes/no na UI thread e devolve o resultado para a thread chamadora."""
        ev = threading.Event()
        out: Dict[str, bool] = {"v": False}

        def _() -> None:
            out["v"] = messagebox.askyesno(title, msg)
            ev.set()

        self.after(0, _)
        ev.wait()
        return out["v"]

    def _info_ts(self, title: str, msg: str) -> None:
        self.after(0, lambda: messagebox.showinfo(title, msg))

    def _error_ts(self, title: str, msg: str) -> None:
        self.after(0, lambda: messagebox.showerror(title, msg))

    def check_updates(self) -> None:
        """Botao 'Atualizar': procura a ultima versao no GitHub Releases e atualiza o exe."""
        if self._is_scanning:
            messagebox.showinfo("Atualizar", "Aguarde o scan terminar para atualizar.")
            return

        if GITHUB_OWNER == "SEU_USUARIO" or GITHUB_REPO == "SEU_REPO":
            messagebox.showwarning(
                "Atualizar",
                "Configure GITHUB_OWNER/GITHUB_REPO no topo do arquivo antes de usar o auto-update.",
            )
            return

        if not getattr(sys, "frozen", False):
            # Em modo .py (dev), o mais correto e usar Git.
            messagebox.showinfo(
                "Atualizar",
                "Voce esta rodando o .py (modo dev).\n\n"
                "Para atualizar: use Git (git pull).\n"
                "No EXE, o botao baixa a versao mais nova via GitHub Releases.",
            )
            return

        t = threading.Thread(target=self._update_flow_thread, daemon=True)
        t.start()

    def _update_flow_thread(self) -> None:
        try:
            self.after(0, lambda: self.status_msg_var.set("Verificando atualizacoes..."))
            latest = upd.get_latest_release(GITHUB_OWNER, GITHUB_REPO)

            current_tag = f"v{APP_VERSION}" if not APP_VERSION.lower().startswith("v") else APP_VERSION
            if not upd.is_newer(latest.tag_name, current_tag):
                self._info_ts("Atualizar", f"Voce ja esta na versao {APP_VERSION}.")
                self.after(0, lambda: self.status_msg_var.set("Pronto."))
                return

            msg = (
                f"Nova versao disponivel: {latest.tag_name}\n"
                f"Versao atual: {APP_VERSION}\n\n"
                "Deseja baixar e instalar agora?\n"
                "(O programa vai reiniciar)"
            )
            if not self._ask_yesno_ts("Atualizar", msg):
                self.after(0, lambda: self.status_msg_var.set("Pronto."))
                return

            asset = upd.pick_asset(latest, preferred_name=GITHUB_ASSET_NAME)
            if asset is None:
                self._error_ts(
                    "Atualizar",
                    f"Nao encontrei um asset .exe no Release {latest.tag_name}.\n"
                    f"Anexe um arquivo chamado '{GITHUB_ASSET_NAME}' (ou qualquer .exe) no Release.",
                )
                self.after(0, lambda: self.status_msg_var.set("Pronto."))
                return

            # download (usa a barra inferior)
            self.after(0, lambda: self.badge_var.set("Baixando"))
            self.after(0, lambda: self.status_msg_var.set("Baixando atualizacao..."))
            self.after(0, lambda: self.progress.configure(mode="determinate", maximum=100))
            self.after(0, lambda: self.progress.configure(value=0))

            def on_progress(done: int, total: int) -> None:
                pct = 0
                if total > 0:
                    pct = int(done * 100 / total)
                self.after(0, lambda: self.progress.configure(value=pct))
                self.after(0, lambda: self.status_stats_var.set(f"{pct}%"))

            downloaded = upd.download_asset(asset, progress_cb=on_progress)

            # prepara troca (precisa sair do processo atual)
            old_exe = Path(sys.executable).resolve()
            pid = os.getpid()

            self.after(0, lambda: self.badge_var.set("Atualizando"))
            self.after(0, lambda: self.status_msg_var.set("Aplicando atualizacao..."))

            upd.launch_replace_and_restart(old_exe, downloaded, pid)
            # Fecha o app atual para liberar o arquivo
            self.after(0, self.destroy)

        except Exception as e:
            self._error_ts("Atualizar", str(e))
            self.after(0, lambda: self.badge_var.set("Pronto"))
            self.after(0, lambda: self.status_msg_var.set("Pronto."))


if __name__ == "__main__":
    app = ScannerGUI()
    app.mainloop()
