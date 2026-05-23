# imageslucya

Eine schlanke, mobile-freundliche Web-Bildergalerie mit ordnerbasierten Alben, EXIF-Anzeige, Tags und automatischer Thumbnail-Erstellung. Deployment per Docker.

## Features

- **Ordner = Alben:** Jeder Unterordner in `photos/` ist automatisch ein Album. Bild reinkopieren → es erscheint im Album.
- **Auto- & Manueller Scan:** Ein Datei-Watcher indexiert neue Bilder live. Zusätzlich kannst du oben rechts den **Rescan**-Button drücken.
- **Thumbnails:** Vorschaubilder (max. 480 px, JPEG q82) werden on-demand und im Hintergrund erstellt — die Galerie lädt deshalb schnell, auch bei großen Original-Dateien.
- **EXIF:** Kamera, Objektiv, Belichtung, ISO, Brennweite, GPS, … direkt im Bilddetail. Plus „Alle EXIF-Daten" als JSON.
- **Tags:** Pro Bild Tags vergeben (Komma-getrennt), per Klick auf einen Tag im Album danach filtern.
- **Suche:** Top-Bar durchsucht Album-, Datei- und Tagnamen.
- **Mobile-friendly:** Responsives Grid, große Touch-Targets, Tastatur-Navigation (← → ESC) am Desktop.
- **Dark/Light:** Folgt dem System-Theme.

## Schnellstart

```bash
cp .env.example .env   # Pfade anpassen, optional
docker compose up -d --build
```

Für Deployment auf einem Linux-Server mit Bildern auf einer SMB-Share siehe [DEPLOY-LINUX.md](DEPLOY-LINUX.md).

Galerie öffnen: <http://localhost:8000>

Bilder hinzufügen:

```
photos/
├── urlaub-2025/
│   ├── DSC_0001.jpg
│   └── DSC_0002.jpg
├── familie/
│   └── …
└── städtetrip-rom/
    └── …
```

Jeder Unterordner ist ein Album. Unterstützt: JPG/JPEG, PNG, WebP, GIF, BMP, TIFF, HEIC*.

(*HEIC erfordert ggf. zusätzliche Pillow-Plugins — JPEG/PNG/WebP funktionieren out of the box.)

## Ordnerstruktur

| Pfad           | Zweck                                                    |
|----------------|----------------------------------------------------------|
| `photos/`      | Deine Originale (gemountet ins Container-`/photos`)      |
| `thumbnails/`  | Generierte Vorschaubilder (Cache, kann jederzeit weg)    |
| `data/`        | SQLite-DB mit EXIF-Cache und Tags                        |

## Konfiguration

Per Umgebungsvariablen (`docker-compose.yml` oder `.env`):

| Variable        | Default       | Bedeutung                                                  |
|-----------------|---------------|------------------------------------------------------------|
| `PHOTOS_DIR`    | `/photos`     | Wo die Original-Ordner liegen                              |
| `THUMBS_DIR`    | `/thumbnails` | Wo Thumbnails gespeichert werden                           |
| `DATA_DIR`      | `/data`       | SQLite-Datenbank                                           |
| `THUMB_SIZE`    | `480`         | Maximalkante der Thumbnails in Pixeln                      |
| `SCAN_INTERVAL` | `0`           | Periodischer Rescan in Sekunden (0 = aus)                  |
| `ENABLE_WATCHER`| `1`           | inotify-Watcher aktiv (auf SMB/NFS am besten `0`)          |
| `ADMIN_TOKEN`   | _(leer)_      | Token f�r Schreib-Endpunkte. Leer = Endpunkte deaktiviert. |
| `HIDE_GPS`      | `1`           | GPS aus EXIF-Anzeige entfernen                             |

## Sicherheit f�r �ffentliches Hosting

Diese Galerie ist als **Read-only-Public + Auth-protected-Admin** Modell ausgelegt:

- **�ffentlich (ohne Auth):** Albenliste, Bilder anschauen, Suche, EXIF lesen
- **Admin-only (mit `ADMIN_TOKEN`):** Rescan ausl�sen, Tags editieren

**Vor dem Public-Stellen unbedingt:**

1. **`ADMIN_TOKEN` setzen** � z.B. mit `openssl rand -hex 32` einen starken Token erzeugen.
   Wenn die Variable leer bleibt, sind Rescan und Tag-Edit komplett deaktiviert (sicher, aber dann nur noch Read-only).
2. **`HIDE_GPS=1`** lassen, sofern du nicht m�chtest, dass Besucher die Aufnahmeorte deiner Bilder sehen.
3. **Hinter Cloudflare** � gut. Zus�tzlich empfehlenswert:
   - **Cloudflare Access** vor `/api/scan` und `/api/image/.../tags` (Header/Cookie-basierte Auth, st�rker als der eingebaute Token)
   - **Cloudflare Rate Limiting** auf `/api/*` (z.B. 30 Requests/Minute)
   - **Bot Fight Mode** aktivieren
4. **Bilder-Mount auf `:ro`** � die App schreibt nie in den Photos-Ordner. Bereits in `docker-compose.yml` so eingestellt.
5. **Pfad-Traversal** ist durch `_safe_rel()` blockiert; nur `photos/<album>/<datei>` ist erreichbar.

**Was bewusst NICHT eingebaut ist:**

- Kein Upload-Endpunkt � Bilder werden nur per Filesystem hinzugef�gt. Damit gibt es keinen Weg, �ber das Web Schadcode/Bilder einzuschleusen.
- Keine Cookie-Sessions � der Admin-Token wird im `sessionStorage` des Browsers gehalten und per `X-Admin-Token`-Header gesendet (kein CSRF-Risiko).
- Kein Rate-Limiting in der App � daf�r ist Cloudflare zust�ndig.

## Lokal entwickeln (ohne Docker)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
mkdir -p photos thumbnails data
uvicorn app.main:app --reload
```

## Endpunkte

- `GET /` — Albenübersicht
- `GET /album/{album}` — Bilder eines Albums (optional `?tag=foo`)
- `GET /image/{album}/{datei}` — Detailansicht
- `GET /thumb/{album}/{datei}` — Thumbnail (lazy generiert)
- `GET /full/{album}/{datei}` — Originaldatei
- `GET /search?q=…` — Suche
- `POST /api/scan` — Manueller Rescan
- `POST /api/image/{album}/{datei}/tags` — Tags setzen (Formularfeld `tags`, Komma-getrennt)

## Hinweise

- Der erste Scan auf vielen Bildern kann etwas dauern (EXIF + Thumbnails). Danach bleibt es schnell, weil alles im Cache liegt.
- Bilder löschen: einfach aus dem `photos/`-Ordner entfernen — der Watcher räumt DB-Eintrag und Thumbnail auf.
- Tags sind unabhängig von der Datei (in der DB gespeichert). Wenn du ein Bild umbenennst, gehen seine Tags verloren — Thumbnails und EXIF werden aber automatisch neu erzeugt.
