# Deploying on a Linux server with an SMB share

Setup: images live on `\\vega\media\Pictures\gallery`, gallery runs on a Linux server (Docker).

## 1. Copy the project to the server

```bash
scp -r imageslucya/ user@prod:/opt/
ssh user@prod
cd /opt/imageslucya
```

## 2. Mount the SMB share on the Linux host

Install packages:

```bash
sudo apt update
sudo apt install -y cifs-utils
```

Credentials file (if the share needs auth):

```bash
sudo install -m 600 /dev/null /etc/samba/vega-credentials
sudo tee /etc/samba/vega-credentials >/dev/null <<'EOF'
username=YOUR_USER
password=YOUR_PASS
domain=WORKGROUP
EOF
```

Mountpoint:

```bash
sudo mkdir -p /mnt/vega-gallery
```

Add a permanent entry to `/etc/fstab` so the mount survives reboots:

```bash
sudo tee -a /etc/fstab <<'EOF'
//vega/media/Pictures/gallery  /mnt/vega-gallery  cifs  credentials=/etc/samba/vega-credentials,uid=1000,gid=1000,iocharset=utf8,vers=3.0,ro,_netdev,nofail  0  0
EOF
```

Notes:
- `ro` (read-only) — the gallery never writes into the photos folder
- `_netdev,nofail` — if vega is unreachable, the server still boots
- `uid=1000,gid=1000` — adjust to your user; run `id` to check

Mount it:

```bash
sudo mount -a
ls /mnt/vega-gallery   # should list the album folders
```

If the share is guest-accessible, the fstab line simplifies to:

```
//vega/media/Pictures/gallery  /mnt/vega-gallery  cifs  guest,uid=1000,gid=1000,iocharset=utf8,vers=3.0,ro,_netdev,nofail  0  0
```

## 3. Configure `.env`

```bash
cp .env.example .env
nano .env
```

Minimum:

```env
PHOTOS_PATH=/mnt/vega-gallery
THUMBS_PATH=./thumbnails
PREVIEWS_PATH=./previews
DATA_PATH=./data
PORT=8000
SCAN_INTERVAL=300
ENABLE_WATCHER=0
```

Why `ENABLE_WATCHER=0` and `SCAN_INTERVAL=300`:

> `inotify` (the Linux kernel file-event API) **does not reliably fire over SMB/CIFS** — new images often don't trigger events. So we disable the watcher and rescan every 5 minutes instead.

## 4. Start

```bash
docker compose up -d --build
docker compose logs -f
```

Reachable at: `http://prod-server:8000`

## 5. Updates

```bash
git pull   # or rsync/scp
docker compose up -d --build
```

Thumbnails, previews, and the DB persist in `./thumbnails`, `./previews`, and `./data`.

## Troubleshooting

**Container sees `/photos` empty:**
- Check on the host: `ls /mnt/vega-gallery` — must show the album folders
- If empty: SMB mount broken → `sudo mount -a` and `dmesg | tail`

**New images not appearing:**
- With `SCAN_INTERVAL=300` it takes up to 5 minutes.

**Permissions:**
- The container runs as `root` so it can read everything. The `uid` option in the mount only affects how files appear to the host user — irrelevant for the container as long as the mount is readable.

**SMB performance:**
- The first scan over the share takes a while (parsing EXIF requires reading the image header). After that the SQLite cache + local thumbnails make everything fast; only previews/originals are fetched over SMB on demand.
