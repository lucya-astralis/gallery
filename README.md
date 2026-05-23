# imageslucya

Eine schlanke, read-only Web-Bildergalerie mit ordnerbasierten Alben, EXIF-Anzeige, Tags via Sidecar-Dateien und automatischer Thumbnail-Erstellung. Deployment per Docker. Safe für öffentliches Hosting hinter Cloudflare.

## Features

- **Ordner = Alben:** Jeder Unterordner in `photos/` ist automatisch ein Album. Bild reinkopieren → es erscheint im Album.
- **Indexierung läuft komplett automatisch:** Watcher (lokal) und/oder periodischer Rescan (für SMB/NFS). Kein manueller Button im Web-UI.
- **Thumbnails:** Vorschaubilder (max. 480 px, JPEG q82) werden on-demand und im Hintergrund erstellt — die Galerie lädt schnell, auch bei großen Originalen.
- **EXIF:** Kamera, Objektiv, Belichtung, ISO, Brennweite, … im Bilddetail. GPS-Daten werden per Default ausgeblendet (Privacy).
- **Tags via Sidecar:** Lege neben einem Bild eine `.tags`-Datei mit kommagetrennten Tags an, z.B. `IMG_0001.jpg.tags` mit Inhalt `urlaub, strand, sunset`. Filterung per Klick im Album.
- **Suche:** Top-Bar durchsucht Album-, Datei- und Tagnamen.
- **Mobile-friendly:** Responsives Grid, große Touch-Targets, Tastatur-Navigation (← → ESC) am Desktop.
- **Dark/Light:** Folgt dem System-Theme.
- **Read-only:** Keine Schreib-Endpunkte. Die App schreibt nie in den `photos/`-Ordner (`:ro` Mount). Damit gibt es keine Angriffsfläche für Upload-/Manipulation-Exploits.

## Schnellstart

```bash
cp .env.example .env   # Pfade & Optionen anpassen
docker compose up -d --build
```

Galerie öffnen: <http://localhost:8000>

Bilder hinzufügen:

```
photos/
├── urlaub-2025/
│   ├── DSC_0001.jpg
│   ├── DSC_0001.jpg.tags     # optional: "strand, italien"
│   └── DSC_0002.jpg
├── familie/
│   └── …
└── städtetrip-rom/
    └── …
```

Jeder Unterordner ist ein Album. Unterstützt: JPG/JPEG, PNG, WebP, GIF, BMP, TIFF, HEIC*.

(*HEIC erfordert ggf. zusätzliche Pillow-Plugins.)

Für Linux-Server-Deployment mit SMB-Share siehe [DEPLOY-LINUX.md](DEPLOY-LINUX.md).

## Tags

Tags werden über Sidecar-Dateien im Filesystem verwaltet — passt zum Rest des Workflows:

```bash
# Neben dem Bild eine .tags-Datei anlegen
echo "urlaub, italien, strand" > photos/urlaub-2025/DSC_0001.jpg.tags
```

Der Scanner liest die Datei beim nächsten Indexierungslauf und verknüpft die Tags. Datei leeren oder löschen → Tags weg. Watcher reagiert live auf Änderungen, periodischer Scan greift sie spätestens beim nächsten Lauf auf.

## Ordnerstruktur

| Pfad           | Zweck                                                    |
|----------------|----------------------------------------------------------|
| `photos/`      | Deine Originale + `.tags`-Sidecars (read-only gemountet) |
| `thumbnails/`  | Generierte Vorschaubilder (Cache, kann jederzeit weg)    |
| `data/`        | SQLite-DB mit EXIF-Cache und Tag-Index                   |

## Konfiguration

| Variable        | Default       | Bedeutung                                                  |
|-----------------|---------------|------------------------------------------------------------|
| `PHOTOS_DIR`    | `/photos`     | Wo die Original-Ordner liegen                              |
| `THUMBS_DIR`    | `/thumbnails` | Wo Thumbnails gespeichert werden                           |
| `DATA_DIR`      | `/data`       | SQLite-Datenbank                                           |
| `THUMB_SIZE`    | `480`         | Maximalkante der Thumbnails in Pixeln                      |
| `SCAN_INTERVAL` | `0`           | Periodischer Rescan in Sekunden (0 = aus). Auf SMB ~300.   |
| `ENABLE_WATCHER`| `1`           | inotify-Watcher (auf SMB/NFS sinnvollerweise `0`)          |
| `HIDE_GPS`      | `1`           | GPS aus EXIF-Anzeige entfernen                             |

## Sicherheit / Hosting

Die App ist **komplett read-only** ausgelegt:

- Keine Schreib-API, kein Upload, kein Tag-Edit im Web
- `photos/` ist als `:ro` gemountet — selbst wenn jemand einen Code-Bug findet, kann er nicht in die Originale schreiben
- Tags und Thumbnails leben in `data/` bzw. `thumbnails/` — nichts davon ist sicherheitskritisch
- Path-Traversal blockiert (`_safe_rel`)
- GPS-Stripping aktiv (`HIDE_GPS=1`)

Für public Hosting empfehlenswert (über Cloudflare):
- **Bot Fight Mode** an
- **Rate Limiting** auf `/full/*` falls du Bandbreite begrenzen willst (Originale können groß sein)
- **Cache Rules** für `/thumb/*` und `/static/*` (lange TTL — die Dateien sind unveränderlich pro URL)

## Endpunkte

Alle GET, alle public:

- `GET /` — Albenübersicht
- `GET /album/{album}` — Bilder eines Albums (optional `?tag=foo`)
- `GET /image/{album}/{datei}` — Detailansicht
- `GET /thumb/{album}/{datei}` — Thumbnail (lazy generiert)
- `GET /full/{album}/{datei}` — Originaldatei
- `GET /search?q=…` — Suche

## Lokal entwickeln (ohne Docker)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
mkdir -p photos thumbnails data
uvicorn app.main:app --reload
```

## Hinweise

- Erster Scan auf großer Bibliothek dauert (EXIF + Thumbs). Danach Cache.
- Bilder löschen: aus `photos/` entfernen — Watcher/Scan räumen DB-Eintrag und Thumbnail auf.
- Tags umbenennen: alte `.tags`-Datei ändern, fertig.
- Thumbnails/DB können jederzeit weggeworfen werden, werden beim nächsten Scan neu erzeugt.
