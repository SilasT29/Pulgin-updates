import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox
from urllib.parse import quote_plus, urlparse

import customtkinter as ctk
import requests
from PIL import Image


API_BASE_URL = "https://api.modrinth.com/v2"
APP_NAME = "Plugin Updater"
CONFIG_FILE = "plugin_config.json"
DEFAULT_PLUGIN_FOLDER = "plugins"
USER_AGENT = "PluginUpdaterBySilasT29/3.2 (Contact: SilasT29)"
HEADERS = {"User-Agent": USER_AGENT}
REQUEST_TIMEOUT = 20
DOWNLOAD_CHUNK_SIZE = 1024 * 128


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", app_dir()))
    return base / name


def config_path() -> Path:
    return app_dir() / CONFIG_FILE


def load_json(path: Path, fallback):
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return fallback


def save_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)


def read_plugin_metadata(jar_path: Path) -> dict:
    metadata = {}
    try:
        with zipfile.ZipFile(jar_path, "r") as zip_ref:
            for candidate in ("plugin.yml", "paper-plugin.yml", "bungee.yml", "velocity-plugin.json"):
                if candidate not in zip_ref.namelist():
                    continue
                with zip_ref.open(candidate) as file:
                    content = file.read().decode("utf-8", "replace")
                if candidate.endswith(".json"):
                    data = json.loads(content)
                    metadata["name"] = data.get("name") or data.get("id")
                    metadata["version"] = data.get("version")
                else:
                    metadata.update(parse_plugin_yml(content))
                break
    except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError):
        return {}
    return {key: value for key, value in metadata.items() if value}


def parse_plugin_yml(content: str) -> dict:
    metadata = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip().strip("'\"")
        if key in {"name", "version"} and value:
            metadata[key] = value
    return metadata


def extract_version(filename: str) -> str | None:
    stem = Path(filename).stem
    match = re.search(r"(?:^|[-_\s]v?)(\d+(?:[.\w+-]*\d|[.\w+-]*))$", stem, re.IGNORECASE)
    return match.group(1) if match else None


def version_key(value: str | None) -> tuple:
    if not value:
        return tuple()

    weights = {
        "snapshot": -5,
        "dev": -4,
        "alpha": -3,
        "a": -3,
        "beta": -2,
        "b": -2,
        "pre": -1,
        "rc": 0,
        "release": 1,
        "stable": 1,
    }
    tokens = re.findall(r"\d+|[A-Za-z]+", value.lower())
    numbers = []
    qualifier_weight = 1
    qualifier_number = 0
    seen_qualifier = False
    for token in tokens:
        if token.isdigit() and not seen_qualifier:
            numbers.append(int(token))
            continue
        if token.isdigit():
            qualifier_number = int(token)
            continue
        seen_qualifier = True
        qualifier_weight = min(qualifier_weight, weights.get(token, 1))

    numbers = (numbers + [0, 0, 0, 0])[:4]
    return tuple(numbers + [qualifier_weight, qualifier_number])


def modrinth_slug_from_link(link: str) -> str | None:
    parsed = urlparse(link.strip())
    if parsed.netloc.lower() not in {"modrinth.com", "www.modrinth.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"plugin", "mod", "datapack"}:
        return parts[1]
    return None


def choose_download_file(version: dict) -> dict | None:
    files = version.get("files") or []
    if not files:
        return None
    for file in files:
        if file.get("primary"):
            return file
    return files[0]


class ModrinthClient:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)

    def get_json(self, url: str, params: dict | None = None):
        response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def latest_release_by_slug(self, slug: str) -> dict | None:
        return self.latest_release_by_project(slug)

    def latest_release_by_project(self, project_id_or_slug: str) -> dict | None:
        versions = self.get_json(
            f"{API_BASE_URL}/project/{quote_plus(project_id_or_slug)}/version",
            {"include_changelog": "false"},
        )
        return self._latest_release_from_versions(versions)

    def search_projects(self, query: str, limit: int = 8) -> list[dict]:
        if not query.strip():
            return []
        facets = json.dumps([["project_type:mod"], ["server_side:required", "server_side:optional"]])
        search = self.get_json(
            f"{API_BASE_URL}/search",
            {
                "query": query,
                "limit": limit,
                "facets": facets,
                "index": "relevance",
            },
        )
        return search.get("hits", [])

    def latest_release_by_name(self, plugin_name: str) -> dict | None:
        for hit in self.search_projects(plugin_name, limit=8):
            info = self.latest_release_by_project(hit["project_id"])
            if info:
                info["project_slug"] = hit.get("slug")
                info["project_title"] = hit.get("title")
                info["project_url"] = modrinth_url(hit.get("slug") or hit["project_id"])
                return info
        return None

    def _latest_release_from_versions(self, versions: list[dict]) -> dict | None:
        for version in versions:
            if version.get("version_type") != "release":
                continue
            file = choose_download_file(version)
            if not file:
                continue
            return {
                "version": version.get("version_number", "unknown"),
                "url": file.get("url"),
                "filename": file.get("filename") or "plugin.jar",
                "size": file.get("size") or 0,
            }
        return None

    def download(self, url: str, destination: Path, progress_callback=None) -> None:
        with self.session.get(url, stream=True, timeout=REQUEST_TIMEOUT) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)
            downloaded = 0
            with destination.open("wb") as file:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if chunk:
                        file.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total)


def modrinth_url(slug: str | None) -> str:
    if not slug:
        return ""
    return f"https://modrinth.com/plugin/{slug}"


class PluginRow(ctk.CTkFrame):
    def __init__(self, master, plugin, link, on_link_change, on_update, on_find_link):
        super().__init__(master)
        self.plugin = plugin
        self.link = link
        self.on_link_change = on_link_change

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        version = plugin["version"] or "Unbekannt"
        self.name_label = ctk.CTkLabel(self, text=f"{plugin['name']} ({version})", anchor="w")
        self.name_label.grid(row=0, column=0, padx=10, pady=6, sticky="ew")

        link_text = link if link else "Nicht gesetzt"
        self.link_label = ctk.CTkLabel(self, text=f"Link: {link_text}", anchor="w", text_color="gray")
        self.link_label.grid(row=0, column=1, padx=10, pady=6, sticky="ew")

        self.btn_link = ctk.CTkButton(self, text="Link", width=70, command=self.change_link)
        self.btn_link.grid(row=0, column=2, padx=5, pady=6)

        self.btn_find = ctk.CTkButton(self, text="Suchen", width=80, command=lambda: on_find_link(self))
        self.btn_find.grid(row=0, column=3, padx=5, pady=6)

        self.btn_update = ctk.CTkButton(
            self,
            text="Update",
            width=90,
            fg_color="green",
            hover_color="darkgreen",
            command=lambda: on_update(self),
        )
        self.btn_update.grid(row=0, column=4, padx=5, pady=6)

    def change_link(self):
        dialog = ctk.CTkInputDialog(text="Modrinth-Link eingeben:", title="Link hinzufügen")
        new_link = dialog.get_input()
        if new_link is None:
            return
        self.link = new_link.strip()
        self.set_link(self.link)
        self.on_link_change(self.plugin["filename"], self.link)

    def set_link(self, link: str):
        self.link = link.strip()
        self.link_label.configure(text=f"Link: {self.link if self.link else 'Nicht gesetzt'}")


class PluginUpdater(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1040x760")
        self.minsize(920, 660)
        ctk.set_appearance_mode("dark")

        self.client = ModrinthClient()
        self.config = self.load_config()
        self.root_folder = Path(self.config.get("root_folder") or app_dir()).resolve()
        self.links = self.config.get("links", {})
        self.appearance_mode = self.config.get("appearance_mode", "dark")
        ctk.set_appearance_mode(self.appearance_mode)

        self.set_window_icon()
        self.build_ui()
        self.refresh_list()

    @property
    def plugin_folder(self) -> Path:
        return self.root_folder / DEFAULT_PLUGIN_FOLDER

    def set_window_icon(self):
        icon = resource_path("logo.ico")
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except ctk.TclError:
                pass

    def build_ui(self):
        logo = resource_path("logo.png")
        if not logo.exists():
            logo = resource_path("logo.ico")
        if logo.exists():
            try:
                self.logo_img = ctk.CTkImage(
                    light_image=Image.open(logo),
                    dark_image=Image.open(logo),
                    size=(92, 92),
                )
                ctk.CTkLabel(self, image=self.logo_img, text="").pack(pady=(18, 4))
            except (OSError, ValueError):
                pass

        ctk.CTkLabel(self, text=APP_NAME, font=("Arial", 24, "bold")).pack(pady=(6, 8))

        folder_frame = ctk.CTkFrame(self)
        folder_frame.pack(fill="x", padx=20, pady=(4, 8))
        folder_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(folder_frame, text="Serverordner:", width=100, anchor="w").grid(row=0, column=0, padx=10, pady=10)
        self.folder_label = ctk.CTkLabel(folder_frame, text=str(self.root_folder), anchor="w")
        self.folder_label.grid(row=0, column=1, padx=10, pady=10, sticky="ew")
        ctk.CTkButton(folder_frame, text="Auswählen", width=110, command=self.choose_root_folder).grid(
            row=0, column=2, padx=10, pady=10
        )
        ctk.CTkButton(folder_frame, text="JARs importieren", width=130, command=self.import_jars).grid(
            row=0, column=3, padx=(0, 10), pady=10
        )

        modrinth_frame = ctk.CTkFrame(self)
        modrinth_frame.pack(fill="x", padx=20, pady=(0, 8))
        modrinth_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(modrinth_frame, text="Modrinth:", width=100, anchor="w").grid(row=0, column=0, padx=10, pady=10)
        self.modrinth_entry = ctk.CTkEntry(modrinth_frame, placeholder_text="Plugin suchen, z.B. LuckPerms")
        self.modrinth_entry.grid(row=0, column=1, padx=10, pady=10, sticky="ew")
        self.modrinth_entry.bind("<Return>", lambda _event: self.open_modrinth_search())
        ctk.CTkButton(modrinth_frame, text="Suchen & installieren", width=160, command=self.open_modrinth_search).grid(
            row=0, column=2, padx=10, pady=10
        )

        self.scroll_frame = ctk.CTkScrollableFrame(self, width=860, height=340)
        self.scroll_frame.pack(fill="both", expand=True, pady=8, padx=20)

        controls = ctk.CTkFrame(self)
        controls.pack(fill="x", padx=20, pady=(2, 8))
        ctk.CTkButton(controls, text="Liste aktualisieren", command=self.refresh_list).pack(side="left", padx=10, pady=10)
        ctk.CTkButton(controls, text="Alle prüfen", fg_color="green", hover_color="darkgreen", command=self.update_all).pack(
            side="left", padx=4, pady=10
        )
        ctk.CTkButton(controls, text="Links automatisch suchen", command=self.find_all_links).pack(
            side="left", padx=4, pady=10
        )
        self.theme_button = ctk.CTkButton(controls, text="White Mode", width=110, command=self.toggle_appearance_mode)
        self.theme_button.pack(side="right", padx=10, pady=10)
        if self.appearance_mode == "light":
            self.theme_button.configure(text="Dark Mode")

        progress_frame = ctk.CTkFrame(self)
        progress_frame.pack(fill="x", padx=20, pady=(0, 8))
        progress_frame.grid_columnconfigure(0, weight=1)
        self.progress_label = ctk.CTkLabel(progress_frame, text="Bereit", anchor="w")
        self.progress_label.grid(row=0, column=0, padx=10, pady=(8, 2), sticky="ew")
        self.progress_bar = ctk.CTkProgressBar(progress_frame)
        self.progress_bar.grid(row=1, column=0, padx=10, pady=(2, 10), sticky="ew")
        self.progress_bar.set(0)

        self.status_box = ctk.CTkTextbox(self, width=860, height=120)
        self.status_box.pack(fill="x", padx=20, pady=(0, 10))

        ctk.CTkLabel(self, text="Branded by SilasT29", font=("Arial", 12, "italic"), text_color="gray").pack(
            side="bottom", pady=8
        )

    def load_config(self):
        data = load_json(config_path(), {})
        if "links" not in data:
            data = {"root_folder": str(app_dir()), "links": data}
        return data

    def save_config(self):
        self.config = {
            "root_folder": str(self.root_folder),
            "links": self.links,
            "appearance_mode": self.appearance_mode,
        }
        save_json(config_path(), self.config)

    def log(self, text: str):
        self.status_box.insert("end", text + "\n")
        self.status_box.see("end")
        self.update_idletasks()

    def set_progress(self, value: float, text: str):
        self.progress_bar.set(max(0, min(1, value)))
        self.progress_label.configure(text=text)
        self.update_idletasks()

    def toggle_appearance_mode(self):
        self.appearance_mode = "light" if self.appearance_mode == "dark" else "dark"
        ctk.set_appearance_mode(self.appearance_mode)
        self.theme_button.configure(text="Dark Mode" if self.appearance_mode == "light" else "White Mode")
        self.save_config()

    def choose_root_folder(self):
        selected = filedialog.askdirectory(title="Serverordner auswählen", initialdir=str(self.root_folder))
        if not selected:
            return
        self.root_folder = Path(selected).resolve()
        self.folder_label.configure(text=str(self.root_folder))
        self.save_config()
        self.refresh_list()

    def import_jars(self):
        selected = filedialog.askdirectory(title="Ordner mit Plugin-JARs auswählen", initialdir=str(self.root_folder))
        if not selected:
            return
        source = Path(selected).resolve()
        self.plugin_folder.mkdir(parents=True, exist_ok=True)
        moved = 0
        for jar in source.glob("*.jar"):
            if jar.parent == self.plugin_folder:
                continue
            target = unique_path(self.plugin_folder / jar.name)
            shutil.move(str(jar), target)
            moved += 1
        self.log(f"{moved} Plugin-Datei(en) nach {self.plugin_folder} importiert.")
        self.refresh_list()

    def refresh_list(self):
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()

        self.plugin_folder.mkdir(parents=True, exist_ok=True)
        plugins = self.scan_plugins()
        if not plugins:
            ctk.CTkLabel(
                self.scroll_frame,
                text=f"Keine .jar-Dateien in {self.plugin_folder} gefunden.",
                text_color="gray",
            ).pack(pady=20)
            return

        for plugin in plugins:
            link = self.links.get(plugin["filename"], "")
            row = PluginRow(
                self.scroll_frame,
                plugin,
                link,
                self.save_plugin_link,
                self.update_single_plugin,
                self.find_link_for_row,
            )
            row.pack(fill="x", pady=3, padx=4)

    def scan_plugins(self) -> list[dict]:
        plugins = []
        for jar in sorted(self.plugin_folder.glob("*.jar"), key=lambda path: path.name.lower()):
            metadata = read_plugin_metadata(jar)
            plugins.append(
                {
                    "path": jar,
                    "filename": jar.name,
                    "name": metadata.get("name") or readable_name_from_filename(jar.name),
                    "version": metadata.get("version") or extract_version(jar.name),
                }
            )
        return plugins

    def save_plugin_link(self, filename: str, link: str):
        if link:
            self.links[filename] = link
        else:
            self.links.pop(filename, None)
        self.save_config()

    def update_all(self):
        rows = [child for child in self.scroll_frame.winfo_children() if isinstance(child, PluginRow)]
        if not rows:
            self.log("Keine Plugins zum Prüfen gefunden.")
            return
        self.set_progress(0, "Auto-Update gestartet...")
        for index, row in enumerate(rows, start=1):
            self.set_progress((index - 1) / len(rows), f"Prüfe {index}/{len(rows)}: {row.plugin['name']}")
            self.update_single_plugin(row, batch_index=index, batch_total=len(rows), refresh_after=False)
        self.refresh_list()
        self.set_progress(1, "Auto-Update fertig.")

    def update_single_plugin(self, row: PluginRow, batch_index: int = 1, batch_total: int = 1, refresh_after: bool = True):
        plugin = row.plugin
        self.log(f"Prüfe {plugin['name']}...")
        try:
            info = self.find_latest_version(plugin["name"], row.link)
            if not info:
                self.log("  -> Keine Version gefunden. Bitte Modrinth-Link manuell setzen.")
                return
            if info.get("project_url") and not row.link:
                row.set_link(info["project_url"])
                self.save_plugin_link(plugin["filename"], info["project_url"])
                self.log(f"  -> Modrinth-Link gespeichert: {info['project_url']}")

            local_version = plugin["version"] or extract_version(plugin["filename"])
            if version_key(info["version"]) <= version_key(local_version):
                self.log(f"  -> Bereits aktuell ({info['version']}).")
                return

            self.log(f"  -> Update gefunden: {local_version or 'unbekannt'} -> {info['version']}")
            self.download_update(plugin["path"], info, batch_index, batch_total)
            if refresh_after:
                self.refresh_list()
                self.set_progress(1, "Update fertig.")
        except requests.RequestException as error:
            self.log(f"  -> Netzwerkfehler: {error}")
        except ValueError as error:
            self.log(f"  -> {error}")
        except OSError as error:
            self.log(f"  -> Dateifehler: {error}")

    def find_latest_version(self, plugin_name: str, link: str) -> dict | None:
        if link:
            slug = modrinth_slug_from_link(link)
            if not slug:
                raise ValueError("Der Link ist kein gültiger Modrinth-Plugin-Link.")
            return self.client.latest_release_by_slug(slug)
        info = self.client.latest_release_by_name(plugin_name)
        return info

    def download_update(self, old_path: Path, info: dict, batch_index: int = 1, batch_total: int = 1):
        if not info.get("url"):
            self.log("  -> Update hat keine Download-URL.")
            return

        self.plugin_folder.mkdir(parents=True, exist_ok=True)
        new_path = unique_path(self.plugin_folder / info["filename"])
        backup_path = unique_path(old_path.with_suffix(old_path.suffix + ".bak"))

        shutil.move(str(old_path), backup_path)
        try:
            self.client.download(
                info["url"],
                new_path,
                lambda done, total: self.update_download_progress(
                    done,
                    total or info.get("size") or 0,
                    info["filename"],
                    batch_index,
                    batch_total,
                ),
            )
        except Exception:
            if new_path.exists():
                new_path.unlink()
            shutil.move(str(backup_path), old_path)
            raise

        self.links[new_path.name] = self.links.pop(old_path.name, "")
        if info.get("project_url"):
            self.links[new_path.name] = info["project_url"]
        self.save_config()
        self.log(f"  [OK] Aktualisiert: {new_path.name}")
        self.log(f"  Backup: {backup_path.name}")

    def update_download_progress(self, done: int, total: int, filename: str, batch_index: int, batch_total: int):
        file_fraction = done / total if total else 0
        overall = ((batch_index - 1) + file_fraction) / batch_total
        if total:
            text = f"Lade {filename}: {done // 1024} KB / {total // 1024} KB"
        else:
            text = f"Lade {filename}: {done // 1024} KB"
        self.set_progress(overall, text)

    def find_link_for_row(self, row: PluginRow):
        self.log(f"Suche Modrinth-Link für {row.plugin['name']}...")
        try:
            hits = self.client.search_projects(row.plugin["name"], limit=1)
            if not hits:
                self.log("  -> Kein Modrinth-Projekt gefunden.")
                return
            hit = hits[0]
            link = modrinth_url(hit.get("slug") or hit["project_id"])
            row.set_link(link)
            self.save_plugin_link(row.plugin["filename"], link)
            self.log(f"  -> Gefunden: {hit.get('title', link)}")
        except requests.RequestException as error:
            self.log(f"  -> Netzwerkfehler: {error}")

    def find_all_links(self):
        rows = [child for child in self.scroll_frame.winfo_children() if isinstance(child, PluginRow)]
        if not rows:
            self.log("Keine Plugins zum Suchen gefunden.")
            return
        self.set_progress(0, "Suche Modrinth-Links...")
        for index, row in enumerate(rows, start=1):
            self.set_progress(index / len(rows), f"Suche Link {index}/{len(rows)}: {row.plugin['name']}")
            if not row.link:
                self.find_link_for_row(row)
        self.set_progress(1, "Link-Suche fertig.")

    def open_modrinth_search(self):
        query = self.modrinth_entry.get().strip()
        if not query:
            self.log("Bitte zuerst einen Suchbegriff für Modrinth eingeben.")
            return
        self.log(f"Suche Modrinth-Projekte für '{query}'...")
        try:
            hits = self.client.search_projects(query, limit=10)
        except requests.RequestException as error:
            self.log(f"  -> Netzwerkfehler: {error}")
            return
        if not hits:
            self.log("  -> Keine passenden Modrinth-Projekte gefunden.")
            return
        self.show_modrinth_results(query, hits)

    def show_modrinth_results(self, query: str, hits: list[dict]):
        window = ctk.CTkToplevel(self)
        window.title(f"Modrinth installieren: {query}")
        window.geometry("760x520")
        window.minsize(680, 420)
        window.transient(self)

        ctk.CTkLabel(window, text="Modrinth-Projekt auswählen", font=("Arial", 18, "bold")).pack(pady=(16, 8))
        results = ctk.CTkScrollableFrame(window, width=720, height=420)
        results.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        for hit in hits:
            frame = ctk.CTkFrame(results)
            frame.pack(fill="x", padx=6, pady=6)
            frame.grid_columnconfigure(0, weight=1)
            title = hit.get("title") or hit.get("slug") or hit["project_id"]
            downloads = hit.get("downloads", 0)
            description = hit.get("description") or "Keine Beschreibung"
            ctk.CTkLabel(frame, text=f"{title}  •  {downloads} Downloads", anchor="w", font=("Arial", 14, "bold")).grid(
                row=0, column=0, padx=10, pady=(8, 2), sticky="ew"
            )
            ctk.CTkLabel(frame, text=description[:180], anchor="w", text_color="gray").grid(
                row=1, column=0, padx=10, pady=(0, 8), sticky="ew"
            )
            ctk.CTkButton(frame, text="Installieren", width=120, command=lambda item=hit, win=window: self.install_modrinth_project(item, win)).grid(
                row=0, column=1, rowspan=2, padx=10, pady=10
            )

    def install_modrinth_project(self, hit: dict, window=None):
        title = hit.get("title") or hit.get("slug") or hit["project_id"]
        self.log(f"Installiere {title} von Modrinth...")
        try:
            info = self.client.latest_release_by_project(hit["project_id"])
            if not info:
                self.log("  -> Keine Release-Datei für dieses Projekt gefunden.")
                return
            info["project_slug"] = hit.get("slug")
            info["project_title"] = title
            info["project_url"] = modrinth_url(hit.get("slug") or hit["project_id"])
            self.download_new_plugin(info)
            if window:
                window.destroy()
            self.refresh_list()
            self.set_progress(1, "Installation fertig.")
        except requests.RequestException as error:
            self.log(f"  -> Netzwerkfehler: {error}")
        except OSError as error:
            self.log(f"  -> Dateifehler: {error}")

    def download_new_plugin(self, info: dict):
        if not info.get("url"):
            self.log("  -> Download hat keine URL.")
            return
        self.plugin_folder.mkdir(parents=True, exist_ok=True)
        target = unique_path(self.plugin_folder / info["filename"])
        self.client.download(
            info["url"],
            target,
            lambda done, total: self.update_download_progress(done, total or info.get("size") or 0, info["filename"], 1, 1),
        )
        if info.get("project_url"):
            self.links[target.name] = info["project_url"]
            self.save_config()
        self.log(f"  [OK] Installiert: {target.name}")


def readable_name_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"[-_\s]v?\d+(?:[.\w+-]*\d|[.\w+-]*)$", "", stem, flags=re.IGNORECASE)
    return stem.replace("_", " ").replace("-", " ").strip() or filename


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 10_000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Kein freier Dateiname für {path}")


def main():
    try:
        app = PluginUpdater()
        app.mainloop()
    except Exception as error:
        messagebox.showerror(APP_NAME, f"Unerwarteter Fehler:\n{error}")
        raise


if __name__ == "__main__":
    main()
