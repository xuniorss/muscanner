# Deploy / Releases (MuScanner)

Este projeto publica um **ScannerGUI.exe** em GitHub Releases e o app faz **auto-update** baixando o asset do release mais recente.

## Publicar uma nova versao

### Metodo rapido (recomendado)

No Windows, com Git instalado:

```bat
tools\release.bat 0.1.5
```

Isso:
- Atualiza `APP_VERSION` em `gui_scanner_pro.py`
- Cria commit
- Cria tag `v0.1.5`
- Faz push do commit e da tag

Quando a tag chega no GitHub, o workflow **Build Windows EXE (Release)**:
- Builda o `ScannerGUI.exe`
- Cria/atualiza a release da tag
- Anexa `dist/ScannerGUI.exe` na release

### Metodo manual

1) Atualize `APP_VERSION` em `gui_scanner_pro.py`

2) Commit + tag:

```bash
git add gui_scanner_pro.py
git commit -m "Release v0.1.5"
git tag v0.1.5
git push
git push origin v0.1.5
```

## Auto-update no app

O botao **Atualizar**:
- consulta o endpoint do GitHub: `/releases/latest`
- baixa o asset `ScannerGUI.exe`
- aplica a atualizacao via PowerShell (mais robusto com Unicode/acentos)
- se nao conseguir atualizar no local atual, faz fallback e instala em:
  `%LOCALAPPDATA%\MuScanner\ScannerGUI.exe`

### Rodar de qualquer pasta (bootstrap)

Para evitar falhas de update quando o exe esta em pastas como **Desktop/Downloads/OneDrive** (ou caminhos com acentos), o app:
- ao iniciar, copia-se para `%LOCALAPPDATA%\MuScanner\ScannerGUI.exe`
- relanca automaticamente por la

Assim o usuario pode abrir o exe de qualquer lugar, e o app sempre fica em um local estavel e gravavel.

### Modo portable (sem bootstrap)

Se voce quiser rodar **100% portable** (sem copiar para LocalAppData), crie um arquivo vazio chamado:

`portable.mode`

na mesma pasta do `ScannerGUI.exe`.

## Dica de suporte

O log de atualizacao fica em:

`%TEMP%\MuScannerUpdate\update_log.txt`
