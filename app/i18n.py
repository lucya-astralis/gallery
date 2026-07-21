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
    "nav.search_ph": (
        "Search / album, file, tag",
        "Suche / Album, Datei, Tag",
        "検索 / アルバム・ファイル・タグ",
    ),
    # short form swapped in on phones (app.js), where the full hint is too long
    # for the narrow field — see the data-ph-short attribute in base.html
    "nav.search_ph_short": ("Search", "Suchen", "検索"),
    "nav.lang_label": ("Language", "Sprache", "言語"),
    "foot.operator": ("Operator", "Operator", "オペレーター"),
    "foot.about": ("lucya / about me ↗", "lucya / über mich ↗", "lucya / 私について ↗"),
    "foot.privacy": ("Privacy", "Datenschutz", "プライバシー"),

    # ---- breadcrumbs / pathbar ---------------------------------------
    "crumb.home": ("HOME", "START", "ホーム"),
    "crumb.albums": ("ALBUMS", "ALBEN", "アルバム"),
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
    "welcome.browse": ("BROWSE ALBUMS →", "ALBEN DURCHSTÖBERN →", "アルバムを見る →"),
    "welcome.open_frame": ("OPEN FRAME ↗", "FRAME ÖFFNEN ↗", "フレームを開く ↗"),
    "welcome.about_me": ("ABOUT ME ↗", "ÜBER MICH ↗", "私について ↗"),
    "welcome.no_images": (
        "No images indexed yet.",
        "Noch keine Bilder indexiert.",
        "まだ画像がインデックスされていません。",
    ),
    "band.images": ("Images", "Bilder", "写真"),
    "band.albums": ("Albums", "Alben", "アルバム"),
    "band.featured": ("Featured", "Ausgewählt", "特集"),
    "showcase.head": ("★ SHOWCASE ALBUMS", "★ AUSGEWÄHLTE ALBEN", "★ 特集アルバム"),
    "showcase.all": ("ALL ALBUMS →", "ALLE ALBEN →", "すべてのアルバム →"),
    "aria.prev_frame": ("Previous frame", "Vorheriger Frame", "前のフレーム"),
    "aria.next_frame": ("Next frame", "Nächster Frame", "次のフレーム"),
    "aria.frame_selector": ("Frame selector", "Frame-Auswahl", "フレーム選択"),
    "aria.reshuffle": ("Reshuffle feed", "Feed neu mischen", "フィードをシャッフル"),

    # ---- album index (/albums) ----------------------------------------
    "index.slug": ("ALBUMS", "ALBEN", "アルバム"),
    # decorative counterpart next to the slug: JP shows Japanese on the
    # EN/DE pages, and flips to English on the JP page so it stays a
    # bilingual ornament instead of repeating the slug.
    "index.slug_deco": ("アルバム", "アルバム", "ALBUMS"),
    "stat.albums": ("ALBUMS", "ALBEN", "アルバム"),
    "stat.images": ("IMAGES", "BILDER", "枚"),
    "feat.label": ("★ FEATURED", "★ AUSGEWÄHLT", "★ 特集"),
    "feat.hint": ("CURATED SETS", "KURATIERTE SETS", "キュレーション"),
    "archive.label": ("▦ ARCHIVE", "▦ ARCHIV", "▦ アーカイブ"),
    "unit.directory": ("DIRECTORY", "VERZEICHNIS", "ディレクトリ"),
    "unit.directories": ("DIRECTORIES", "VERZEICHNISSE", "ディレクトリ"),
    "sort.btn": ("SORT", "SORTIEREN", "並び替え"),
    "sort.by": ("SORT BY", "SORTIEREN NACH", "並び替え順"),
    "card.no_cover": ("NO COVER", "KEIN COVER", "カバーなし"),
    "card.enter": ("ENTER →", "ÖFFNEN →", "開く →"),
    "card.view_album": ("VIEW ALBUM →", "ALBUM ANSEHEN →", "アルバムを見る →"),
    "unit.image": ("IMAGE", "BILD", "枚"),
    "unit.images": ("IMAGES", "BILDER", "枚"),
    "unit.dir": ("DIR", "ORDNER", "フォルダ"),
    "unit.dirs": ("DIRS", "ORDNER", "フォルダ"),
    "unit.folder": ("▸ FOLDER", "▸ ORDNER", "▸ フォルダ"),

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

    # ---- album page -----------------------------------------------------
    "album.og_desc": (
        "{count} {unit} in “{album}”",
        "{count} {unit} in „{album}“",
        "「{album}」の写真{count}{unit}",
    ),
    "album.og_unit_one": ("image", "Bild", "枚"),
    "album.og_unit_many": ("images", "Bilder", "枚"),
    "album.slug_deco": ("ギャラリー", "ギャラリー", "GALLERY"),
    "stamp.sub_album": ("▸ SUB-ALBUM", "▸ UNTERALBUM", "▸ サブアルバム"),
    "stamp.showcase_album": ("★ SHOWCASE ALBUM", "★ AUSGEWÄHLTES ALBUM", "★ 特集アルバム"),
    "stamp.collection": ("⧉ COLLECTION", "⧉ SAMMLUNG", "⧉ コレクション"),
    "album.about": ("▸ ABOUT", "▸ INFO", "▸ 概要"),
    "album.folders": ("▸ FOLDERS", "▸ ORDNER", "▸ フォルダ"),
    "album.photos": ("▤ PHOTOS", "▤ FOTOS", "▤ 写真"),
    "unit.album_one": ("album", "Album", "件"),
    "unit.album_many": ("albums", "Alben", "件"),
    "unit.image_one": ("image", "Bild", "枚"),
    "unit.image_many": ("images", "Bilder", "枚"),
    "tag.all": ("ALL", "ALLE", "すべて"),
    "empty.tag_prefix": ("No images tagged ", "Keine Bilder mit Tag ", ""),
    "empty.tag_suffix": (".", ".", " のタグが付いた画像はありません。"),
    "empty.no_images": ("No images.", "Keine Bilder.", "画像がありません。"),
    "aria.up_to": ("Up to {name}", "Hoch zu {name}", "{name} へ戻る"),
    "aria.folders": ("Folders", "Ordner", "フォルダ"),
    "aria.album_info": ("Album info", "Album-Info", "アルバム情報"),

    # ---- featured reel (_featured.html) ---------------------------------
    "reel.random": ("⟳ RANDOM", "⟳ ZUFALL", "⟳ ランダム"),
    "reel.featured": ("★ FEATURED", "★ AUSGEWÄHLT", "★ 特集"),
    "unit.photo": ("PHOTO", "FOTO", "枚"),
    "unit.photos": ("PHOTOS", "FOTOS", "枚"),
    "reel.open": ("OPEN ↗", "ÖFFNEN ↗", "開く ↗"),
    "aria.random_photos": ("Random photos", "Zufällige Fotos", "ランダム写真"),
    "aria.featured_photos": ("Featured photos", "Ausgewählte Fotos", "特集写真"),
    "aria.prev_photo": ("Previous photo", "Vorheriges Foto", "前の写真"),
    "aria.next_photo": ("Next photo", "Nächstes Foto", "次の写真"),
    "aria.photo_selector": ("Photo selector", "Foto-Auswahl", "写真選択"),

    # ---- trip dashboard (_trip.html) ------------------------------------
    "trip.tag": ("▸ TRIP", "▸ REISE", "▸ 旅"),
    "trip.status_aria": ("{title} — trip status", "{title} — Reisestatus", "{title} — 旅の状況"),
    "trip.jst_title": ("Local time in Japan", "Ortszeit in Japan", "日本の現地時間"),
    "trip.departs_in": ("Departs in", "Abflug in", "出発まで"),
    "trip.days": ("Days", "Tage", "日"),
    "trip.hrs": ("Hrs", "Std", "時間"),
    "trip.min": ("Min", "Min", "分"),
    "trip.sec": ("Sec", "Sek", "秒"),
    "trip.flight": ("Flight", "Flug", "フライト"),

    # ---- search ----------------------------------------------------------
    "search.slug": ("SEARCH", "SUCHE", "検索"),
    "search.slug_deco": ("検索", "検索", "SEARCH"),
    "search.crumb": ("SEARCH", "SUCHE", "検索"),
    "search.no_match_prefix": ("No matches for ", "Keine Treffer für ", ""),
    "search.no_match_suffix": (".", ".", " に一致する結果はありません。"),
    "search.hint": (
        "Searches album names, filenames and tags.",
        "Durchsucht Albumnamen, Dateinamen und Tags.",
        "アルバム名・ファイル名・タグを検索します。",
    ),

    # ---- 404 --------------------------------------------------------------
    "nf.title": ("NOT FOUND", "NICHT GEFUNDEN", "見つかりません"),
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
    "nf.home": ("← home", "← start", "← ホーム"),
    "nf.albums": ("albums →", "alben →", "アルバム →"),

    # ---- image detail ------------------------------------------------------
    "image.og_desc": (
        "Photo from album “{album}” · lucya.systems gallery",
        "Foto aus dem Album „{album}“ · lucya.systems gallery",
        "アルバム「{album}」の写真 · lucya.systems gallery",
    ),
    "kv.album": ("Album", "Album", "アルバム"),
    "kv.filename": ("Filename", "Dateiname", "ファイル名"),
    "kv.dimensions": ("Dimensions", "Abmessungen", "寸法"),
    "kv.size": ("Size", "Größe", "サイズ"),
    "kv.original": ("Original", "Original", "オリジナル"),
    "kv.download": ("download ↓", "herunterladen ↓", "ダウンロード ↓"),
    "panel.description": ("DESCRIPTION", "BESCHREIBUNG", "説明"),
    "panel.tags": ("TAGS", "TAGS", "タグ"),
    "panel.raw_dump": ("raw dump", "Rohdaten", "生データ"),
    "panel.no_exif": ("No EXIF data.", "Keine EXIF-Daten.", "EXIFデータがありません。"),
    "btn.load_original": ("Load original", "Original laden", "オリジナルを読み込む"),
    "btn.fullscreen": ("Fullscreen ⛶", "Vollbild ⛶", "全画面 ⛶"),
    "btn.download": ("Download ↓", "Herunterladen ↓", "ダウンロード ↓"),
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


def month_label(lang: str, iso: str | None) -> str | None:
    """'2026-06-27T18:57:15' -> 'JUN 2026' / 'JUN 2026' / '2026年6月'
    for the album-card date chips."""
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
    return f"{months[m - 1]} {y}".upper()


def date_span(lang: str, iso_min: str | None, iso_max: str | None) -> str | None:
    """Day-precise, collapsed date range for the album SPAN stat:
        same day    -> '20 MAR 2026'      / '20. MÄR 2026'      / '2026年3月20日'
        same month  -> '20–21 MAR 2026'    / '20.–21. MÄR 2026'  / '2026年3月20–21日'
        same year   -> '20 MAR – 5 APR 2026'                     / '2026年3月20日 – 4月5日'
        crosses year-> '28 DEC 2025 – 3 JAN 2026'
    Uppercased for en/de like month_label (it reads as a HUD chip). Accepts a
    single side (min or max may be None); None only when neither parses."""
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
        return months[m - 1].upper()

    if (y1, m1, d1) == (y2, m2, d2):
        return f"{d1}{dot} {_mon(m1)} {y1}"
    if (y1, m1) == (y2, m2):
        return f"{d1}{dot}–{d2}{dot} {_mon(m1)} {y1}"
    if y1 == y2:
        return f"{d1}{dot} {_mon(m1)} – {d2}{dot} {_mon(m2)} {y1}"
    return f"{d1}{dot} {_mon(m1)} {y1} – {d2}{dot} {_mon(m2)} {y2}"
