# Deployment auf Linux-Server mit SMB-Share

Setup: Bilder liegen auf `\\vega\media\Pictures\gallery`, Galerie läuft auf einem Linux-Server (Docker).

## 1. Projekt auf den Server kopieren

```bash
# z.B. per scp, git, oder rsync
scp -r imageslucya/ user@prod:/opt/
ssh user@prod
cd /opt/imageslucya
```

## 2. SMB-Share auf dem Linux-Host mounten

Pakete installieren:

```bash
sudo apt update
sudo apt install -y cifs-utils
```

Credentials-Datei (falls die Share Auth braucht):

```bash
sudo install -m 600 /dev/null /etc/samba/vega-credentials
sudo tee /etc/samba/vega-credentials >/dev/null <<'EOF'
username=DEIN_USER
password=DEIN_PASS
domain=WORKGROUP
EOF
```

Mountpoint anlegen:

```bash
sudo mkdir -p /mnt/vega-gallery
```

In `/etc/fstab` permanent eintragen, damit der Mount auch nach Reboot da ist:

```bash
sudo tee -a /etc/fstab <<'EOF'
//vega/media/Pictures/gallery  /mnt/vega-gallery  cifs  credentials=/etc/samba/vega-credentials,uid=1000,gid=1000,iocharset=utf8,vers=3.0,ro,_netdev,nofail  0  0
EOF
```

Wichtig:
- `ro` (read-only) — die Galerie schreibt nie in den Bilder-Ordner
- `_netdev,nofail` — falls vega nicht erreichbar ist, bootet der Server trotzdem
- `uid=1000,gid=1000` — passt auf deinen User, falls anders: `id` ausführen und anpassen

Mounten:

```bash
sudo mount -a
ls /mnt/vega-gallery   # sollte die Album-Ordner zeigen
```

Falls die Share ohne Auth geht, vereinfacht sich der fstab-Eintrag zu:

```
//vega/media/Pictures/gallery  /mnt/vega-gallery  cifs  guest,uid=1000,gid=1000,iocharset=utf8,vers=3.0,ro,_netdev,nofail  0  0
```

## 3. `.env` konfigurieren

```bash
cp .env.example .env
nano .env
```

Mindestens das hier:

```env
PHOTOS_PATH=/mnt/vega-gallery
THUMBS_PATH=./thumbnails
DATA_PATH=./data
PORT=8000
SCAN_INTERVAL=300
ENABLE_WATCHER=0
```

Warum `ENABLE_WATCHER=0` und `SCAN_INTERVAL=300`:

> `inotify` (die Linux-Kernel-API für Datei-Events) **funktioniert über SMB/CIFS nicht zuverlässig** — neue Bilder lösen oft keine Events aus. Deshalb deaktivieren wir den Watcher und scannen stattdessen alle 5 Minuten neu. Den manuellen Rescan-Button gibt es weiterhin.

## 4. Starten

```bash
docker compose up -d --build
docker compose logs -f
```

Galerie erreichbar unter: `http://prod-server:8000`

## 5. Updates

```bash
git pull   # oder rsync/scp
docker compose up -d --build
```

Thumbnails und DB bleiben in `./thumbnails` und `./data` erhalten.

## Troubleshooting

**Container sieht `/photos` leer:**
- Prüfen: `ls /mnt/vega-gallery` auf dem Host — muss die Album-Ordner zeigen
- Falls leer: SMB-Mount kaputt → `sudo mount -a` und `dmesg | tail` checken

**Neue Bilder werden nicht gefunden:**
- Mit `SCAN_INTERVAL=300` dauert es bis zu 5 Min. Sofort über den ↻ Rescan-Button auslösen.

**Permissions:**
- Container läuft als `root`, kann also alles lesen. Die `uid`-Option im Mount betrifft nur, wie Dateien dem Host-Benutzer angezeigt werden — für den Container irrelevant, solange der Mount lesbar ist.

**SMB-Performance:**
- Erster Scan über die Share kann dauern (EXIF parsen erfordert das Lesen des Bild-Headers). Danach ist alles im Cache (SQLite), nur Thumbnails werden bei Bedarf nachgezogen.
