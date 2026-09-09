"""UI translations (EN / DE / JP).

One flat dict: key -> (en, de, jp). `t(lang, key)` picks the column and
falls back to English for missing/empty entries, and to the key itself for
unknown keys, so a typo can never crash a template.

Editorial line: real content (buttons, leads, counters, sort menu, EXIF
table, trip countdown) is translated; the decorative camera-HUD tokens
(REC, FRM, SIG /, AF·LOCK, ONLINE, …) deliberately stay English in every
language — just like the HUD of an actual Japanese camera. The empty-index
setup notes are operator-facing and stay English too.

Adding Japanese text ANYWHERE (here, templates, app.js, album_jp.md) may
introduce new glyphs: re-run  python tools/build_jp_subset.py  afterwards,
or the new characters render as tofu (the shipped Noto Sans JP woff2 is a
glyph subset).
"""

from datetime import date as _date

# Cookie / file-suffix language codes. "jp" (not ISO "ja") because the album
# markdown files are named album_jp.md; HTML_LANG maps to proper BCP-47.
LANGS = ("en", "de", "jp")
DEFAULT_LANG = "en"
HTML_LANG = {"en": "en", "de": "de", "jp": "ja"}
# selector chips, shown in their own language
LANG_LABELS = {"en": "EN", "de": "DE", "jp": "日本語"}

_IDX = {lang: i for i, lang in enumerate(LANGS)}

STRINGS: dict[str, tuple[str, str, str]] = {
    # ---- shared chrome (base.html) -----------------------------------
    "meta.site_desc": (
        "Personal photo archive.",
        "Persönliches Fotoarchiv.",
        "個人写真アーカイブ。",
    ),
    "nav.search_ph": ("Search albums, files and tags", "Alben, Dateien und Tags durchsuchen", "検索 / アルバム・ファイル・タグ"),
    # short form swapped in on phones (app.js), where the full hint is too long
    # for the narrow field — see the data-ph-short attribute in base.html
    "nav.search_ph_short": ("Search", "Suchen", "検索"),
    "nav.lang_label": ("Language", "Sprache", "言語"),
    "foot.operator": ("Operator", "Operator", "オペレーター"),
    # {operator} is gallery.cfg's `operator` — who runs this archive. The
    # name used to be baked in here, which made the translation table a
    # branding file; see the site-branding section in main.py.
    "foot.about": ("{operator} / about me", "{operator} / über mich",
                   "{operator} / 私について"),
    "foot.privacy": ("Privacy", "Datenschutz", "プライバシー"),
    # gallery.cfg `privacy_url` / `imprint_url` decide where these point,
    # and a deployment is free to send both to one combined page
    "foot.imprint": ("Imprint", "Impressum", "運営者情報"),

    # ---- breadcrumbs / pathbar ---------------------------------------
    "crumb.home": ("Home", "Start", "ホーム"),
    "crumb.albums": ("Albums", "Alben", "アルバム"),
    "back.label": ("Back to", "Zurück zu", "戻る:"),
    "back.home": ("Home", "Start", "ホーム"),
    "back.albums": ("Albums", "Alben", "アルバム"),

    # ---- welcome (viewfinder hero) ------------------------------------
    "welcome.og_desc": (
        "Personal photo archive · {images} images in {albums} albums.",
        "Persönliches Fotoarchiv · {images} Bilder in {albums} Alben.",
        "個人写真アーカイブ · {albums}アルバム・{images}枚。",
    ),
    "welcome.lead": (
        "A read-only window into a personal photo archive.",
        "Ein Read-only-Fenster in ein persönliches Fotoarchiv.",
        "個人写真アーカイブをのぞく読み取り専用の窓。",
    ),
    "welcome.browse": ("Browse albums", "Alben durchstöbern", "アルバムを見る"),
    "welcome.open_frame": ("Open photo", "Foto öffnen", "フレームを開く"),
    "welcome.about_me": ("About me", "Über mich", "私について"),
    "welcome.no_images": (
        "No images indexed yet.",
        "Noch keine Bilder indexiert.",
        "まだ画像がインデックスされていません。",
    ),
    # ---- archive readout under the hero (welcome.html .arc) -------------
    "band.head": ("Archive", "Archiv", "アーカイブ"),
    "band.images": ("Images", "Bilder", "写真"),
    "band.albums": ("Albums", "Alben", "アルバム"),
    "band.featured": ("Featured", "Ausgewählt", "特集"),
    "band.updated": ("Last update", "Letztes Update", "最終更新"),
    # link out of the readout into the full /stats page
    "band.stats": ("All statistics", "Alle Statistiken", "統計をすべて見る"),
    "showcase.head": ("Featured albums", "Ausgewählte Alben", "特集アルバム"),
    "showcase.all": ("All albums", "Alle Alben", "すべてのアルバム"),
    "aria.prev_frame": ("Previous frame", "Vorheriger Frame", "前のフレーム"),
    "aria.next_frame": ("Next frame", "Nächster Frame", "次のフレーム"),
    "aria.frame_selector": ("Frame selector", "Frame-Auswahl", "フレーム選択"),
    "welcome.reshuffle": ("Shuffle", "Mischen", "シャッフル"),
    "aria.reshuffle": ("Reshuffle feed", "Feed neu mischen", "フィードをシャッフル"),

    # ---- album index (/albums) ----------------------------------------
    "index.slug": ("Albums", "Alben", "アルバム"),
    # decorative counterpart next to the slug: JP shows Japanese on the
    # EN/DE pages, and flips to English on the JP page so it stays a
    # bilingual ornament instead of repeating the slug.
    "stat.albums": ("albums", "Alben", "アルバム"),
    # keys of the album description card's stat rows (main._album_stats)
    "stat.location": ("Location", "Ort", "場所"),
    "stat.span": ("Dates", "Zeitraum", "期間"),
    "stat.device": ("Camera", "Kamera", "カメラ"),
    "stat.focal": ("Focal length", "Brennweite", "焦点距離"),
    "stat.aperture": ("Aperture", "Blende", "絞り"),
    "stat.data": ("Size", "Größe", "サイズ"),
    "stat.images": ("photos", "Fotos", "枚"),
    "feat.label": ("Featured", "Ausgewählt", "特集"),
    "archive.label": ("Archive", "Archiv", "アーカイブ"),
    "unit.directory": ("folder", "Ordner", "ディレクトリ"),
    "unit.directories": ("folders", "Ordner", "ディレクトリ"),
    "sort.btn": ("Sort", "Sortieren", "並び替え"),
    "sort.by": ("Sort by", "Sortieren nach", "並び替え順"),
    "card.no_cover": ("No cover", "Kein Cover", "カバーなし"),
    "card.enter": ("Open", "Öffnen", "開く"),
    "card.view_album": ("View album", "Album ansehen", "アルバムを見る"),
    "unit.image": ("photo", "Foto", "枚"),
    "unit.images": ("photos", "Fotos", "枚"),
    "unit.dir": ("folder", "Ordner", "フォルダ"),
    "unit.dirs": ("folders", "Ordner", "フォルダ"),
    "unit.folder": ("folder", "Ordner", "フォルダ"),

    # ---- sort options (main.py) ----------------------------------------
    "sort.date_desc": ("Newest first", "Neueste zuerst", "新しい順"),
    "sort.date_asc": ("Oldest first", "Älteste zuerst", "古い順"),
    "sort.name_asc": ("Filename A → Z", "Dateiname A → Z", "ファイル名 A → Z"),
    "sort.name_desc": ("Filename Z → A", "Dateiname Z → A", "ファイル名 Z → A"),
    "sort.size_desc": ("Largest first", "Größte zuerst", "サイズが大きい順"),
    "sort.size_asc": ("Smallest first", "Kleinste zuerst", "サイズが小さい順"),
    "sort.latest_desc": ("Most recent", "Zuletzt aktiv", "更新が新しい順"),
    "sort.latest_asc": ("Oldest activity", "Älteste Aktivität", "更新が古い順"),
    "sort.album_name_asc": ("Name A → Z", "Name A → Z", "名前 A → Z"),
    "sort.album_name_desc": ("Name Z → A", "Name Z → A", "名前 Z → A"),
    "sort.count_desc": ("Most photos", "Meiste Fotos", "写真が多い順"),
    "sort.count_asc": ("Fewest photos", "Wenigste Fotos", "写真が少ない順"),
    "sort.curated": ("Curated", "Kuratiert", "キュレーション"),
    # chronological, split into one framed section per capture day
    # (only offered when an album's photos span more than one day)
    "sort.days": ("By day", "Nach Tagen", "日付ごと"),

    # ---- day sections (Sort → By day) -----------------------------------
    "day.n": ("Day {n}", "Tag {n}", "{n}日目"),
    "day.undated": ("Undated", "Ohne Datum", "日付なし"),

    # ---- album page -----------------------------------------------------
    "album.og_desc": (
        "{count} {unit} in “{album}”",
        "{count} {unit} in „{album}“",
        "「{album}」の写真{count}{unit}",
    ),
    "album.og_unit_one": ("image", "Bild", "枚"),
    "album.og_unit_many": ("images", "Bilder", "枚"),
    "stamp.sub_album": ("Sub-album", "Unteralbum", "サブアルバム"),
    "stamp.showcase_album": ("Featured album", "Ausgewähltes Album", "特集アルバム"),
    # Short form for the doc line under the pathbar, where the mark sits
    # among three or four other chips: "Ausgewähltes Album" ran that row
    # off its own width in German. The hero kicker, which has a line to
    # itself, keeps the full phrase above.
    "stamp.showcase": ("Featured", "Ausgewählt", "特集"),
    "stamp.collection": ("Collection", "Sammlung", "コレクション"),
    "album.about": ("About", "Info", "概要"),
    "album.folders": ("Folders", "Ordner", "フォルダ"),
    "album.photos": ("Photos", "Fotos", "写真"),
    "unit.album_one": ("album", "Album", "件"),
    "unit.album_many": ("albums", "Alben", "件"),
    "unit.image_one": ("image", "Bild", "枚"),
    "unit.image_many": ("images", "Bilder", "枚"),
    "tag.all": ("All", "Alle", "すべて"),
    "empty.tag_prefix": ("No images tagged ", "Keine Bilder mit Tag ", ""),
    "empty.tag_suffix": (".", ".", " のタグが付いた画像はありません。"),
    "empty.no_images": ("No images.", "Keine Bilder.", "画像がありません。"),
    "aria.up_to": ("Up to {name}", "Hoch zu {name}", "{name} へ戻る"),
    "aria.folders": ("Folders", "Ordner", "フォルダ"),
    "aria.album_info": ("Album info", "Album-Info", "アルバム情報"),

    # ---- featured reel (_featured.html) ---------------------------------
    "unit.photo": ("photo", "Foto", "枚"),
    "unit.photos": ("photos", "Fotos", "枚"),
    "reel.open": ("Open", "Öffnen", "開く"),
    "aria.random_photos": ("Random photos", "Zufällige Fotos", "ランダム写真"),
    "aria.featured_photos": ("Featured photos", "Ausgewählte Fotos", "特集写真"),
    "aria.prev_photo": ("Previous photo", "Vorheriges Foto", "前の写真"),
    "aria.next_photo": ("Next photo", "Nächstes Foto", "次の写真"),
    "aria.photo_selector": ("Photo selector", "Foto-Auswahl", "写真選択"),

    # ---- trip dashboard (_trip.html) ------------------------------------
    "trip.tag": ("Trip", "Reise", "旅"),
    "trip.status_aria": ("{title} — trip status", "{title} — Reisestatus", "{title} — 旅の状況"),
    "trip.jst_title": ("Local time in Japan", "Ortszeit in Japan", "日本の現地時間"),
    # visible prefix on the live clock, so "16:20 JST" reads as a place
    "trip.jst_label": ("Japan time", "Japan-Zeit", "日本時間"),
    "trip.departs_in": ("Departs in", "Abflug in", "出発まで"),
    "trip.days": ("Days", "Tage", "日"),
    "trip.hrs": ("Hrs", "Std", "時間"),
    "trip.min": ("Min", "Min", "分"),
    "trip.sec": ("Sec", "Sek", "秒"),
    "trip.flight": ("Flight", "Flug", "フライト"),
    # how many stops the itinerary has — the module's own fact, in the meta
    # line where the trip's name used to be repeated
    "trip.leg": ("leg", "Etappe", "区間"),
    "trip.legs": ("legs", "Etappen", "区間"),

    # ---- statistics page (/stats) ----------------------------------------
    # Everything here describes the archive itself. No visitor is measured,
    # nothing is logged — which is why the page is safe to hand to anyone,
    # and why the lead says so out loud.
    "stats.slug": ("Statistics", "Statistiken", "統計"),
    "stats.crumb": ("Statistics", "Statistiken", "統計"),
    "stats.title": ("Statistics — {brand}", "Statistiken — {brand}",
                    "統計 — {brand}"),
    "stats.og_desc": (
        "What the archive holds: {images} photos across {albums} albums, charted.",
        "Was das Archiv enthält: {images} Fotos in {albums} Alben, als Diagramme.",
        "アーカイブの中身：{albums}アルバム・{images}枚をグラフで。",
    ),
    "stats.lead": (
        "Everything on this page is measured from the photos themselves — their "
        "capture dates and their EXIF. No visitor is counted and nothing is logged.",
        "Alles auf dieser Seite ist aus den Fotos selbst gemessen — aus ihren "
        "Aufnahmedaten und ihren EXIF-Daten. Es werden keine Besucher gezählt und "
        "nichts protokolliert.",
        "このページの数値はすべて写真そのもの（撮影日時とEXIF）から算出しています。"
        "訪問者の計測もログの記録もありません。",
    ),
    "stats.more": ("+{n} more", "+{n} weitere", "他{n}件"),
    "stats.empty": (
        "Nothing indexed yet — there is nothing to measure.",
        "Noch nichts indexiert — es gibt nichts zu messen.",
        "まだ何もインデックスされていません。"
    ),
    # headline figures
    "stats.fig.photos": ("Photos", "Fotos", "写真"),
    "stats.fig.albums": ("Albums", "Alben", "アルバム"),
    "stats.fig.folders": ("Folders", "Ordner", "フォルダ"),
    "stats.fig.featured": ("Featured", "Ausgewählt", "特集"),
    "stats.fig.tags": ("Tags", "Tags", "タグ"),
    "stats.fig.data": ("Data", "Daten", "データ"),
    "stats.fig.pixels": ("Pixels", "Pixel", "ピクセル"),
    "stats.fig.days": ("Days shot", "Fototage", "撮影日数"),
    "stats.fig.busiest": ("Busiest day", "Stärkster Tag", "最多の日"),
    # chart headings
    "stats.timeline.head": ("Photos per month", "Fotos pro Monat", "月別の写真数"),
    "stats.timeline.note": (
        "Rolling {n}-month window ending with the newest photo.",
        "Gleitendes Fenster über {n} Monate bis zum neuesten Foto.",
        "最新の写真までの{n}か月間。",
    ),
    "stats.albums.head": ("Largest albums", "Größte Alben", "アルバム別"),
    "stats.tags.head": ("Most used tags", "Häufigste Tags", "よく使うタグ"),
    "stats.weekday.head": ("By weekday", "Nach Wochentag", "曜日別"),
    "stats.hour.head": ("By hour of day", "Nach Tageszeit", "時間帯別"),
    # A dial looks like a clock, so it has to say that it carries 24 hours
    # and not 12 — the eight printed ticks show it, this says it.
    "stats.hour.note": (
        "24-hour dial, midnight at the top, running clockwise. Local time where the photo was taken.",
        "24-Stunden-Zifferblatt, Mitternacht oben, im Uhrzeigersinn. Ortszeit am Aufnahmeort.",
        "24時間の文字盤。0時が上、時計回り。撮影地の現地時間。",
    ),
    "stats.camera.head": ("Cameras", "Kameras", "カメラ"),
    "stats.shape.head": ("Orientation", "Ausrichtung", "向き"),
    "stats.focal.head": ("Focal length", "Brennweite", "焦点距離"),
    "stats.focal.note": (
        "35 mm equivalent.",
        "Kleinbild-Äquivalent.",
        "35mm換算。",
    ),
    "stats.aperture.head": ("Aperture", "Blende", "絞り"),
    "stats.iso.head": ("ISO", "ISO", "ISO"),
    "stats.shape.landscape": ("Landscape", "Querformat", "横"),
    "stats.shape.portrait": ("Portrait", "Hochformat", "縦"),
    "stats.shape.square": ("Square", "Quadratisch", "正方形"),
    # the footnote under the capture charts — EXIF is not guaranteed
    "stats.exif_note": (
        "Capture figures come from EXIF; {n} of {total} photos carry none.",
        "Aufnahmewerte stammen aus EXIF; {n} von {total} Fotos haben keine.",
        "撮影データはEXIF由来です。{total}枚中{n}枚にはEXIFがありません。",
    ),

    # ---- search ----------------------------------------------------------
    "search.slug": ("Search", "Suche", "検索"),
    "search.crumb": ("Search", "Suche", "検索"),
    "search.no_match_prefix": ("No matches for ", "Keine Treffer für ", ""),
    "search.no_match_suffix": (".", ".", " に一致する結果はありません。"),
    "search.hits_one": ("match", "Treffer", "件"),
    "search.hits_many": ("matches", "Treffer", "件"),
    "search.hint": (
        "Searches album names, filenames and tags.",
        "Durchsucht Albumnamen, Dateinamen und Tags.",
        "アルバム名・ファイル名・タグを検索します。",
    ),
    # results are grouped: the albums a query names first, its photos below
    "search.albums_head": ("Albums", "Alben", "アルバム"),
    "search.photos_head": ("Photos", "Fotos", "写真"),
    # shown when the photo half hit its cap — see SEARCH_PHOTO_LIMIT
    "search.capped": (
        "Showing the first {n}. Narrow the search for a shorter list.",
        "Die ersten {n} werden gezeigt. Suche eingrenzen für eine kürzere Liste.",
        "最初の{n}件を表示しています。検索を絞り込んでください。",
    ),

    # ---- 404 --------------------------------------------------------------
    "nf.title": ("Not found", "Nicht gefunden", "見つかりません"),
    "nf.lead_prefix": ("The resource ", "Die Ressource ", "リソース "),
    "nf.lead_suffix": (
        " does not exist in this index.",
        " existiert nicht in diesem Index.",
        " はこのインデックスに存在しません。",
    ),
    "nf.note": (
        "It may have been removed, renamed, or never existed in the first place.",
        "Sie wurde möglicherweise entfernt, umbenannt oder hat nie existiert.",
        "削除・改名されたか、そもそも存在しなかった可能性があります。",
    ),
    "nf.home": ("home", "start", "ホーム"),
    "nf.albums": ("albums", "alben", "アルバム"),

    # ---- image detail ------------------------------------------------------
    "image.og_desc": (
        "Photo from album “{album}” · {brand}",
        "Foto aus dem Album „{album}“ · {brand}",
        "アルバム「{album}」の写真 · {brand}",
    ),
    "kv.album": ("Album", "Album", "アルバム"),
    "kv.filename": ("Filename", "Dateiname", "ファイル名"),
    "kv.dimensions": ("Dimensions", "Abmessungen", "寸法"),
    "kv.size": ("Size", "Größe", "サイズ"),
    "kv.original": ("Original", "Original", "オリジナル"),
    "kv.download": ("download", "herunterladen", "ダウンロード"),
    "panel.file": ("File", "Datei", "ファイル"),
    "panel.description": ("Description", "Beschreibung", "説明"),
    "panel.tags": ("Tags", "Tags", "タグ"),
    "panel.raw_dump": ("raw dump", "Rohdaten", "生データ"),
    "panel.no_exif": ("No EXIF data.", "Keine EXIF-Daten.", "EXIFデータがありません。"),
    # the stage starts on the downscaled preview and swaps to the original on
    # demand. Only the initial state is rendered here — both labels the marker
    # then flips between live in app.js (UI_STRINGS.qualityPreview /
    # .qualityOriginal), so there is deliberately no "quality.original" key.
    "quality.preview": ("Preview", "Vorschau", "プレビュー"),
    "btn.load_original": ("Load original", "Original laden", "オリジナルを読み込む"),
    "btn.fullscreen": ("Fullscreen", "Vollbild", "全画面"),
    "btn.download": ("Download", "Herunterladen", "ダウンロード"),
    "aria.previous": ("Previous", "Zurück", "前へ"),
    "aria.next": ("Next", "Weiter", "次へ"),
    "aria.close": ("Close (Esc)", "Schließen (Esc)", "閉じる (Esc)"),
    "aria.lightbox": ("Fullscreen viewer", "Vollbild-Ansicht", "全画面ビューア"),

    # ---- EXIF labels (main._prettify_exif) ----------------------------------
    "exif.make": ("Camera make", "Kamerahersteller", "メーカー"),
    "exif.model": ("Camera model", "Kameramodell", "機種"),
    "exif.lens": ("Lens", "Objektiv", "レンズ"),
    "exif.date_taken": ("Date taken", "Aufnahmedatum", "撮影日時"),
    "exif.exposure": ("Exposure", "Belichtungszeit", "露出時間"),
    "exif.aperture": ("Aperture", "Blende", "絞り"),
    "exif.iso": ("ISO", "ISO", "ISO"),
    "exif.focal": ("Focal length", "Brennweite", "焦点距離"),
    "exif.focal35": ("Focal length (35mm eq.)", "Brennweite (35mm)", "焦点距離（35mm換算）"),
    "exif.flash": ("Flash", "Blitz", "フラッシュ"),
    "exif.wb": ("White balance", "Weißabgleich", "ホワイトバランス"),
    "exif.program": ("Exposure program", "Belichtungsprogramm", "露出プログラム"),
    "exif.metering": ("Metering mode", "Messmethode", "測光方式"),
    "exif.orientation": ("Orientation", "Ausrichtung", "向き"),
    "exif.software": ("Software", "Software", "ソフトウェア"),
    "exif.gps": ("GPS", "GPS", "GPS"),
}


def t(lang: str, key: str, **fmt) -> str:
    """Translate `key` into `lang` (EN fallback per entry, key as last
    resort). Optional str.format kwargs; a bad placeholder degrades to the
    unformatted string rather than raising mid-render."""
    entry = STRINGS.get(key)
    if entry is None:
        return key
    s = entry[_IDX.get(lang, 0)] or entry[0]
    if fmt:
        try:
            return s.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return s
    return s


# ---- dates ---------------------------------------------------------------
_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTHS_DE = ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez")


def fmt_date(lang: str, iso_date: str) -> str:
    """'2026-08-10' -> '10 Aug 2026' / '10. Aug 2026' / '2026年8月10日'."""
    try:
        y, m, d = (int(x) for x in iso_date[:10].split("-"))
    except (ValueError, IndexError):
        return iso_date
    if lang == "jp":
        return f"{y}年{m}月{d}日"
    if lang == "de":
        return f"{d}. {_MONTHS_DE[m - 1]} {y}"
    return f"{d} {_MONTHS_EN[m - 1]} {y}"


_WEEKDAYS_EN = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_WEEKDAYS_DE = ("MO", "DI", "MI", "DO", "FR", "SA", "SO")
_WEEKDAYS_JP = ("月", "火", "水", "木", "金", "土", "日")


def day_label(lang: str, n: int) -> str:
    """Day counter for the day-section headers: 'DAY 05' / 'TAG 05' / '5日目'.
    en/de are zero-padded so the mono headers line up in a long list; the
    Japanese counter reads wrong padded, so it stays bare."""
    return t(lang, "day.n", n=str(n) if lang == "jp" else f"{n:02d}")


def weekday_index(lang: str, idx: int) -> str:
    """Weekday by POSITION (0 = Monday) rather than by date — the /stats
    rhythm chart plots a fixed Mon…Sun axis that has no date to read."""
    table = _WEEKDAYS_JP if lang == "jp" else (_WEEKDAYS_DE if lang == "de" else _WEEKDAYS_EN)
    return table[idx % 7]


def month_short(lang: str, year: int, month: int) -> str:
    """Axis tick for the /stats timeline: 'Aug' / 'Aug' / '8月'. The year is
    printed separately (only where it changes), so it is left off here."""
    if not 1 <= month <= 12:
        return ""
    if lang == "jp":
        return f"{month}月"
    return (_MONTHS_DE if lang == "de" else _MONTHS_EN)[month - 1]


def weekday_label(lang: str, iso: str | None) -> str | None:
    """'2026-08-14' -> 'FRI' / 'FR' / '金' for the day-section headers.
    None when the date doesn't parse (the header drops the chip then)."""
    if not iso:
        return None
    try:
        d = _date(int(iso[:4]), int(iso[5:7]), int(iso[8:10]))
    except (ValueError, IndexError, TypeError):
        return None
    if lang == "jp":
        return _WEEKDAYS_JP[d.weekday()]
    return (_WEEKDAYS_DE if lang == "de" else _WEEKDAYS_EN)[d.weekday()]


def month_label(lang: str, iso: str | None) -> str | None:
    """'2026-06-27T18:57:15' -> 'Jun 2026' / 'Jun 2026' / '2026年6月'
    for the album-card dates."""
    if not iso:
        return None
    try:
        y, m = int(iso[:4]), int(iso[5:7])
        if not 1 <= m <= 12:
            return None
    except (ValueError, IndexError):
        return None
    if lang == "jp":
        return f"{y}年{m}月"
    months = _MONTHS_DE if lang == "de" else _MONTHS_EN
    return f"{months[m - 1]} {y}"


def date_label(lang: str, iso: str | None) -> str | None:
    """'2026-08-27T18:57:15' -> '27 Aug 2026' / '27. Aug 2026' / '2026年8月27日'.
    The single-day case of date_span, for the archive's last-update line.
    (Not to be confused with day_label above — that one counts trip days.)"""
    return date_span(lang, iso, iso)


def date_span(lang: str, iso_min: str | None, iso_max: str | None) -> str | None:
    """Day-precise, collapsed date range for the album SPAN stat:
        same day    -> '20 Mar 2026'      / '20. Mär 2026'      / '2026年3月20日'
        same month  -> '20–21 Mar 2026'    / '20.–21. Mär 2026'  / '2026年3月20–21日'
        same year   -> '20 Mar – 5 Apr 2026'                     / '2026年3月20日 – 4月5日'
        crosses year-> '28 Dec 2025 – 3 Jan 2026'
    Accepts a single side (min or max may be None); None only when neither
    parses."""
    def _parse(iso):
        try:
            return int(iso[:4]), int(iso[5:7]), int(iso[8:10])
        except (ValueError, IndexError, TypeError):
            return None
    a, b = _parse(iso_min), _parse(iso_max)
    a, b = a or b, b or a
    if not a:
        return None
    if b < a:
        a, b = b, a
    (y1, m1, d1), (y2, m2, d2) = a, b
    if lang == "jp":
        lo = f"{y1}年{m1}月{d1}日"
        if (y1, m1, d1) == (y2, m2, d2):
            return lo
        if (y1, m1) == (y2, m2):
            return f"{y1}年{m1}月{d1}–{d2}日"
        hi = f"{m2}月{d2}日" if y1 == y2 else f"{y2}年{m2}月{d2}日"
        return f"{lo} – {hi}"
    months = _MONTHS_DE if lang == "de" else _MONTHS_EN
    dot = "." if lang == "de" else ""

    def _mon(m):
        return months[m - 1]

    if (y1, m1, d1) == (y2, m2, d2):
        return f"{d1}{dot} {_mon(m1)} {y1}"
    if (y1, m1) == (y2, m2):
        return f"{d1}{dot}–{d2}{dot} {_mon(m1)} {y1}"
    if y1 == y2:
        return f"{d1}{dot} {_mon(m1)} – {d2}{dot} {_mon(m2)} {y1}"
    return f"{d1}{dot} {_mon(m1)} {y1} – {d2}{dot} {_mon(m2)} {y2}"
