# PluginUpdater

Ein Minecraft Plugin Updater mit CustomTkinter-GUI. Die App verwaltet einen `plugins`-Ordner in deinem Serverordner, erkennt Plugin-Namen und Versionen aus `.jar`-Dateien und sucht Updates über Modrinth.

## Funktionen

- Serverordner auswählen und automatisch einen `plugins`-Ordner anlegen
- `.jar`-Dateien aus einem beliebigen Ordner in den `plugins`-Ordner importieren
- Plugin-Metadaten aus `plugin.yml`, `paper-plugin.yml`, `bungee.yml` und `velocity-plugin.json` lesen
- Modrinth-Links pro Plugin speichern
- Einzelne Plugins oder alle Plugins auf Updates prüfen
- Fortschrittsbalken beim Prüfen, Installieren und Herunterladen
- Modrinth-Projekte direkt in der App suchen und installieren
- Modrinth-Links automatisch finden und speichern
- Umschalter für White Mode, Dark Mode bleibt Standard
- Neue Version herunterladen, alte Datei als `.bak` sichern und bei Downloadfehlern automatisch zurückrollen
- Konfiguration in `plugin_config.json` neben der App speichern

## Installation aus dem Quellcode

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Falls `py` nicht verfügbar ist, installiere Python 3.10 oder neuer von <https://www.python.org/>.

## Nutzung

1. App starten.
2. Deinen Minecraft-Serverordner auswählen.
3. Optional über `JARs importieren` vorhandene Plugin-Dateien in den Serverordner übernehmen.
4. Über `Modrinth` direkt nach Plugins suchen und installieren.
5. Bei vorhandenen Plugins `Links automatisch suchen` oder den `Suchen`-Button pro Plugin verwenden.
6. `Update` für ein Plugin oder `Alle prüfen` verwenden.

## Release bauen

```powershell
pip install pyinstaller -r requirements.txt
pyinstaller --noconsole --onefile --clean --name Plugin-updater --icon logo.ico --add-data "logo.ico;." --collect-data customtkinter main.py
```

Die ausführbare Datei liegt danach unter `dist/Plugin-updater.exe`.

## Hinweis

Modrinth liefert Updates nur für Projekte, die dort vorhanden sind. Plugins von Spigot, BukkitDev, GitHub Releases oder privaten Quellen müssen weiterhin manuell verwaltet werden.
