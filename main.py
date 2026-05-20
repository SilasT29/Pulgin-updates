import os
import zipfile
import requests
import customtkinter as ctk
from PIL import Image
import re
import json

# --- KONFIGURATION ---
API_BASE_URL = "https://api.modrinth.com/v2"
PLUGIN_FOLDER = "plugins"
LOGO_PATH = "logo.png"
CONFIG_FILE = "plugin_config.json"
USER_AGENT = "PluginUpdaterBySilasT29/3.0 (Contact: SilasT29)"
HEADERS = {"User-Agent": USER_AGENT}

class PluginRow(ctk.CTkFrame):
    """ Ein einzelner Eintrag in der Plugin-Liste """
    def __init__(self, master, plugin_name, filename, current_version, link, update_callback):
        super().__init__(master)
        self.plugin_name = plugin_name
        self.filename = filename
        self.link = link

        # Layout
        self.grid_columnconfigure(1, weight=1)

        self.name_label = ctk.CTkLabel(self, text=f"{plugin_name} ({current_version})", width=300, anchor="w")
        self.name_label.grid(row=0, column=0, padx=10, pady=5)

        self.link_label = ctk.CTkLabel(self, text=f"Link: {link if link else 'Nicht gesetzt'}", font=("Arial", 10), text_color="gray")
        self.link_label.grid(row=0, column=1, padx=10, pady=5)

        self.btn_link = ctk.CTkButton(self, text="Link", width=60, command=self.change_link)
        self.btn_link.grid(row=0, column=2, padx=5, pady=5)

        self.btn_update = ctk.CTkButton(self, text="Update", width=80, fg_color="green", hover_color="darkgreen", 
                                        command=lambda: update_callback(self))
        self.btn_update.grid(row=0, column=3, padx=5, pady=5)

    def change_link(self):
        dialog = ctk.CTkInputDialog(text="Modrinth Link eingeben:", title="Link hinzufügen")
        new_link = dialog.get_input()
        if new_link:
            self.link = new_link
            self.link_label.configure(text=f"Link: {new_link}")
            # Speichern in Config
            app.save_config()

class PluginUpdater(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Plugin Updater")
        self.geometry("850x600")
        ctk.set_appearance_mode("dark")

        # Taskleisten Icon setzen
        try:
            self.iconbitmap("logo.ico") # Benötigt logo.ico im Ordner
        except:
            pass

        self.config = self.load_config()

        # --- UI LAYOUT ---
        try:
            self.logo_img = ctk.CTkImage(light_image=Image.open(LOGO_PATH), 
                                        dark_image=Image.open(LOGO_PATH), 
                                        size=(100, 100))
            self.logo_label = ctk.CTkLabel(self, image=self.logo_img, text="")
            self.logo_label.pack(pady=(20, 5))
        except: pass

        self.label = ctk.CTkLabel(self, text="Plugin Updater", font=("Arial", 24, "bold"))
        self.label.pack(pady=10)

        # Scrollbare Liste für Plugins
        self.scroll_frame = ctk.CTkScrollableFrame(self, width=800, height=350)
        self.scroll_frame.pack(pady=10, padx=20)

        self.status_box = ctk.CTkTextbox(self, width=750, height=100)
        self.status_box.pack(pady=10)

        self.btn_refresh = ctk.CTkButton(self, text="Liste aktualisieren", command=self.refresh_list)
        self.btn_refresh.pack(pady=10)

        self.branding_label = ctk.CTkLabel(self, text="Branded by SilasT29", 
                                           font=("Arial", 12, "italic"), text_color="gray")
        self.branding_label.pack(side="bottom", pady=10)

        self.refresh_list()

    def log(self, text):
        self.status_box.insert("end", text + "\n")
        self.status_box.see("end")

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        return {}

    def save_config(self):
        # Alle aktuellen Links aus der UI in die config.json schreiben
        new_config = {}
        for row in self.scroll_frame.winfo_children():
            if isinstance(row, PluginRow):
                new_config[row.filename] = row.link
        with open(CONFIG_FILE, "w") as f:
            json.dump(new_config, f, indent=4)

    def refresh_list(self):
        # Alte Liste löschen
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()

        if not os.path.exists(PLUGIN_FOLDER):
            os.makedirs(PLUGIN_FOLDER)

        jars = [f for f in os.listdir(PLUGIN_FOLDER) if f.endswith(".jar")]
        for jar in jars:
            path = os.path.join(PLUGIN_FOLDER, jar)
            name = self.get_plugin_name(path) or jar
            ver = self.extract_version(jar) or "Unbekannt"
            link = self.config.get(jar, "")
            
            row = PluginRow(self.scroll_frame, name, jar, ver, link, self.update_single_plugin)
            row.pack(fill="x", pady=2)

    def get_plugin_name(self, jar_path):
        try:
            with zipfile.ZipFile(jar_path, 'r') as zip_ref:
                for file in zip_ref.namelist():
                    if file in ["plugin.yml", "paper-plugin.yml"]:
                        with zip_ref.open(file) as f:
                            content = f.read().decode('utf-8', 'replace')
                            for line in content.splitlines():
                                if line.startswith("name:"):
                                    return line.replace("name:", "").strip()
        except: pass
        return None

    def extract_version(self, filename):
        match = re.search(r"(?:[-_\s]|(?:\sv))(\d[\w.+-]*)$", filename.replace(".jar", ""), re.IGNORECASE)
        return match.group(1) if match else None

    def version_key(self, value):
        if not value: return tuple()
        tokens = re.findall(r"\d+|[A-Za-z]+", value.lower())
        key = []
        weights = {"snapshot": -3, "alpha": -2, "beta": -1, "rc": 0}
        for token in tokens:
            if token.isdigit(): key.append((1, int(token)))
            else: key.append((0, weights.get(token, 1), token))
        return tuple(key)

    def get_version_from_link(self, link):
        try:
            # Extrahiere Slug aus Link (z.B. 'worldedit' aus 'https://modrinth.com/plugin/worldedit')
            match = re.search(r"modrinth\.com/plugin/([^/?#]+)", link)
            if not match: return None
            slug = match.group(1)
            
            version_url = f"{API_BASE_URL}/project/{slug}/version"
            v_res = requests.get(version_url, headers=HEADERS).json()
            
            for version in v_res:
                if version.get('version_type') == 'release' and version['resources']:
                    res = version['resources'][0]
                    return {"version": version['version_number'], "url": res['url'], "filename": res['filename']}
        except: pass
        return None

    def update_single_plugin(self, row):
        self.log(f"Prüfe {row.plugin_name}...")
        
        # 1. Priorität: Manueller Link
        if row.link:
            info = self.get_version_from_link(row.link)
        else:
            # 2. Priorität: Automatische Suche
            info = self.get_latest_version_info(row.plugin_name)

        if info:
            local_ver = self.extract_version(row.filename)
            if self.version_key(info['version']) > self.version_key(local_ver):
                self.log(f"  -> Update gefunden: {info['version']}")
                self.download_update(os.path.join(PLUGIN_FOLDER, row.filename), info)
            else:
                self.log(f"  -> Bereits aktuell ({info['version']})")
        else:
            self.log(f"  -> Keine Versionen gefunden. Bitte Link manuell hinzufügen!")

    def get_latest_version_info(self, plugin_name):
        try:
            search_url = f"{API_BASE_URL}/search?query={plugin_name}"
            res = requests.get(search_url, headers=HEADERS).json()
            if not res or 'hits' not in res or not res['hits']: return None
            project_id = res['hits'][0]['project_id']
            version_url = f"{API_BASE_URL}/project/{project_id}/version"
            v_res = requests.get(version_url, headers=HEADERS).json()
            for version in v_res:
                if version.get('version_type') == 'release' and version['resources']:
                    res = version['resources'][0]
                    return {"version": version['version_number'], "url": res['url'], "filename": res['filename']}
        except: pass
        return None

    def download_update(self, old_path, info):
        try:
            backup_path = old_path + ".bak"
            if os.path.exists(backup_path): os.remove(backup_path)
            os.rename(old_path, backup_path)
            r = requests.get(info['url'], headers=HEADERS)
            with open(os.path.join(PLUGIN_FOLDER, info['filename']), 'wb') as f:
                f.write(r.content)
            self.log(f"  [OK] Aktualisiert!")
            self.refresh_list()
        except Exception as e:
            self.log(f"  [Fehler] {e}")

if __name__ == "__main__":
    app = PluginUpdater()
    app.mainloop()
