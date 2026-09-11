# Deploying on a Linux server with an SMB share

Setup: images live on `\\vega\media\Pictures\gallery`, Aperture runs on a Linux
server in one Docker container — the public gallery on `:8000`, the operator
console on `:8090`.

## 1. Copy the project to the server

```bash
scp -r gallery/ user@prod:/opt/aperture
ssh user@prod
cd /opt/aperture
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
//vega/media/Pictures/gallery  /mnt/vega-gallery  cifs  credentials=/etc/samba/vega-credentials,uid=10001,gid=10001,file_mode=0664,dir_mode=0775,iocharset=utf8,vers=3.0,rw,_netdev,nofail  0  0
EOF
```

Notes:
- **`uid=10001,gid=10001`** — a CIFS mount has no real file owners; these
  options decide who the files *appear* to belong to. They must match the user
  the container runs as (`APERTURE_UID`/`APERTURE_GID` in `.env`, 10001 by
  default), or the console can read the share but not write to it.
- **`rw`** — the console edits `album.cfg`, `gallery.cfg`, per-language
  descriptions, icons, fonts, wallpapers and `.tags` sidecars on the share. It
  never writes a photograph (see *Security / hosting* in the README), but it
  does need a writable mount. If you want the share mounted `ro`, run the two
  surfaces separately — see *Two containers* below.
- `_netdev,nofail` — if vega is unreachable, the server still boots

Mount it:

```bash
sudo mount -a
ls /mnt/vega-gallery   # should list the album folders
```

If the share is guest-accessible, replace `credentials=…` with `guest`.

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
APERTURE_UID=10001
APERTURE_GID=10001
PORT=8000
SCAN_INTERVAL=300
ENABLE_WATCHER=0

# where the console is reachable — this address is the boundary
CONSOLE_HOST=127.0.0.1
```

Why `ENABLE_WATCHER=0` and `SCAN_INTERVAL=300`:

> `inotify` (the Linux kernel file-event API) **does not reliably fire over
> SMB/CIFS** — new images often don't trigger events. So the watcher is off and
> the gallery rescans every 5 minutes instead.

**Behind a reverse proxy** (nginx, Caddy, Traefik, a Cloudflare Tunnel agent):
set `FORWARDED_ALLOW_IPS` to the proxy's address. Only that peer is believed
about the real client IP — and the console's login throttle and audit log key
on it. With the proxy left untrusted, every visitor looks like the proxy, and
one wrong password slows everyone down.

```env
FORWARDED_ALLOW_IPS=172.18.0.1
PUBLIC_BASE_URL=https://gallery.lucya.systems
```

## 4. Prepare the directories

The container runs as uid 10001, not as root. The directories it writes to
must belong to that user:

```bash
mkdir -p data thumbnails previews
sudo chown -R 10001:10001 data thumbnails previews
```

**Upgrading from a pre-1.0 gallery container:** that one ran as root, so the
existing `data/`, `thumbnails/` and `previews/` belong to root. Run the `chown`
above once before the first start, or the index and the thumbnails cannot be
written.

## 5. Set the console password

The console refuses to start without one: inside the container it binds
`0.0.0.0`, and it cannot tell from in there that you only published the port
on `127.0.0.1`.

```bash
docker compose build
docker compose run --rm aperture python -m aperture.cli passwd
```

The hash lands in `data/console/credentials`. Run the same command again to
change it; every open console session ends when you do.

## 6. Start

```bash
docker compose up -d
docker compose logs -f
```

- Gallery: `http://prod-server:8000`
- Console: `http://127.0.0.1:8090` on the server itself — reach it from your
  machine with an SSH tunnel:

  ```bash
  ssh -L 8090:127.0.0.1:8090 user@prod
  ```

  then open `http://127.0.0.1:8090` locally. To put it on a LAN or VPN address
  instead, set `CONSOLE_HOST` to that address. Do not put it on the internet.

## 7. Updates

```bash
git pull   # or rsync/scp
docker compose up -d --build
```

Thumbnails, previews, the index, the console password and its audit log all
persist in `./thumbnails`, `./previews` and `./data`.

## Two containers

For the strictest setup — the photo share mounted read-only wherever the public
gallery runs — run the same image twice, from two compose files or two service
blocks:

| service | `APERTURE_ROLE` | photos mount | ports |
|---|---|---|---|
| public | `public` | `:ro` | `8000` |
| console | `console` | `:rw` | `127.0.0.1:8090` |

Both mount the same `data/`. The public one owns the indexer; the console one
asks it for scans through `data/control/`, the same way the CLI does.

## Troubleshooting

**Container sees `/photos` empty:**
- Check on the host: `ls /mnt/vega-gallery` — must show the album folders
- If empty: SMB mount broken → `sudo mount -a` and `dmesg | tail`

**`PermissionError` in the logs, or the console cannot save:**
- `ls -ln data thumbnails previews` — they must belong to `10001` (or whatever
  `APERTURE_UID` says). Fix with the `chown` from step 4.
- On the share: `ls -ln /mnt/vega-gallery | head` must show the same uid.
  Fix the `uid=`/`gid=` options in fstab, then `sudo umount /mnt/vega-gallery &&
  sudo mount -a`.

**The console does not start ("has no password and is not bound to loopback"):**
- Step 5. The message lists the other two ways out.

**New images not appearing:**
- With `SCAN_INTERVAL=300` it takes up to 5 minutes.
- Do not want to wait: **Operations → Scan now** in the console, or
  `docker compose exec aperture python -m aperture.cli scan`
  (add an album path to limit it to one subtree).
- Still missing: **Operations → Run doctor**, or
  `docker compose exec aperture python -m aperture.cli doctor` — lists files
  the index does not know about, unreadable originals and config errors.
  `… python -m aperture.cli status` shows whether the indexer is paused or stuck.

**Maintenance on the share:**
- **Operations → Pause indexing**, or
  `docker compose exec aperture python -m aperture.cli pause "resorting"`,
  stops the indexer from reacting while you move folders around; resume
  afterwards picks everything up in one pass. The pause survives a restart.

**SMB performance:**
- The first scan over the share takes a while (parsing EXIF requires reading the
  image header). After that the SQLite cache + local thumbnails make everything
  fast; only previews/originals are fetched over SMB on demand.
