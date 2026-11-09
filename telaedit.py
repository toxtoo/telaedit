#!/usr/bin/env python3

import zipfile
from datetime import datetime
import json
import os
import html
import subprocess
import sys
import re
import shlex
import shutil
import stat
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote

# TelaEdit configuration paths
CONFIG_DIR = Path.home() / ".config" / "TelaEdit"
# TelaEdit was called SiteForge before version 0.5. Its settings were kept
# in this folder, and are copied across the first time TelaEdit starts.
OLD_CONFIG_DIR = Path.home() / ".config" / "SiteForge"
STATE_FILE = CONFIG_DIR / "state.json"
# The version shown in Help > About. Change this for each new release,
# and use the same number when you run make-release.sh.
APP_VERSION = "0.5"

SHORTCUTS_FILE = CONFIG_DIR / "shortcuts.json"
BROWSERS_FILE = CONFIG_DIR / "browsers.json"
EXTERNAL_TOOLS_FILE = CONFIG_DIR / "external_tools.json"
USER_DICTIONARY_FILE = CONFIG_DIR / "user_dictionary.txt"
TEMPLATES_DIR = CONFIG_DIR / "templates"
SNIPPETS_DIR = CONFIG_DIR / "snippets"

# Browser icons: the five generic icons that come with the program, and the
# icons a user has chosen with "Other icon..." (copied into the config
# folder so they keep working if the original file is moved).
BROWSER_ICON_DIR = Path(__file__).resolve().parent / "icons" / "browsers"
USER_BROWSER_ICON_DIR = CONFIG_DIR / "browser_icons"
DEFAULT_BROWSER_ICON = "browser-1.png"
# The last entry in each icon drop-down, which opens a file chooser
OTHER_ICON_CHOICE = "__other__"

# Toolbar icon sizes offered in Settings > Toolbar icon size. The icons are
# scaled to this size. DEFAULT_TOOLBAR_ICON_SIZE is used until you choose one.
TOOLBAR_ICON_SIZES = (20, 24, 32)
DEFAULT_TOOLBAR_ICON_SIZE = 24

# Set to True when the settings were copied from the old SiteForge folder
# during this start-up, so the window can say so.
SETTINGS_MIGRATED = False


def migrate_old_settings():
    """Copies ~/.config/SiteForge to ~/.config/TelaEdit the first time
    TelaEdit runs. The old folder is left exactly as it was."""
    global SETTINGS_MIGRATED

    if CONFIG_DIR.exists() or not OLD_CONFIG_DIR.is_dir():
        return

    try:
        shutil.copytree(OLD_CONFIG_DIR, CONFIG_DIR)
        SETTINGS_MIGRATED = True
    except (OSError, shutil.Error):
        # Not fatal: TelaEdit just starts with fresh settings. Remove a
        # half-copied folder so the next start can try again.
        shutil.rmtree(CONFIG_DIR, ignore_errors=True)


# Do it now, before anything below reads a settings file
migrate_old_settings()


def bundled_browser_icons():
    """File names of the generic browser icons that ship with the program."""
    try:
        return sorted(path.name for path in BROWSER_ICON_DIR.glob("*.png"))
    except OSError:
        return []


def user_browser_icons():
    """File names of the icons the user added with 'Other icon...'."""
    try:
        return sorted(
            path.name for path in USER_BROWSER_ICON_DIR.iterdir()
            if path.is_file()
        )
    except OSError:
        return []


def browser_icon_path(icon_name):
    """Returns the file to show for a saved browser icon name.

    'browser-2.png' is one of the bundled icons; 'user:name.png' is one the
    user added. Anything that can't be found (such as 'firefox.png' saved by
    an older version) falls back to the first bundled icon."""
    if isinstance(icon_name, str) and icon_name.startswith("user:"):
        path = USER_BROWSER_ICON_DIR / Path(icon_name[5:]).name

        if path.is_file():
            return path
    elif isinstance(icon_name, str) and icon_name:
        path = BROWSER_ICON_DIR / Path(icon_name).name

        if path.is_file():
            return path

    bundled = bundled_browser_icons()

    if bundled:
        return BROWSER_ICON_DIR / bundled[0]

    return None


def browser_icon(icon_name):
    """A QIcon for a saved browser icon name (empty if there is none)."""
    path = browser_icon_path(icon_name)
    return QIcon(str(path)) if path else QIcon()


from html.parser import HTMLParser
from PyQt6.QtCore import (
    Qt, QUrl, QDir, QSize, QTimer, QSortFilterProxyModel,
    QT_VERSION_STR, PYQT_VERSION_STR
)
from PyQt6.QtGui import QDesktopServices, QTextCursor, QShortcut, QAction, QFont, QFileSystemModel, QColor, QIcon, QKeySequence, QPixmap, QPalette, QBrush
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QFileDialog,
    QMessageBox, QTreeView, QDockWidget,
    QToolBar, QStatusBar, QDialog, QVBoxLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QDialogButtonBox,
    QPushButton, QSplitter, QWidget, QLabel, QHBoxLayout,
    QToolButton, QColorDialog, QFontDialog, QInputDialog,
    QLineEdit, QKeySequenceEdit, QTreeWidget, QTreeWidgetItem,
    QFileIconProvider, QStyledItemDelegate, QListWidget, QListWidgetItem,
    QTextEdit, QStyleOptionViewItem, QRadioButton, QComboBox, QFormLayout,
    QSpinBox, QMenu, QCheckBox
)
from PyQt6.Qsci import (
    QsciScintilla, QsciLexerHTML,
    QsciLexerCSS, QsciLexerJavaScript
)


def apply_saved_font(font, saved):
    """Applies a font description saved by QFont.toString().

    A description saved by a newer Qt can have extra fields that an older
    Qt rejects with 'QFont::fromString: Invalid description'. Only the
    first 16 fields matter, so trim to those, and if that still fails
    fall back to just the font family and size."""
    if not saved or not isinstance(saved, str):
        return font

    parts = saved.split(",")

    # Try the description trimmed to the 16 fields every Qt understands
    if font.fromString(",".join(parts[:16])):
        return font

    # Still rejected: keep just the family and size
    try:
        font.setFamily(parts[0])
        font.setPointSizeF(float(parts[1]))
    except (IndexError, ValueError):
        pass

    return font


# Syntax highlighting colours -------------------------------------------

# Which languages can have their colours changed: key -> name in the dialog
SYNTAX_LANGUAGES = {
    "html": "HTML",
    "css": "CSS",
    "js": "JavaScript",
}


def syntax_language_key(suffix):
    """Returns 'html', 'css' or 'js' for a file extension (or None)."""
    suffix = suffix.lower()

    if suffix in (".html", ".htm", ".xhtml", ".xml", ".svg"):
        return "html"
    if suffix == ".css":
        return "css"
    if suffix in (".js", ".mjs"):
        return "js"

    return None


def new_syntax_lexer(key, parent=None):
    if key == "html":
        return QsciLexerHTML(parent)
    if key == "css":
        return QsciLexerCSS(parent)
    if key == "js":
        return QsciLexerJavaScript(parent)

    return None


def load_syntax_overrides():
    """Reads the colours and bold settings you have changed.

    Returns {'html': {style_number: {'color': '#rrggbb', 'bold': True}}, ...}
    Only the styles you changed are stored; the rest keep their defaults."""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    saved = data.get("syntax_styles", {})
    result = {}

    if not isinstance(saved, dict):
        return result

    for key, styles in saved.items():
        if key not in SYNTAX_LANGUAGES or not isinstance(styles, dict):
            continue

        result[key] = {}

        for number, entry in styles.items():
            try:
                number = int(number)
            except (TypeError, ValueError):
                continue

            if not isinstance(entry, dict):
                continue

            color = QColor(str(entry.get("color", "")))
            result[key][number] = {
                "color": color.name() if color.isValid() else "",
                "bold": bool(entry.get("bold", False)),
            }

    return result


def save_syntax_overrides(overrides):
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}

    data["syntax_styles"] = {
        key: {str(number): entry for number, entry in styles.items()}
        for key, styles in overrides.items()
        if styles
    }

    try:
        STATE_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
        return True
    except OSError:
        return False


# Spell checking ---------------------------------------------------------

# Parts of an HTML file that are not text a reader sees: comments, scripts,
# styles, tags (with their attributes) and character entities.
SPELL_MASK = re.compile(
    r"<!--.*?-->|<script\b.*?</script\s*>|<style\b.*?</style\s*>"
    r"|<[^>]*>|&#?\w+;",
    re.DOTALL | re.IGNORECASE
)

# A word: letters only, with apostrophes allowed inside (don't, Stuart's)
SPELL_WORD = re.compile(r"(?<!\w)[^\W\d_]+(?:['\u2019][^\W\d_]+)*(?!\w)")

SPELL_INSTALL_HELP = (
    "Spell checking needs the program aspell or hunspell, with a "
    "dictionary for the language '{language}'.\n\n"
    "In a terminal, install them with:\n"
    "    sudo apt install aspell aspell-en\n"
    "or:\n"
    "    sudo apt install hunspell hunspell-en-gb\n\n"
    "Details: {details}"
)


# Ways of marking a misspelled word: name -> Scintilla indicator style number
SPELL_STYLES = {
    "Thick straight underline": 14,   # INDIC_COMPOSITIONTHICK
    "Wavy line (thin)": 1,            # INDIC_SQUIGGLE
    "Pale red box": 8,                # INDIC_STRAIGHTBOX
    "Red text": 17,                   # INDIC_TEXTFORE
}
DEFAULT_SPELL_STYLE = "Thick straight underline"


class SpellChecker:
    """Spell checking through the aspell or hunspell command-line programs.

    One shared copy (SPELLER) is used by every editor tab."""

    BACKENDS = ("aspell", "hunspell")

    def __init__(self):
        self.enabled = True
        self.language = "en_GB"
        self.style = DEFAULT_SPELL_STYLE
        self.cache = {}            # word -> True if the dictionary rejects it
        self.ignored = set()       # words ignored for this session (lower case)
        self.user_words = set()    # personal dictionary (lower case)
        self.working_backend = None
        self.failed = False
        self.on_error = None
        self.load_settings()
        self.load_user_words()

    def load_settings(self):
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return

        self.enabled = bool(data.get("spell_check_enabled", True))
        language = str(data.get("spell_language", "en_GB")).strip()

        if re.fullmatch(r"[A-Za-z_]{2,20}", language):
            self.language = language

        style = data.get("spell_style", DEFAULT_SPELL_STYLE)
        self.style = style if style in SPELL_STYLES else DEFAULT_SPELL_STYLE

    def save_settings(self):
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}

        data["spell_check_enabled"] = self.enabled
        data["spell_language"] = self.language
        data["spell_style"] = self.style

        try:
            STATE_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
        except OSError:
            pass

    def load_user_words(self):
        try:
            lines = USER_DICTIONARY_FILE.read_text(encoding="utf-8").splitlines()
        except OSError:
            return

        self.user_words = {line.strip().lower() for line in lines if line.strip()}

    def set_language(self, language):
        self.language = language
        self.cache.clear()
        self.failed = False
        self.working_backend = None
        self.save_settings()

    def add_word(self, word):
        word = word.strip()
        if not word:
            return

        self.user_words.add(word.lower())

        try:
            USER_DICTIONARY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(USER_DICTIONARY_FILE, "a", encoding="utf-8") as file:
                file.write(word + "\n")
        except OSError:
            pass

    def ignore_word(self, word):
        self.ignored.add(word.lower())

    def backends_to_try(self):
        found = [name for name in self.BACKENDS if shutil.which(name)]

        if self.working_backend in found:
            found.remove(self.working_backend)
            found.insert(0, self.working_backend)

        return found

    def available(self):
        return bool(self.backends_to_try())

    def command(self, backend, mode):
        if backend == "aspell":
            base = ["aspell", "--lang=" + self.language, "--encoding=utf-8"]
            return base + (["list"] if mode == "list" else ["-a"])

        base = ["hunspell", "-d", self.language, "-i", "utf-8"]
        return base + (["-l"] if mode == "list" else ["-a"])

    def run(self, mode, text):
        """Runs the checker. Returns its output, or None if it failed."""
        details = "no spell checker program found"

        for backend in self.backends_to_try():
            try:
                result = subprocess.run(
                    self.command(backend, mode),
                    input=text,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=20
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                details = f"{backend}: {error}"
                continue

            if result.returncode == 0:
                self.working_backend = backend
                return result.stdout

            details = f"{backend}: " + (
                (result.stderr or result.stdout).strip() or "failed"
            )

        self.failed = True

        if self.on_error:
            self.on_error(
                SPELL_INSTALL_HELP.format(
                    language=self.language, details=details
                )
            )

        return None

    def misspelled(self, words):
        """Given a set of words, returns the ones that are misspelled."""
        if self.failed:
            return set()

        unknown = [word for word in words if word not in self.cache]

        if unknown:
            output = self.run("list", "\n".join(unknown) + "\n")

            if output is None:
                return set()

            rejected = {
                line.strip() for line in output.splitlines() if line.strip()
            }

            for word in unknown:
                self.cache[word] = word in rejected

        return {
            word for word in words
            if self.cache.get(word)
            and word.lower() not in self.user_words
            and word.lower() not in self.ignored
        }

    def suggest(self, word):
        """Returns up to 8 suggested spellings for a word."""
        output = self.run("suggest", "^" + word + "\n")

        if not output:
            return []

        for line in output.splitlines():
            if line.startswith("&"):
                _, _, rest = line.partition(":")
                return [item.strip() for item in rest.split(",") if item.strip()][:8]

        return []


SPELLER = SpellChecker()


class ImageSourceParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.sources = []
        self.stylesheets = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attributes = {
            name.lower(): value
            for name, value in attrs
        }

        if tag.lower() == "img":
            source = attributes.get("src")
            if source:
                self.sources.append(source.strip())

        elif tag.lower() == "link":
            rel = attributes.get("rel", "")
            href = attributes.get("href")

            if href and "stylesheet" in rel.lower().split():
                self.stylesheets.append(href.strip())

        elif tag.lower() == "script":
            source = attributes.get("src")
            if source:
                self.scripts.append(source.strip())


class CodeEditor(QsciScintilla):

    def __init__(self, path=None):
        super().__init__()
        self.setAcceptDrops(True)

        self.path = Path(path).resolve() if path else None
        self.setUtf8(True)

        editor_font = QFont("Monospace", 10)
        settings = STATE_FILE

        try:
            data = json.loads(settings.read_text())
            saved_font = data.get("editor_font")
            apply_saved_font(editor_font, saved_font)
        except (OSError, ValueError):
            pass

        self.setFont(editor_font)

        # Line numbers / Bookmark margin
        self.setMarginType(1, QsciScintilla.MarginType.SymbolMargin)
        self.setMarginWidth(1, 12)
        self.setMarginSensitivity(1, True)

        self.bookmark_marker = self.markerDefine(
            QsciScintilla.MarkerSymbol.Circle
        )

        bookmark_pixmap = QPixmap(
            str(Path(__file__).resolve().parent / "icons" / "bookmark-triangle.png")
        )

        self.SendScintilla(
            QsciScintilla.SCI_MARKERDEFINEPIXMAP,
            self.bookmark_marker,
            bookmark_pixmap
        )
        
        try:
            data = json.loads(settings.read_text())
            saved_color = data.get("bookmark_color", "#ff0000")
        except (OSError, ValueError):
            saved_color = "#ff0000"

        self.bookmark_color = QColor(saved_color)
        self.setMarkerBackgroundColor(
            self.bookmark_color, self.bookmark_marker
        )

        self.setMarginMarkerMask(1, 1 << self.bookmark_marker)
        self.marginClicked.connect(self.toggle_bookmark)
        self.setMarginType(0, QsciScintilla.MarginType.NumberMargin)
        self.setMarginWidth(0, "0000")

        # Indentation
        try:
            indent_data = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            indent_data = {}

        use_tabs = bool(indent_data.get("indent_use_tabs", False))
        indent_width = indent_data.get("indent_width", 4)

        try:
            indent_width = int(indent_width)
        except (TypeError, ValueError):
            indent_width = 4

        indent_width = max(1, min(8, indent_width))

        self.setIndentationsUseTabs(use_tabs)
        self.setTabWidth(indent_width)
        self.setIndentationWidth(indent_width)
        self.setAutoIndent(True)

        # Highlight the current line
        self.setCaretLineVisible(True)

        try:
            line_data = json.loads(STATE_FILE.read_text())
            line_color_name = line_data.get("current_line_color", "#f7f7c6")
        except (OSError, ValueError):
            line_color_name = "#f7f7c6"

        line_color = QColor(line_color_name)
        if not line_color.isValid():
            line_color = QColor("#f7f7c6")

        self.current_line_color = line_color
        self.setCaretLineBackgroundColor(self.current_line_color)

        self.set_language()
        
        # Tag matching configuration & indicator setup
        self.tag_indicator = self.indicatorDefine(
            QsciScintilla.IndicatorStyle.RoundBoxIndicator
        )

        try:
            tag_data = json.loads(STATE_FILE.read_text())
            tag_color_name = tag_data.get("tag_pair_color", "#308cc6")
        except (OSError, ValueError):
            tag_color_name = "#308cc6"

        tag_color = QColor(tag_color_name)
        if not tag_color.isValid():
            tag_color = QColor("#308cc6")

        self.tag_pair_color = tag_color
        self.setIndicatorForegroundColor(
            self.tag_pair_color, self.tag_indicator
        )
        self.setIndicatorDrawUnder(True, self.tag_indicator)
        
        # Feed the swapped hex code so Scintilla flips it back to blue
        sel_bg = QColor("#c68c30")
        sel_fg = QColor("#ffffff")
        self.setSelectionForegroundColor(sel_fg)
        self.setSelectionBackgroundColor(sel_bg)
        self.SendScintilla(QsciScintilla.SCI_SETSELFORE, True, sel_fg.rgb())
        self.SendScintilla(QsciScintilla.SCI_SETSELBACK, True, sel_bg.rgb())

        self.cursorPositionChanged.connect(self.update_tag_matching)

        # Tag completion setting
        try:
            cfg = json.loads(STATE_FILE.read_text())
            self.tag_completion_enabled = bool(cfg.get("tag_completion_enabled", True))
        except (OSError, ValueError):
            self.tag_completion_enabled = True

        self.setAutoCompletionSource(QsciScintilla.AutoCompletionSource.AcsAll)
        self.setAutoCompletionThreshold(1)
        self.setAutoCompletionCaseSensitivity(False)
        self.setAutoCompletionReplaceWord(False)

        # Spell checking: red squiggle under misspelled words
        self.spell_ranges = []
        self.spell_indicator = self.indicatorDefine(
            QsciScintilla.IndicatorStyle.SquiggleIndicator
        )
        self.apply_spell_style()

        self.spell_timer = QTimer(self)
        self.spell_timer.setSingleShot(True)
        self.spell_timer.setInterval(700)
        self.spell_timer.timeout.connect(self.run_spell_check)
        self.textChanged.connect(lambda: self.spell_timer.start())

        if self.path:
            self.load_file(self.path)

    def has_active_selection(self):
        start = self.SendScintilla(QsciScintilla.SCI_GETSELECTIONSTART)
        end = self.SendScintilla(QsciScintilla.SCI_GETSELECTIONEND)
        return start != end

    def clear_tag_highlights(self):
        encoded_text = self.text().encode("utf-8")
        self.SendScintilla(QsciScintilla.SCI_SETINDICATORCURRENT, self.tag_indicator)
        self.SendScintilla(QsciScintilla.SCI_INDICATORCLEARRANGE, 0, len(encoded_text))

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if self.has_active_selection():
            self.clear_tag_highlights()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self.has_active_selection():
            self.clear_tag_highlights()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.has_active_selection():
            self.clear_tag_highlights()

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        
        if self.has_active_selection():
            self.clear_tag_highlights()

        if not getattr(self, "tag_completion_enabled", True):
            return

        if event.text() == '>':
            line, index = self.getCursorPosition()

            # Look back over the current line and up to 20 lines before it
            first_line = max(0, line - 20)
            text_up_to_pos = "".join(
                self.text(n) for n in range(first_line, line)
            ) + self.text(line)[:index]

            match = re.search(
                r'<([A-Za-z][\w:.-]*)(?:\s[^<>]*)?>$',
                text_up_to_pos
            )
            if match and not text_up_to_pos.endswith("/>"):
                tag_name = match.group(1).lower()
                void_tags = {
                    "area", "base", "br", "col", "embed", "hr", "img",
                    "input", "link", "meta", "param", "source", "track", "wbr"
                }
                if (
                    tag_name not in void_tags
                    and not self.tag_already_closed(tag_name)
                ):
                    self.insertAt(f"</{tag_name}>", line, index)
                    self.setCursorPosition(line, index)

    def tag_already_closed(self, tag_name):
        """True if the page already has as many closing </tag> as opening
        <tag>, so a tag being re-typed does not need another closing tag.

        This is what stops a closing tag being added again when you delete
        the > of an opening tag and type it back."""
        cleaned = re.sub(
            r"<!--.*?-->|<script\b.*?</script\s*>|<style\b.*?</style\s*>",
            "",
            self.text(),
            flags=re.DOTALL | re.IGNORECASE
        )

        name = re.escape(tag_name)

        # Opening tags, not counting self-closing ones such as <div/>
        opening = len(re.findall(
            r"<\s*" + name + r"(?=[\s>/])[^>]*(?<!/)>",
            cleaned,
            flags=re.IGNORECASE
        ))

        closing = len(re.findall(
            r"<\s*/\s*" + name + r"\s*>",
            cleaned,
            flags=re.IGNORECASE
        ))

        return closing >= opening

    def apply_spell_style(self):
        """Sets how misspelled words are marked (see SPELL_STYLES)."""
        style = SPELL_STYLES.get(SPELLER.style, SPELL_STYLES[DEFAULT_SPELL_STYLE])

        self.SendScintilla(
            QsciScintilla.SCI_INDICSETSTYLE, self.spell_indicator, style
        )
        self.setIndicatorForegroundColor(
            QColor("#e00000"), self.spell_indicator
        )

        # Transparency is only used by the pale box style
        self.SendScintilla(
            QsciScintilla.SCI_INDICSETALPHA, self.spell_indicator, 60
        )
        self.SendScintilla(
            QsciScintilla.SCI_INDICSETOUTLINEALPHA, self.spell_indicator, 140
        )

    def spell_applicable(self):
        """Spell check page text, not code files."""
        if not self.path:
            return True

        return self.path.suffix.lower() in (
            ".html", ".htm", ".xhtml", ".txt", ".md"
        )

    def run_spell_check(self):
        """Underlines misspelled words in the text of the page."""
        self.spell_ranges = []

        self.SendScintilla(
            QsciScintilla.SCI_SETINDICATORCURRENT, self.spell_indicator
        )
        self.SendScintilla(
            QsciScintilla.SCI_INDICATORCLEARRANGE,
            0,
            self.SendScintilla(QsciScintilla.SCI_GETLENGTH)
        )

        if not SPELLER.enabled or not self.spell_applicable():
            return

        if not SPELLER.available():
            return

        text = self.text()
        masked = SPELL_MASK.sub(lambda match: " " * len(match.group(0)), text)

        found = []
        for match in SPELL_WORD.finditer(masked):
            word = match.group(0).replace("\u2019", "'")

            # Skip single letters and ACRONYMS
            if len(word) < 2 or word.isupper():
                continue

            found.append((match.start(), match.end(), word))

        if not found:
            return

        bad = SPELLER.misspelled({word for _, _, word in found})

        if not bad:
            return

        ranges = []
        last_char = 0
        byte_position = 0

        for start, end, word in found:
            if word not in bad:
                continue

            byte_position += len(text[last_char:start].encode("utf-8"))
            length = len(text[start:end].encode("utf-8"))
            ranges.append((byte_position, byte_position + length, word))
            byte_position += length
            last_char = end

            if len(ranges) >= 2000:
                break

        self.SendScintilla(
            QsciScintilla.SCI_SETINDICATORCURRENT, self.spell_indicator
        )

        for start, end, word in ranges:
            self.SendScintilla(
                QsciScintilla.SCI_INDICATORFILLRANGE, start, end - start
            )

        self.spell_ranges = ranges

    def replace_byte_range(self, start, end, new_text):
        start_line, start_index = self.lineIndexFromPosition(start)
        end_line, end_index = self.lineIndexFromPosition(end)
        self.setSelection(start_line, start_index, end_line, end_index)
        self.replaceSelectedText(new_text)

    def recheck_spelling_everywhere(self):
        window = self.window()

        if hasattr(window, "recheck_all_spelling"):
            window.recheck_all_spelling()
        else:
            self.run_spell_check()

    def contextMenuEvent(self, event):
        # Build the whole right-click menu here rather than relying on
        # QScintilla's own menu, so the spelling items are always included
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        position = self.SendScintilla(
            QsciScintilla.SCI_POSITIONFROMPOINT,
            event.pos().x(),
            event.pos().y()
        )

        hit = None

        if position >= 0 and SPELLER.enabled and self.spell_applicable():
            self.run_spell_check()

            for start, end, word in self.spell_ranges:
                if start <= position <= end:
                    hit = (start, end, word)
                    break

        if hit:
            start, end, word = hit
            suggestions = SPELLER.suggest(word)

            if suggestions:
                for suggestion in suggestions:
                    action = menu.addAction(suggestion)
                    bold = action.font()
                    bold.setBold(True)
                    action.setFont(bold)
                    action.triggered.connect(
                        lambda checked=False, s=suggestion, a=start, b=end:
                            self.replace_byte_range(a, b, s)
                    )
            else:
                action = menu.addAction("(No suggestions)")
                action.setEnabled(False)

            menu.addSeparator()

            ignore_action = menu.addAction(f'Ignore "{word}"')
            ignore_action.triggered.connect(
                lambda checked=False, w=word: (
                    SPELLER.ignore_word(w),
                    self.recheck_spelling_everywhere()
                )
            )

            add_action = menu.addAction(f'Add "{word}" to dictionary')
            add_action.triggered.connect(
                lambda checked=False, w=word: (
                    SPELLER.add_word(w),
                    self.recheck_spelling_everywhere()
                )
            )

            menu.addSeparator()

        # The usual editing items
        undo_action = menu.addAction("Undo")
        undo_action.setEnabled(self.isUndoAvailable())
        undo_action.triggered.connect(lambda checked=False: self.undo())

        redo_action = menu.addAction("Redo")
        redo_action.setEnabled(self.isRedoAvailable())
        redo_action.triggered.connect(lambda checked=False: self.redo())

        menu.addSeparator()

        cut_action = menu.addAction("Cut")
        cut_action.setEnabled(self.hasSelectedText())
        cut_action.triggered.connect(lambda checked=False: self.cut())

        copy_action = menu.addAction("Copy")
        copy_action.setEnabled(self.hasSelectedText())
        copy_action.triggered.connect(lambda checked=False: self.copy())

        paste_action = menu.addAction("Paste")
        paste_action.triggered.connect(lambda checked=False: self.paste())

        menu.addSeparator()

        select_all_action = menu.addAction("Select All")
        select_all_action.triggered.connect(
            lambda checked=False: self.selectAll()
        )

        menu.popup(event.globalPos())

    def toggle_bookmark(self, margin, line, modifiers): 
        if margin != 1:
            return

        markers = self.markerFindNext(line, 1 << self.bookmark_marker)

        if markers == line:
            self.markerDelete(line, self.bookmark_marker)
        else:
            self.markerAdd(line, self.bookmark_marker)

    def set_bookmark_color(self, color):
        self.bookmark_color = QColor(color)
        self.setMarkerBackgroundColor(
            self.bookmark_color, self.bookmark_marker
        )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and Path(url.toLocalFile()).is_file():
                    event.acceptProposedAction()
                    return

        event.ignore()

    def dropEvent(self, event):
        image_extensions = {
            ".jpg", ".jpeg", ".png", ".gif",
            ".webp", ".svg", ".avif", ".bmp"
        }

        dropped_path = None

        for url in event.mimeData().urls():
            if url.isLocalFile():
                candidate = Path(url.toLocalFile())
                if candidate.is_file():
                    dropped_path = candidate.resolve()
                    break

        if dropped_path is None:
            event.ignore()
            return

        if not self.path or not self.path.exists():
            QMessageBox.warning(
                self,
                "Save file first",
                "Please save this file before inserting a reference.\n\n"
                "The relative path depends on the file's location."
            )
            event.ignore()
            return

        editor_extension = self.path.suffix.lower()

        if editor_extension not in (".html", ".htm", ".xhtml", ".css"):
            event.ignore()
            return

        relative_path = os.path.relpath(dropped_path, self.path.parent)
        relative_path = Path(relative_path).as_posix()

        if editor_extension == ".css":
            relative_path = relative_path.replace("\\", "\\\\").replace('"', '\\"')

            if dropped_path.suffix.lower() == ".css":
                tag = f'@import url("{relative_path}");'
            else:
                tag = f'url("{relative_path}")'

        elif dropped_path.suffix.lower() in image_extensions:
            image_src = html.escape(relative_path, quote=True)
            pixmap = QPixmap(str(dropped_path))

            if pixmap.isNull():
                tag = f'<img src="{image_src}">'
            else:
                width = pixmap.width()
                height = pixmap.height()
                tag = (
                    f'<img src="{image_src}" '
                    f'width="{width}" height="{height}" alt="">'
                )

        else:
            link_href = html.escape(relative_path, quote=True)
            link_text = html.escape(dropped_path.name)
            tag = f'<a href="{link_href}">{link_text}</a>'

        point = event.position().toPoint()
        position = self.SendScintilla(
            QsciScintilla.SCI_POSITIONFROMPOINT,
            point.x(),
            point.y()
        )

        if position < 0:
            position = self.SendScintilla(QsciScintilla.SCI_GETCURRENTPOS)

        line, index = self.lineIndexFromPosition(position)
        self.insertAt(tag, line, index)
        event.acceptProposedAction()

    def set_language(self):
        suffix = self.path.suffix.lower() if self.path else ".html"

        self.syntax_key = syntax_language_key(suffix)

        if self.syntax_key is None:
            self.setLexer(None)
            self.apply_brace_style()
            return

        lexer = new_syntax_lexer(self.syntax_key, self)

        try:
            data = json.loads(STATE_FILE.read_text())
            saved_font = data.get("editor_font")
        except (OSError, ValueError):
            saved_font = None

        font = QFont("Monospace", 10)
        apply_saved_font(font, saved_font)

        lexer.setFont(font, -1)
        self.setLexer(lexer)

        lexer.setFont(font, -1)
        self.setLexer(lexer)

        # Colours and bold settings chosen in Settings > Syntax highlighting
        self.apply_syntax_overrides(font)

        # Keep selection nice and blue
        self.setSelectionForegroundColor(QColor("#ffffff"))
        self.setSelectionBackgroundColor(QColor("#308cc6"))

        self.apply_brace_style()

    def apply_syntax_overrides(self, base_font):
        """Applies the syntax colours and bold settings you have chosen.

        base_font is the editor font; the bold styles are made from it."""
        lexer = self.lexer()

        if lexer is None or not getattr(self, "syntax_key", None):
            return

        styles = load_syntax_overrides().get(self.syntax_key, {})

        for number, entry in styles.items():
            if not lexer.description(number):
                continue

            if entry["color"]:
                lexer.setColor(QColor(entry["color"]), number)

            style_font = QFont(base_font)
            style_font.setBold(entry["bold"])
            lexer.setFont(style_font, number)

    def apply_brace_style(self):
        """Highlights the bracket next to the cursor and its partner.

        Works for ( ) [ ] { }. A bracket with no partner is marked in red."""
        self.setBraceMatching(QsciScintilla.BraceMatch.SloppyBraceMatch)

        self.setMatchedBraceForegroundColor(QColor("#000000"))
        self.setMatchedBraceBackgroundColor(QColor("#a8e0a8"))
        self.setUnmatchedBraceForegroundColor(QColor("#ffffff"))
        self.setUnmatchedBraceBackgroundColor(QColor("#e05050"))

    def update_tag_matching(self, line, index):
        if self.has_active_selection():
            self.clear_tag_highlights()
            return

        text = self.text()
        encoded_text = text.encode("utf-8")

        cursor_byte = self.SendScintilla(QsciScintilla.SCI_GETCURRENTPOS)

        self.clear_tag_highlights()

        pattern = re.compile(
            r"<!--.*?-->|"
            r"<\s*(/?)\s*([A-Za-z][\w:.-]*)\b[^>]*>",
            re.DOTALL,
        )

        void_tags = {
            "area", "base", "br", "col", "embed", "hr", "img",
            "input", "link", "meta", "param", "source", "track",
            "wbr",
        }

        tags = []

        for match in pattern.finditer(text):
            token = match.group(0)

            if token.startswith("<!--"):
                continue

            name = match.group(2).lower()
            closing = bool(match.group(1))
            self_closing = (
                token.rstrip().endswith("/>")
                or name in void_tags
            )

            tags.append({
                "start_byte": len(text[:match.start()].encode("utf-8")),
                "end_byte": len(text[:match.end()].encode("utf-8")),
                "line": text.count("\n", 0, match.start()) + 1,
                "name": name,
                "closing": closing,
                "self_closing": self_closing,
            })

        current = None

        for i, tag in enumerate(tags):
            if tag["start_byte"] <= cursor_byte <= tag["end_byte"]:
                current = i
                break

        if current is None:
            return

        tag = tags[current]
        name = tag["name"]

        if tag["self_closing"]:
            return

        partner = None
        depth = 0

        if tag["closing"]:
            for i in range(current - 1, -1, -1):
                candidate = tags[i]
                if candidate["name"] != name or candidate["self_closing"]:
                    continue
                if candidate["closing"]:
                    depth += 1
                elif depth:
                    depth -= 1
                else:
                    partner = i
                    break
        else:
            for i in range(current + 1, len(tags)):
                candidate = tags[i]
                if candidate["name"] != name or candidate["self_closing"]:
                    continue
                if not candidate["closing"]:
                    depth += 1
                elif depth:
                    depth -= 1
                else:
                    partner = i
                    break

        if partner is None:
            return

        self.fill_tag(tag)
        self.fill_tag(tags[partner])

    def fill_tag(self, tag):
        if self.has_active_selection():
            return

        self.SendScintilla(
            QsciScintilla.SCI_SETINDICATORCURRENT,
            self.tag_indicator,
        )
        self.SendScintilla(
            QsciScintilla.SCI_INDICATORFILLRANGE,
            tag["start_byte"],
            tag["end_byte"] - tag["start_byte"],
        )

    def load_file(self, path):
        self.path = Path(path).resolve()
        self.set_language()

        content = self.path.read_text(encoding="utf-8", errors="replace")

        self.setText(content)
        self.setModified(False)

    def save_file(self, path=None):
        if path:
            self.path = Path(path).resolve()

        if not self.path:
            return False

        self.path.write_text(self.text(), encoding="utf-8")
        self.setModified(False)
        return True


class ClickableOutput(QTextEdit):
    """Read-only output box. Clicking a line that mentions
    'line N column M' (as HTML Tidy writes it) jumps to that spot."""

    def __init__(self, parent, jump_callback):
        super().__init__(parent)
        self.jump_callback = jump_callback
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)

        if event.button() != Qt.MouseButton.LeftButton:
            return

        # Do nothing if the user was selecting text to copy
        if self.textCursor().hasSelection():
            return

        cursor = self.cursorForPosition(event.position().toPoint())
        line_text = cursor.block().text()

        match = re.search(
            r"line\s+(\d+)(?:\s+column\s+(\d+))?",
            line_text,
            re.IGNORECASE
        )

        if match:
            line_number = int(match.group(1))
            column = int(match.group(2)) if match.group(2) else 1
            self.jump_callback(line_number, column)


class FindReplaceDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.last_search = ""
        self.last_editor = None
        self.last_direction = None

        self.setWindowTitle("Find and Replace")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)

        find_row = QHBoxLayout()
        find_row.addWidget(QLabel("Find what:", self))
        self.find_input = QLineEdit(self)
        find_row.addWidget(self.find_input)
        layout.addLayout(find_row)

        replace_row = QHBoxLayout()
        replace_row.addWidget(QLabel("Replace with:", self))
        self.replace_input = QLineEdit(self)
        replace_row.addWidget(self.replace_input)
        layout.addLayout(replace_row)

        self.all_documents_box = QCheckBox("Search all open documents", self)
        layout.addWidget(self.all_documents_box)

        self.match_count_label = QLabel("Matches: 0", self)
        layout.addWidget(self.match_count_label)

        button_row = QHBoxLayout()

        self.find_previous_button = QPushButton("Find Previous", self)
        self.find_next_button = QPushButton("Find Next", self)
        self.replace_next_button = QPushButton("Replace Next", self)
        self.replace_all_button = QPushButton("Replace All", self)
        self.close_button = QPushButton("Close", self)

        button_row.addWidget(self.find_previous_button)
        button_row.addWidget(self.find_next_button)
        button_row.addWidget(self.replace_next_button)
        button_row.addWidget(self.replace_all_button)
        button_row.addWidget(self.close_button)

        layout.addLayout(button_row)

        self.find_input.textChanged.connect(self.search_text_changed)
        self.all_documents_box.toggled.connect(self.search_text_changed)
        self.find_previous_button.clicked.connect(lambda: self.find_previous())
        self.find_next_button.clicked.connect(lambda: self.find_next())
        self.replace_next_button.clicked.connect(lambda: self.replace_next())
        self.replace_all_button.clicked.connect(lambda: self.replace_all())
        self.close_button.clicked.connect(self.close)

        # Keep the match count right when you change tab
        owner.tabs.currentChanged.connect(
            lambda *_: self.update_match_count()
        )

    def all_documents(self):
        return self.all_documents_box.isChecked()

    def editors_in_scope(self):
        """The documents to search: all open ones, or just the current one."""
        tabs = self.owner.tabs

        if self.all_documents():
            return [tabs.widget(index) for index in range(tabs.count())]

        editor = self.owner.editor()
        return [editor] if editor else []

    def document_name(self, editor):
        index = self.owner.tabs.indexOf(editor)
        name = self.owner.tabs.tabText(index) if index != -1 else "Untitled"
        return name.lstrip("*")

    def count_in_editor(self, editor, search_text):
        if not editor or not search_text:
            return 0

        return len(
            re.findall(re.escape(search_text), editor.text(), re.IGNORECASE)
        )

    def count_matches(self, search_text):
        return sum(
            self.count_in_editor(editor, search_text)
            for editor in self.editors_in_scope()
        )

    def update_match_count(self):
        search_text = self.find_input.text()
        editors = self.editors_in_scope()
        counts = [
            self.count_in_editor(editor, search_text) for editor in editors
        ]
        total = sum(counts)

        if self.all_documents():
            with_matches = sum(1 for count in counts if count)
            self.match_count_label.setText(
                f"Matches: {total} (in {with_matches} of "
                f"{len(editors)} open documents)"
            )
        else:
            self.match_count_label.setText(f"Matches: {total}")

    def search_text_changed(self, *_):
        self.last_search = ""
        self.update_match_count()

    def search_in_editor(self, editor, search_text, forward, from_cursor, wrap):
        """Looks for the next match in one document.

        from_cursor=True starts at the cursor (or at the end or start of the
        match just found); False starts at the top of the document when
        searching forward, or at the bottom when searching backward."""
        if from_cursor:
            continuing = (
                editor is self.last_editor
                and search_text == self.last_search
            )

            if (
                continuing
                and editor.hasSelectedText()
                and editor.selectedText().casefold() == search_text.casefold()
            ):
                line_from, index_from, line_to, index_to = editor.getSelection()
                if forward:
                    start_line, start_index = line_to, index_to
                else:
                    start_line, start_index = line_from, index_from
            else:
                start_line, start_index = editor.getCursorPosition()

        elif forward:
            start_line, start_index = 0, 0

        else:
            start_line = max(0, editor.lines() - 1)
            start_index = len(editor.text(start_line))

        return editor.findFirst(
            search_text, False, False, False, wrap, forward,
            start_line, start_index
        )

    def find_in_direction(self, forward):
        search_text = self.find_input.text()
        editor = self.owner.editor()

        if not editor or not search_text:
            return False

        found_editor = None

        if not self.all_documents():
            if self.search_in_editor(editor, search_text, forward, True, True):
                found_editor = editor

        else:
            tabs = self.owner.tabs
            count = tabs.count()
            start = tabs.currentIndex()

            # 1. The rest of this document
            if self.search_in_editor(editor, search_text, forward, True, False):
                found_editor = editor

            # 2. The other open documents, in tab order
            if found_editor is None:
                for step in range(1, count):
                    index = (start + step if forward else start - step) % count
                    other = tabs.widget(index)

                    if self.search_in_editor(
                        other, search_text, forward, False, False
                    ):
                        tabs.setCurrentIndex(index)
                        found_editor = other
                        break

            # 3. Round to the beginning (or end) of this document again
            if found_editor is None:
                if self.search_in_editor(
                    editor, search_text, forward, False, False
                ):
                    found_editor = editor

        if found_editor is not None:
            self.last_editor = found_editor
            self.last_search = search_text
            self.last_direction = "forward" if forward else "backward"

        return found_editor is not None

    def find_next_match(self):
        return self.find_in_direction(True)

    def not_found_text(self, search_text, previous=False):
        where = " in the open documents" if self.all_documents() else ""
        word = "No previous match" if previous else "No match"
        return f"{word} found for '{search_text}'{where}."

    def find_next(self):
        search_text = self.find_input.text()
        if not search_text:
            return

        if not self.find_next_match():
            QMessageBox.information(
                self, "Find Next", self.not_found_text(search_text)
            )

    def find_previous(self):
        search_text = self.find_input.text()
        if not search_text:
            return

        if not self.find_in_direction(False):
            QMessageBox.information(
                self, "Find Previous",
                self.not_found_text(search_text, previous=True)
            )

    def replace_next(self):
        search_text = self.find_input.text()
        editor = self.owner.editor()

        if not editor or not search_text:
            return

        if editor.selectedText().casefold() != search_text.casefold():
            if not self.find_next_match():
                QMessageBox.information(
                    self, "Replace Next", self.not_found_text(search_text)
                )
                return

            # The match may be in another tab now
            editor = self.owner.editor()

        editor.replaceSelectedText(self.replace_input.text())
        self.last_editor = editor
        self.last_search = search_text
        self.update_match_count()

        # Move on to the next match, ready for the next click
        if not self.find_next_match():
            self.last_search = ""

    def replace_all_in_editor(self, editor, search_text, replacement_text):
        count = 0

        editor.beginUndoAction()
        try:
            editor.setCursorPosition(0, 0)
            found = editor.findFirst(search_text, False, False, False, False, True)

            while found:
                editor.replace(replacement_text)
                count += 1
                found = editor.findNext()
        finally:
            editor.endUndoAction()

        return count

    def replace_all(self):
        search_text = self.find_input.text()
        editors = self.editors_in_scope()

        if not editors or not search_text:
            return

        count_before = self.count_matches(search_text)

        if count_before == 0:
            QMessageBox.information(self, "Replace All", "No matches found.")
            return

        if self.all_documents():
            answer = QMessageBox.question(
                self,
                "Replace All",
                f"Replace {count_before} occurrence(s) of '{search_text}' "
                f"in {len(editors)} open document(s)?\n\n"
                "You can undo it in each document separately."
            )

            if answer != QMessageBox.StandardButton.Yes:
                return

        replacement_text = self.replace_input.text()
        results = []

        for editor in editors:
            count = self.replace_all_in_editor(
                editor, search_text, replacement_text
            )

            if count:
                results.append((self.document_name(editor), count))

        self.last_editor = self.owner.editor()
        self.last_search = search_text
        self.update_match_count()

        total = sum(count for _, count in results)

        if self.all_documents():
            lines = "\n".join(
                f"{name}: {count}" for name, count in results[:15]
            )

            if len(results) > 15:
                lines += f"\n... and {len(results) - 15} more"

            message = (
                f"Replaced {total} occurrence(s) in "
                f"{len(results)} document(s):\n\n{lines}"
            )
        else:
            message = f"Replaced {total} occurrence(s)."

        QMessageBox.information(self, "Replace All", message)


class TelaEditIconProvider(QFileIconProvider):

    def icon(self, info):
        if info.isDir():
            icon_path = (
                Path(__file__).resolve().parent / "icons" / "files" / "folder.png"
            )
            if icon_path.is_file():
                return QIcon(str(icon_path))
            return super().icon(info)

        suffix = info.suffix().lower()
        filename = info.fileName().lower()

        special_files = {
            ".htaccess": "htaccess",
            "robots.txt": "robots",
        }

        mime_icons = {
            "html": "html", "htm": "html", "xhtml": "html", "css": "css",
            "js": "js", "mjs": "js", "json": "json", "jpg": "image-file",
            "jpeg": "image-file", "png": "image-file", "gif": "image-file",
            "bmp": "image-file", "webp": "image-file", "svg": "svg",
            "ico": "image-file", "txt": "text", "md": "markdown", "php": "php",
            "asp": "binary", "aspx": "binary", "cfm": "binary", "jsp": "binary",
            "pl": "shell", "rb": "binary", "sql": "config", "py": "python",
            "vbs": "shell", "swf": "binary", "zip": "zip", "mp3": "audio",
            "wav": "audio", "ogg": "audio", "mp4": "video", "avi": "video",
            "mkv": "video", "xml": "xml", "sh": "shell", "exe": "exe",
            "pdf": "pdf", "ttf": "font", "otf": "font", "conf": "config",
            "ini": "config",
        }

        icon_name = special_files.get(filename)
        if icon_name is None:
            icon_name = mime_icons.get(suffix, "file-generic")

        icon_path = (
            Path(__file__).resolve().parent / "icons" / "files" / f"{icon_name}.png"
        )

        if icon_path.is_file():
            return QIcon(str(icon_path))

        return super().icon(info)


class FolderStateIconDelegate(QStyledItemDelegate):
    """Show open and closed icons for folders in the folder tree."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view

        icon_directory = Path(__file__).resolve().parent / "icons" / "files"

        self.closed_icon = QIcon(str(icon_directory / "folder.png"))
        self.open_icon = QIcon(str(icon_directory / "folder-open.png"))

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        model = index.model()

        if index.column() == 0 and model.isDir(index):
            if self.view.isExpanded(index):
                option.icon = self.open_icon
            else:
                option.icon = self.closed_icon


class DraggableFileSystemModel(QFileSystemModel):
    """Custom QFileSystemModel that explicitly enables dragging for files."""
    def flags(self, index):
        default_flags = super().flags(index)
        if index.isValid() and not self.isDir(index):
            return default_flags | Qt.ItemFlag.ItemIsDragEnabled
        return default_flags


class FilesOnlyProxyModel(QSortFilterProxyModel):
    """Used by the lower Explorer panel to show only the files of one folder.

    Folders are hidden, except the chosen folder itself and the folders
    above it: those have to stay in the model, otherwise the list could not
    be pointed at the chosen folder."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.folder = ""

    def set_folder(self, folder):
        self.folder = str(folder).rstrip("/")
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)

        if not model.isDir(index):
            return True

        path = model.filePath(index).rstrip("/")
        return self.folder == path or self.folder.startswith(path + "/")


def external_program_env():
    """The environment for programs TelaEdit starts (such as the file
    manager). The standalone build changes LD_LIBRARY_PATH so it can find its
    own libraries. Other programs must get the original value back, or they
    can fail to start."""
    env = dict(os.environ)

    if getattr(sys, "frozen", False):
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)

        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original

    return env


class TelaEdit(QMainWindow):

    def __init__(self):
        super().__init__()

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        SNIPPETS_DIR.mkdir(parents=True, exist_ok=True)

        self.setWindowTitle("TelaEdit")
        icon_path = os.path.join(os.path.dirname(__file__), "icons", "telaedit-icon.png")
        self.setWindowIcon(QIcon(icon_path))   
        window_settings = STATE_FILE

        try:
            window_data = json.loads(window_settings.read_text())
        except (OSError, ValueError):
            window_data = {}

        geometry = window_data.get("window_geometry", {})

        self.resize(
            geometry.get("width", 1100),
            geometry.get("height", 750)
        )

        if "x" in geometry and "y" in geometry:
            self.move(geometry["x"], geometry["y"])

        self.restore_maximized = window_data.get("window_maximized", False)
        self.saved_explorer_width = window_data.get("explorer_width", 250)
        self.saved_explorer_split = window_data.get("explorer_split", [])
        
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.currentChanged.connect(self.update_title)
        self.tabs.currentChanged.connect(self.update_cursor_position)
        self.tabs.tabCloseRequested.connect(self.close_tab)

        self.setCentralWidget(self.tabs)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.position_label = QLabel("Line 1, Column 1", self)
        self.status.addPermanentWidget(self.position_label)        
        self.last_directory = self.load_last_directory()
        self.recent_files = self.load_recent_files()        
        self.create_file_panel()
        self.create_actions()
        self.setup_tidy_tool()
        self.external_tool_entries = self.load_external_tools()
        self.toolbar_icon_size = self.load_toolbar_icon_size()
        self.create_output_panel()
        self.create_menus()
        self.create_toolbar()

        self.spell_error_shown = False
        SPELLER.on_error = self.spell_error
        self.register_snippet_shortcuts()
        self.restore_session()

        if SPELLER.enabled and not SPELLER.available():
            self.status.showMessage(
                "Spell checking is on, but aspell or hunspell is not "
                "installed (sudo apt install aspell aspell-en).", 10000
            )

        if SETTINGS_MIGRATED:
            self.status.showMessage(
                "Your settings were copied from the old SiteForge folder "
                "(~/.config/SiteForge). That folder has been left as it was.",
                15000
            )

    def create_file_panel(self):
        self.explorer_tabs = QTabWidget()

        # The folder the lower panel is showing the files of
        self.explorer_folder = ""

        # One icon provider for both panels (kept here so it stays alive)
        self.icon_provider = TelaEditIconProvider()

        # ---- Upper panel: the folders ------------------------------------
        self.up_folder_button = QPushButton("↑ Up")
        self.up_folder_button.setToolTip("Go to the parent folder")
        self.up_folder_button.clicked.connect(self.navigate_up_folder)

        # Shows the full path of the chosen folder. A long path is clipped
        # rather than making the whole panel wider.
        self.folder_path_label = QLabel()
        self.folder_path_label.setMinimumWidth(10)

        self.folder_model = QFileSystemModel(self)
        self.folder_model.setFilter(
            QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot
        )
        self.folder_model.setRootPath(self.last_directory)
        self.folder_model.setIconProvider(self.icon_provider)

        self.folder_tree = QTreeView()
        self.folder_tree.setModel(self.folder_model)
        self.folder_tree.setItemDelegate(
            FolderStateIconDelegate(self.folder_tree)
        )
        self.folder_tree.setStyleSheet("QTreeView { font-size: 11pt; }")
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.setIconSize(QSize(24, 24))
        self.folder_tree.setUniformRowHeights(True)

        for column in range(1, 4):
            self.folder_tree.hideColumn(column)

        # Let the folder column grow to fit deep folders (a scroll bar
        # appears instead of the names being cut off)
        self.folder_tree.header().setStretchLastSection(False)
        self.folder_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )

        # Clicking a folder shows its files in the lower panel
        self.folder_tree.selectionModel().currentChanged.connect(
            self.on_folder_selected
        )
        self.folder_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.folder_tree.customContextMenuRequested.connect(
            self.folder_tree_menu
        )

        folder_panel = QWidget()
        folder_layout = QVBoxLayout(folder_panel)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.setSpacing(4)
        folder_layout.addWidget(self.up_folder_button)
        folder_layout.addWidget(self.folder_path_label)
        folder_layout.addWidget(self.folder_tree)

        # ---- Lower panel: the files of the chosen folder -----------------
        self.file_model = DraggableFileSystemModel(self)
        self.file_model.setFilter(
            QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot
        )
        self.file_model.setRootPath(self.last_directory)
        self.file_model.setIconProvider(self.icon_provider)
        self.file_model.directoryLoaded.connect(self.on_file_directory_loaded)

        # The proxy hides the sub-folders, so only files are listed
        self.file_proxy = FilesOnlyProxyModel(self)
        self.file_proxy.setSourceModel(self.file_model)

        self.file_list = QTreeView()
        self.file_list.setModel(self.file_proxy)
        self.file_list.setStyleSheet("QTreeView { font-size: 11pt; }")
        self.file_list.setHeaderHidden(True)
        self.file_list.setRootIsDecorated(False)
        self.file_list.setIconSize(QSize(24, 24))
        self.file_list.setUniformRowHeights(True)

        # Dragging a file into the editor inserts a link, image and so on
        self.file_list.setDragEnabled(True)
        self.file_list.setDragDropMode(QTreeView.DragDropMode.DragOnly)

        for column in range(1, 4):
            self.file_list.hideColumn(column)

        # Double-clicking a file opens it
        self.file_list.doubleClicked.connect(self.open_tree_file)
        self.file_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.file_list.customContextMenuRequested.connect(
            self.file_list_menu
        )

        # ---- The two panels, with a divider you can drag -----------------
        self.explorer_splitter = QSplitter(Qt.Orientation.Vertical)
        self.explorer_splitter.addWidget(folder_panel)
        self.explorer_splitter.addWidget(self.file_list)
        self.explorer_splitter.setChildrenCollapsible(False)
        self.explorer_splitter.setHandleWidth(6)
        self.explorer_splitter.setStretchFactor(0, 1)
        self.explorer_splitter.setStretchFactor(1, 1)
        self.explorer_splitter.setStyleSheet(
            "QSplitter::handle { background: #c4c4c4; }"
            "QSplitter::handle:hover { background: #8fb4d8; }"
        )

        saved = self.saved_explorer_split

        if (
            isinstance(saved, list) and len(saved) == 2
            and all(isinstance(size, int) and size > 0 for size in saved)
        ):
            self.explorer_splitter.setSizes(saved)
        else:
            self.explorer_splitter.setSizes([1, 1])

        self.file_dock = QDockWidget("Explorer", self)
        dock = self.file_dock
        dock.setObjectName("FilesDock")
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)

        title_bar = QWidget(dock)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(6, 2, 4, 2)

        title_label = QLabel("Explorer", title_bar)
        title_layout.addWidget(title_label)
        title_layout.addStretch()

        collapse_button = QToolButton(title_bar)
        collapse_button.setArrowType(Qt.ArrowType.LeftArrow)
        collapse_button.setToolTip("Hide Explorer")
        collapse_button.setAutoRaise(True)
        collapse_button.clicked.connect(self.toggle_explorer_panel)
        title_layout.addWidget(collapse_button)

        dock.setTitleBarWidget(title_bar)
        self.explorer_tabs.addTab(self.explorer_splitter, "Explorer")

        # Snippets panel with manage button and QTreeWidget matching Bluefish structure
        self.snippets_panel = QWidget()
        snippets_layout = QVBoxLayout(self.snippets_panel)
        snippets_layout.setContentsMargins(0, 0, 0, 0)
        snippets_layout.setSpacing(4)

        manage_snippets_btn = QPushButton("Manage Snippets...")
        manage_snippets_btn.clicked.connect(self.manage_snippets)
        snippets_layout.addWidget(manage_snippets_btn)

        self.snippet_list = QTreeWidget()
        self.snippet_list.setHeaderHidden(True)
        self.snippet_list.itemDoubleClicked.connect(self.insert_selected_snippet)
        snippets_layout.addWidget(self.snippet_list)

        self.explorer_tabs.addTab(self.snippets_panel, "Snippets")

        dock.setWidget(self.explorer_tabs)
        self.refresh_snippet_list()
        
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.resizeDocks([dock], [self.saved_explorer_width], Qt.Orientation.Horizontal)

        # Create the expand button toolbar on the left edge (hidden by default)
        self.expand_toolbar = QToolBar("Expand Explorer", self)
        self.expand_toolbar.setMovable(False)
        self.expand_explorer_btn = QToolButton(self)
        self.expand_explorer_btn.setArrowType(Qt.ArrowType.RightArrow)
        self.expand_explorer_btn.setToolTip("Expand Explorer")
        self.expand_explorer_btn.setAutoRaise(True)
        self.expand_explorer_btn.clicked.connect(self.toggle_explorer_panel)
        self.expand_toolbar.addWidget(self.expand_explorer_btn)
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, self.expand_toolbar)
        self.expand_toolbar.hide()

        dock.visibilityChanged.connect(self.on_dock_visibility_changed)

        # Start in the folder you were last working in
        self.go_to_folder(self.last_directory)

    def toggle_explorer_panel(self):
        """Toggles the Explorer panel open and closed."""
        if self.file_dock.isVisible():
            self.file_dock.hide()
            self.expand_toolbar.show()
        else:
            self.file_dock.show()
            self.expand_toolbar.hide()

    def on_dock_visibility_changed(self, visible):
        """Ensures the expand button stays synchronized if visibility changes."""
        if visible:
            self.expand_toolbar.hide()
        else:
            self.expand_toolbar.show()

    def navigate_up_folder(self):
        """The Up button: go to the parent of the folder being shown."""
        if not self.explorer_folder:
            return

        current = Path(self.explorer_folder)
        parent = current.parent

        if parent == current:
            return

        self.go_to_folder(str(parent))

    def go_to_folder(self, path):
        """Shows a folder in the Explorer: opens the folders above it in the
        upper panel, selects it, and lists its files in the lower panel."""
        path = str(Path(path))

        if not Path(path).is_dir():
            return

        index = self.folder_model.index(path)

        if not index.isValid():
            return

        # Already showing this folder: leave the panels as they are
        if path == self.explorer_folder and self.folder_tree.currentIndex() == index:
            return

        # Open every folder above this one, starting from the top
        parents = []
        parent = index.parent()

        while parent.isValid():
            parents.append(parent)
            parent = parent.parent()

        for parent in reversed(parents):
            self.folder_tree.expand(parent)

        self.folder_tree.setCurrentIndex(index)
        self.folder_tree.scrollTo(index)
        self.show_folder_files(path)

        # The folders above are still loading, which moves things about,
        # so scroll again a moment later
        QTimer.singleShot(250, self.scroll_folder_tree_to_current)

    def scroll_folder_tree_to_current(self):
        if not self.explorer_folder:
            return

        index = self.folder_model.index(self.explorer_folder)

        if index.isValid():
            self.folder_tree.scrollTo(
                index, QTreeView.ScrollHint.PositionAtCenter
            )

    def on_folder_selected(self, current, previous=None):
        """A folder was clicked in the upper panel."""
        if not current.isValid():
            return

        path = self.folder_model.filePath(current)

        if Path(path).is_dir():
            self.show_folder_files(path)

    def show_folder_files(self, path):
        """Lists the files of a folder in the lower panel."""
        path = str(Path(path))

        if path == self.explorer_folder or not Path(path).is_dir():
            return

        self.explorer_folder = path
        self.file_proxy.set_folder(path)
        self.file_model.setRootPath(path)

        source_index = self.file_model.index(path)
        self.file_list.setRootIndex(self.file_proxy.mapFromSource(source_index))

        self.folder_path_label.setText(path)
        self.folder_path_label.setToolTip(path)
        self.remember_directory(path)

        # The folder may not have finished loading yet; check again shortly
        QTimer.singleShot(300, self.refresh_file_list_root)

    def refresh_file_list_root(self):
        """Points the lower panel at the chosen folder. Called again once the
        folder has loaded, in case the first attempt came too early."""
        if not self.explorer_folder:
            return

        index = self.file_proxy.mapFromSource(
            self.file_model.index(self.explorer_folder)
        )

        if index.isValid() and index != self.file_list.rootIndex():
            self.file_list.setRootIndex(index)

    def on_file_directory_loaded(self, path):
        if path == self.explorer_folder:
            self.refresh_file_list_root()

    def open_in_file_manager(self, folder):
        """Opens a folder in the system's file manager."""
        folder = Path(folder)

        if folder.is_file():
            folder = folder.parent

        if not folder.is_dir():
            self.status.showMessage("That folder no longer exists.", 5000)
            return

        opener = shutil.which("xdg-open")

        if opener:
            try:
                subprocess.Popen(
                    [opener, str(folder)],
                    env=external_program_env(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                return
            except OSError:
                pass

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(
                self, "TelaEdit",
                f"Could not open the file manager for:\n{folder}"
            )

    def folder_tree_menu(self, position):
        """Right-click menu in the upper (folders) panel."""
        index = self.folder_tree.indexAt(position)

        if index.isValid():
            folder = self.folder_model.filePath(index)
        else:
            folder = self.explorer_folder

        if not folder:
            return

        menu = QMenu(self)
        action = menu.addAction("Open in file manager")
        action.triggered.connect(
            lambda checked=False, f=folder: self.open_in_file_manager(f)
        )
        menu.exec(self.folder_tree.viewport().mapToGlobal(position))

    def file_list_menu(self, position):
        """Right-click menu in the lower (files) panel."""
        index = self.file_list.indexAt(position)
        menu = QMenu(self)

        if index.isValid():
            path = self.file_model.filePath(self.file_proxy.mapToSource(index))
            open_action = menu.addAction("Open")
            open_action.triggered.connect(
                lambda checked=False, p=path: self.open_path(p)
            )
            menu.addSeparator()
            label = "Open folder in file manager"
        else:
            label = "Open in file manager"

        if self.explorer_folder:
            manager_action = menu.addAction(label)
            manager_action.triggered.connect(
                lambda checked=False, f=self.explorer_folder:
                    self.open_in_file_manager(f)
            )

        if not menu.isEmpty():
            menu.exec(self.file_list.viewport().mapToGlobal(position))

    def load_last_directory(self):
        settings = STATE_FILE

        try:
            data = json.loads(settings.read_text())
            directory = Path(data["last_directory"])

            if directory.is_dir():
                return str(directory)

        except (OSError, ValueError, KeyError, TypeError):
            pass

        default = Path.home() / "_WEBDEV"

        if default.is_dir():
            return str(default)

        return str(Path.home())

    def remember_directory(self, directory):
        directory = Path(directory)

        if directory.is_file():
            directory = directory.parent

        if not directory.is_dir():
            return

        self.last_directory = str(directory)
        settings = STATE_FILE

        try:
            data = json.loads(settings.read_text())
        except (OSError, ValueError):
            data = {}

        data["last_directory"] = self.last_directory

        try:
            settings.write_text(json.dumps(data, indent=4))
        except OSError:
            pass

    def load_recent_files(self):
        settings = STATE_FILE

        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

        recent = data.get("recent_files", [])

        if not isinstance(recent, list):
            return []

        return [
            str(Path(path).resolve())
            for path in recent
            if isinstance(path, str)
            and path
            and Path(path).is_file()
        ][:10]

    def save_recent_files(self):
        settings = STATE_FILE

        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}

        data["recent_files"] = self.recent_files

        try:
            settings.write_text(
                json.dumps(data, indent=4),
                encoding="utf-8"
            )
        except OSError:
            pass

    def add_recent_file(self, path):
        target = str(Path(path).resolve())

        self.recent_files = [
            target
        ] + [
            item for item in self.recent_files
            if item != target and Path(item).is_file()
        ]

        self.recent_files = self.recent_files[:10]
        self.save_recent_files()
        self.update_recent_files_menu()

    def update_recent_files_menu(self):
        if not hasattr(self, "recent_files_menu"):
            return

        self.recent_files_menu.clear()

        if not self.recent_files:
            action = self.recent_files_menu.addAction(
                "(No recent files)"
            )
            action.setEnabled(False)
            return

        for path in self.recent_files:
            action = self.recent_files_menu.addAction(path)
            action.triggered.connect(
                lambda checked=False, filename=path:
                    self.open_recent_file(filename)
            )

    def open_recent_file(self, path):
        if not Path(path).is_file():
            self.recent_files = [
                item for item in self.recent_files
                if item != path
            ]
            self.save_recent_files()
            self.update_recent_files_menu()
            QMessageBox.information(
                self,
                "Recent Files",
                "That file no longer exists."
            )
            return

        self.open_path(path)

    def save_session(self):
        settings = STATE_FILE

        try:
            data = json.loads(settings.read_text())
        except (OSError, ValueError):
            data = {}

        open_files = []
        cursor_positions = {}
        bookmarks = {}
        active_index = self.tabs.currentIndex()

        for index in range(self.tabs.count()):
            editor = self.tabs.widget(index)

            if editor.path:
                file_path = str(editor.path.resolve())
                open_files.append(file_path)
                line, column = editor.getCursorPosition()
                cursor_positions[file_path] = [line, column]

                bookmark_lines = []
                line = editor.markerFindNext(
                    0, 1 << editor.bookmark_marker
                )

                while line != -1:
                    bookmark_lines.append(line)
                    line = editor.markerFindNext(
                        line + 1, 1 << editor.bookmark_marker
                    )

                bookmarks[file_path] = bookmark_lines

            else:
                open_files.append(None)

        data["open_files"] = open_files
        data["cursor_positions"] = cursor_positions
        data["active_tab"] = active_index
        data["bookmarks"] = bookmarks
        data["word_wrap"] = self.actions["wordwrap"].isChecked()
        data["tag_completion_enabled"] = self.actions["tag_completion"].isChecked()
        data["spell_check_enabled"] = self.actions["spellcheck"].isChecked()
        data["spell_language"] = SPELLER.language
        data["spell_style"] = SPELLER.style
        
        geometry = (
            self.normalGeometry()
            if self.isMaximized()
            else self.geometry()
        )

        data["window_geometry"] = {
            "x": geometry.x(),
            "y": geometry.y(),
            "width": geometry.width(),
            "height": geometry.height(),
        }
        data["window_maximized"] = self.isMaximized()
        data["explorer_width"] = self.file_dock.width()
        data["explorer_split"] = self.explorer_splitter.sizes()
        
        try:
            settings.write_text(json.dumps(data, indent=4))
        except OSError:
            pass

    def restore_session(self):
        data = {}
        settings = STATE_FILE

        try:
            data = json.loads(settings.read_text())
            open_files = data.get("open_files", [])
            cursor_positions = data.get("cursor_positions", {})
            bookmarks = data.get("bookmarks", {})
            active_index = data.get("active_tab", 0)
        except (OSError, ValueError):
            open_files = []
            cursor_positions = {}
            bookmarks = {}
            active_index = 0

        word_wrap = data.get("word_wrap", False)
        self.actions["wordwrap"].setChecked(word_wrap)

        tag_comp = data.get("tag_completion_enabled", True)
        self.actions["tag_completion"].setChecked(tag_comp)

        restored = False

        for path in open_files:
            if path and Path(path).is_file():
                self.open_path(path, record_recent=False)
                target = Path(path).resolve()

                for tab_index in range(self.tabs.count()):
                    editor = self.tabs.widget(tab_index)

                    if editor.path == target:
                        position = cursor_positions.get(str(target))

                        if position and len(position) == 2:
                            line = max(
                                0, min(int(position[0]), editor.lines() - 1)
                            )
                            column = max(0, int(position[1]))
                            editor.setCursorPosition(line, column)
                            editor.ensureLineVisible(line)
                            editor.setFocus()

                        for bookmark_line in bookmarks.get(
                            str(target), []
                        ):
                            bookmark_line = int(bookmark_line)

                            if 0 <= bookmark_line < editor.lines():
                                editor.markerAdd(
                                    bookmark_line,
                                    editor.bookmark_marker
                                )

                        break

                restored = True

        if restored:
            self.tabs.setCurrentIndex(
                min(active_index, self.tabs.count() - 1)
            )
        else:
            self.new_file()

    def create_actions(self):
        self.actions = {}
        self.shortcut_settings_file = SHORTCUTS_FILE
        
        icon_directory = Path(__file__).resolve().parent / "icons" / "menu"

        icon_files = {
            "new": "new.png",
            "open": "open.png",
            "save": "save.png",
            "saveall": "saveall.png",
            "saveas": "saveas.png",
            "close": "close.png",
            "close_all": "close-all.png",
            "undo": "undo.png",
            "redo": "redo.png",
            "find": "find.png",
            "findnext": "find.png",
            "findprevious": "find.png",
            "replaceall": "replace.png",
            "insert_paragraph": "paragraph.png",
            "insert_h1": "heading.png",
            "insert_h2": "heading.png",
            "insert_bold": "bold.png",
            "insert_italic": "italic.png",
            "insert_underline": "underline.png",
            "insert_div": "tag.png",
            "insert_ul": "ul.png",
            "insert_ol": "ol.png",
            "insert_hr": "hr.png",
            "insert_link": "link.png",
            "insert_image": "image.png",
        }

        def add(name, label, shortcut, function):
            action = QAction(label, self)
            action.setShortcut(shortcut)

            icon_name = icon_files.get(name)
            if icon_name:
                icon_path = icon_directory / icon_name
                if icon_path.is_file():
                    icon = QIcon(str(icon_path))
                    if not icon.isNull():
                        action.setIcon(icon)

            action.triggered.connect(function)
            self.actions[name] = action

        add("new", "New", "Ctrl+N", self.new_file)
        add("open", "Open...", "Ctrl+O", self.open_file)
        add("save", "Save", "Ctrl+S", self.save_file)
        add("saveall", "Save All", "Ctrl+Alt+S", self.save_all)        
        add("saveas", "Save As...", "Ctrl+Shift+S", self.save_as)
        add("close", "Close Tab", "Ctrl+W",
            lambda: self.close_tab(self.tabs.currentIndex()))

        add("undo", "Undo", "Ctrl+Z", lambda: self.editor_call("undo"))
        add("redo", "Redo", "Ctrl+Y", lambda: self.editor_call("redo"))
        add("find", "Find and Replace...", "Ctrl+F", self.show_find_replace)
        add("findnext", "Find Next", "F3", self.find_next_in_dialog)
        add("findprevious", "Find Previous", "Shift+F3", self.find_previous_in_dialog)
        add("gotoline", "Go to Line...", "Ctrl+G", self.go_to_line)        
        add("replaceall", "Replace...", "Ctrl+H", self.show_replace_dialog)        
        add("quit", "Quit", "Ctrl+Q", self.close)
        add("close_all", "Close All", "", self.close_all_tabs)
        add("help", "TelaEdit Help", "F1", self.show_help)
        add("about", "About TelaEdit", "", self.show_about)
        add("defaultbrowser", "Open in Default Browser", "",
            self.launch_default_browser)
        self.actions["defaultbrowser"].setShortcut(QKeySequence("F12"))
        self.actions["defaultbrowser"].setShortcutContext(
            Qt.ShortcutContext.ApplicationShortcut
        )
        self.addAction(self.actions["defaultbrowser"])

        # HTML insertion actions
        add("insert_paragraph", "Paragraph", "",
            lambda checked=False: self.insert_tag_pair("<p>", "</p>"))
        add("insert_h1", "Heading 1", "",
            lambda checked=False: self.insert_tag_pair("<h1>", "</h1>"))
        add("insert_h2", "Heading 2", "",
            lambda checked=False: self.insert_tag_pair("<h2>", "</h2>"))
        add("insert_bold", "Bold", "",
            lambda checked=False: self.insert_tag_pair("<b>", "</b>"))
        add("insert_italic", "Italic", "",
            lambda checked=False: self.insert_tag_pair("<i>", "</i>"))
        add("insert_underline", "Underline", "",
            lambda checked=False: self.insert_tag_pair("<u>", "</u>"))
        add("insert_div", "Division (div)", "",
            lambda checked=False: self.insert_tag_pair("<div>", "</div>"))
        add("insert_ul", "Unordered List", "", self.insert_unordered_list)
        add("insert_ol", "Ordered List", "", self.insert_ordered_list)
        add("insert_hr", "Horizontal Rule", "", self.insert_horizontal_rule)
        add("insert_link", "Link...", "", self.insert_link)
        add("insert_image", "Image...", "", self.insert_image)
     
        panel_action = QAction("Explorer", self)
        panel_action.setCheckable(True)
        panel_action.setChecked(True)
        panel_action.triggered.connect(self.file_dock.setVisible)
        self.file_dock.visibilityChanged.connect(panel_action.setChecked)
        self.actions["filepanel"] = panel_action
        
        wrap_action = QAction("Word wrap", self)
        wrap_action.setCheckable(True)
        wrap_action.setChecked(False)
        wrap_action.triggered.connect(self.toggle_word_wrap)
        self.actions["wordwrap"] = wrap_action

        tag_comp_action = QAction("Tag completion", self)
        tag_comp_action.setCheckable(True)
        tag_comp_action.setChecked(True)
        tag_comp_action.triggered.connect(self.toggle_tag_completion)
        self.actions["tag_completion"] = tag_comp_action

        spell_action = QAction("Spell checking", self)
        spell_action.setCheckable(True)
        spell_action.setChecked(SPELLER.enabled)
        spell_action.triggered.connect(self.toggle_spell_check)
        self.actions["spellcheck"] = spell_action

        font_action = QAction("Editor font...", self)
        font_action.triggered.connect(self.choose_editor_font)
        self.actions["editorfont"] = font_action

        # Indentation commands
        add("indent_line", "Indent Line", "Ctrl+]", self.indent_line)
        add("unindent_line", "Unindent Line", "Ctrl+[", self.unindent_line)
        add("indent_block", "Indent Block", "Ctrl+Shift+]", self.indent_block)
        add("unindent_block", "Unindent Block", "Ctrl+Shift+[", self.unindent_block)        
        
        self.default_shortcuts = {
            name: action.shortcut().toString()
            for name, action in self.actions.items()
        }

        self.load_shortcuts()

    def browser_config_path(self):
        return BROWSERS_FILE

    def load_browser_entries(self):
        defaults = [
            {"name": "Firefox", "command": "/usr/bin/firefox", "icon": "browser-1.png"},
            {"name": "Brave", "command": "/usr/bin/brave-browser", "icon": "browser-2.png"},
            {"name": "Firefox 4", "command": "/opt/firefox-4/firefox", "icon": "browser-3.png"},
        ]
        blank = {"name": "", "command": "", "icon": DEFAULT_BROWSER_ICON}

        config = self.browser_config_path()

        if config.exists():
            try:
                entries = json.loads(config.read_text(encoding="utf-8"))
                for entry in entries:
                    if not entry.get("icon"):
                        entry["icon"] = DEFAULT_BROWSER_ICON
                return (entries + [dict(blank) for _ in range(10)])[:10]
            except (OSError, json.JSONDecodeError):
                pass

        return defaults + [dict(blank) for _ in range(7)]

    def rebuild_browser_menu(self):
        self.browsers_menu.clear()

        for browser_index, entry in enumerate(self.browser_entries):
            name = entry.get("name", "").strip()
            command = entry.get("command", "").strip()

            if name and command:
                action = self.browsers_menu.addAction(
                    browser_icon(entry.get("icon")),
                    name,
                    lambda checked=False, cmd=command: self.launch_browser(cmd)
                )

                if browser_index < 3:
                    action.setShortcut(
                        ["Ctrl+F2", "Ctrl+F3", "Ctrl+F4"][browser_index]
                    )

        self.browsers_menu.addSeparator()
        self.browsers_menu.addAction(
            "Configure browsers...",
            self.configure_browsers
        )

    def browse_browser_executable(self, table, row):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select browser executable",
            "/usr/bin",
            "All files (*)"
        )

        if path:
            table.setItem(row, 1, QTableWidgetItem(path))

    def store_user_browser_icon(self, source):
        """Copies an image chosen with 'Other icon...' into the config
        folder. Returns the stored file name, or None if it didn't work."""
        source = Path(source)

        if QPixmap(str(source)).isNull():
            QMessageBox.information(
                self, "TelaEdit",
                "That file could not be read as an image."
            )
            return None

        suffix = source.suffix.lower() or ".png"
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem).strip("-.") or "icon"

        try:
            USER_BROWSER_ICON_DIR.mkdir(parents=True, exist_ok=True)
            target = USER_BROWSER_ICON_DIR / f"{stem}{suffix}"
            number = 2

            while target.exists():
                # The same picture is already stored: just use that one
                if target.read_bytes() == source.read_bytes():
                    return target.name

                target = USER_BROWSER_ICON_DIR / f"{stem}-{number}{suffix}"
                number += 1

            shutil.copyfile(source, target)
        except OSError as error:
            QMessageBox.critical(
                self, "TelaEdit",
                f"Could not save the icon:\n{error}"
            )
            return None

        return target.name

    def on_browser_icon_chosen(self, box, all_boxes, index):
        """An icon was picked in the Configure browsers table. 'Other
        icon...' opens a file chooser; the chosen image is copied into the
        config folder and then offered in every row of the table."""
        if box.itemData(index) != OTHER_ICON_CHOICE:
            box.setProperty("last_index", index)
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a browser icon",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.ico *.svg *.xpm);;All files (*)"
        )

        stored = self.store_user_browser_icon(path) if path else None

        if not stored:
            # Cancelled or failed: go back to the icon that was selected
            box.setCurrentIndex(int(box.property("last_index") or 0))
            return

        data = "user:" + stored

        for other in all_boxes:
            if other.findData(data) < 0:
                # Just before the 'Other icon...' entry at the end
                other.insertItem(
                    other.count() - 1, browser_icon(data), stored, data
                )

        box.setCurrentIndex(box.findData(data))
        box.setProperty("last_index", box.currentIndex())

    def configure_browsers(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Configure browsers")
        dialog.resize(950, 420)
        layout = QVBoxLayout(dialog)

        table = QTableWidget(10, 5, dialog)
        table.setHorizontalHeaderLabels(
            ["Browser name", "Executable path", "Browse", "Icon", "Default"]
        )

        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        default_buttons = []
        icon_boxes = []

        for row, entry in enumerate(self.browser_entries):
            table.setItem(row, 0, QTableWidgetItem(entry.get("name", "")))
            table.setItem(row, 1, QTableWidgetItem(entry.get("command", "")))

            browse_button = QPushButton("Browse...")
            browse_button.clicked.connect(
                lambda checked=False, r=row: self.browse_browser_executable(table, r)
            )
            table.setCellWidget(row, 2, browse_button)

            icon_box = QComboBox()
            icon_box.setIconSize(QSize(24, 24))

            for filename in bundled_browser_icons():
                icon_box.addItem(
                    browser_icon(filename),
                    filename.removesuffix(".png").replace("-", " ").replace("_", " ").title(),
                    filename
                )

            for filename in user_browser_icons():
                icon_box.addItem(
                    browser_icon("user:" + filename),
                    filename,
                    "user:" + filename
                )

            icon_box.addItem("Other icon...", OTHER_ICON_CHOICE)

            # An icon that can't be found (such as 'firefox.png' saved by an
            # older version) leaves the first icon selected
            icon_index = icon_box.findData(entry.get("icon", DEFAULT_BROWSER_ICON))
            icon_box.setCurrentIndex(icon_index if icon_index >= 0 else 0)
            icon_box.setProperty("last_index", icon_box.currentIndex())

            icon_box.activated.connect(
                lambda index, box=icon_box: self.on_browser_icon_chosen(
                    box, icon_boxes, index
                )
            )

            icon_boxes.append(icon_box)
            table.setCellWidget(row, 3, icon_box)

            default_button = QRadioButton()
            default_buttons.append(default_button)
            table.setCellWidget(row, 4, default_button)

            def select_default(checked, current=row):
                if checked:
                    for index, button in enumerate(default_buttons):
                        if index != current:
                            button.setChecked(False)

            default_button.toggled.connect(select_default)

        selected_default = None

        for row, entry in enumerate(self.browser_entries):
            if (
                entry.get("is_default", False)
                and entry.get("name", "").strip()
                and entry.get("command", "").strip()
            ):
                selected_default = row
                break

        if selected_default is None:
            for row, entry in enumerate(self.browser_entries):
                if (
                    entry.get("name", "").strip()
                    and entry.get("command", "").strip()
                ):
                    selected_default = row
                    break

        if selected_default is not None:
            default_buttons[selected_default].setChecked(True)

        layout.addWidget(table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        entries = []
        selected_row = next(
            (
                row for row, button in enumerate(default_buttons)
                if button.isChecked()
            ),
            None
        )

        if selected_row is not None:
            name_item = table.item(selected_row, 0)
            command_item = table.item(selected_row, 1)

            if not (
                name_item and name_item.text().strip()
                and command_item and command_item.text().strip()
            ):
                QMessageBox.information(
                    self, "TelaEdit",
                    "Please select a row with a browser name and executable path."
                )
                return

        for row in range(10):
            name_item = table.item(row, 0)
            command_item = table.item(row, 1)

            name = name_item.text().strip() if name_item else ""
            command = command_item.text().strip() if command_item else ""

            entries.append({
                "name": name,
                "command": command,
                "icon": icon_boxes[row].currentData() or DEFAULT_BROWSER_ICON,
                "is_default": row == selected_row
            })

        config = self.browser_config_path()

        try:
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(
                json.dumps(entries, indent=4),
                encoding="utf-8"
            )
        except OSError as error:
            QMessageBox.critical(
                self, "TelaEdit",
                f"Could not save browser settings:\n{error}"
            )
            return

        self.browser_entries = entries
        self.rebuild_browser_menu()
        self.refresh_browser_toolbar()

    def launch_browser(self, command):
        editor = self.editor()

        if not editor:
            return

        if not editor.path or not editor.path.exists():
            QMessageBox.information(
                self, "TelaEdit",
                "Save this file before opening it in a browser."
            )
            return

        try:
            subprocess.Popen([command, str(editor.path)])
        except Exception as error:
            QMessageBox.critical(
                self, "TelaEdit",
                f"Could not launch browser:\n{error}"
            )

    def load_shortcuts(self):
        try:
            data = json.loads(
                self.shortcut_settings_file.read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError):
            return

        for name, shortcut in data.items():
            if name in self.actions:
                self.actions[name].setShortcut(shortcut)

    def show_keyboard_shortcuts(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard Shortcuts")
        dialog.resize(560, 600)

        layout = QVBoxLayout(dialog)

        tree = QTreeWidget(dialog)
        tree.setColumnCount(2)
        tree.setHeaderLabels(["Command", "Shortcut"])
        tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        tree.header().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        tree.setRootIsDecorated(True)

        standard_names = {
            "new", "open", "save", "saveall", "saveas",
            "close", "undo", "redo", "find", "findnext",
            "findprevious", "gotoline", "replaceall", "quit",
        }

        sections = {
            "insert": QTreeWidgetItem(tree, ["Insert commands"]),
            "standard": QTreeWidgetItem(tree, ["Standard shortcuts"]),
            "other": QTreeWidgetItem(tree, ["Other commands"]),
        }

        shortcut_editors = {}

        for name, action in self.actions.items():
            label = action.text().replace("&", "")

            if name.startswith("insert_"):
                section = sections["insert"]
            elif name in standard_names:
                section = sections["standard"]
            else:
                section = sections["other"]

            item = QTreeWidgetItem(section, [label, ""])
            editor = QKeySequenceEdit(action.shortcut(), tree)
            tree.setItemWidget(item, 1, editor)
            shortcut_editors[name] = editor

        sections["insert"].setExpanded(True)
        sections["standard"].setExpanded(False)
        sections["other"].setExpanded(False)

        layout.addWidget(tree)

        restore_button = QPushButton("Restore Defaults", dialog)
        button_row = QHBoxLayout()
        button_row.addWidget(restore_button)
        button_row.addStretch()
        layout.addLayout(button_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog
        )
        layout.addWidget(buttons)

        def restore_defaults():
            for name, editor in shortcut_editors.items():
                editor.setKeySequence(
                    QKeySequence(self.default_shortcuts[name])
                )

        def save_shortcuts():
            shortcuts = {}
            owners = {}

            for name, editor in shortcut_editors.items():
                shortcut = editor.keySequence().toString()
                shortcuts[name] = shortcut

                if shortcut:
                    if shortcut in owners:
                        QMessageBox.warning(
                            dialog,
                            "Shortcut Conflict",
                            f'"{shortcut}" is assigned to more '
                            f'than one command:\n\n'
                            f'{owners[shortcut]}\n'
                            f'{self.actions[name].text()}'
                        )
                        return

                    owners[shortcut] = self.actions[name].text()

            try:
                self.shortcut_settings_file.write_text(
                    json.dumps(shortcuts, indent=2),
                    encoding="utf-8"
                )
            except OSError as error:
                QMessageBox.warning(
                    dialog,
                    "Save Error",
                    f"Could not save keyboard shortcuts:\n{error}"
                )
                return

            for name, shortcut in shortcuts.items():
                self.actions[name].setShortcut(shortcut)

            dialog.accept()

        restore_button.clicked.connect(restore_defaults)
        buttons.accepted.connect(save_shortcuts)
        buttons.rejected.connect(dialog.reject)

        dialog.exec()

    def show_document_weight(self):
        editor = self.editor()
        if not editor:
            return

        if not editor.path:
            QMessageBox.warning(
                self, "Document Weight",
                "Please save the document first."
            )
            return

        if editor.isModified():
            QMessageBox.warning(
                self, "Document Weight",
                "Please save your changes first."
            )
            return

        try:
            html_path = editor.path.resolve()
            html_size = html_path.stat().st_size
            html_text = html_path.read_text(encoding="utf-8")

            parser = ImageSourceParser()
            parser.feed(html_text)

            def collect_files(sources):
                found = set()
                missing = set()
                external = set()

                for source in sources:
                    source = source.strip()
                    if not source:
                        continue

                    if source.startswith(
                        ("http://", "https://", "//", "data:")
                    ):
                        external.add(source)
                        continue

                    if source.startswith("file:"):
                        continue

                    try:
                        path = (
                            html_path.parent
                            / source.split("#")[0].split("?")[0]
                        ).resolve()

                        if path.is_file():
                            found.add(path)
                        else:
                            missing.add(source)
                    except (OSError, ValueError):
                        missing.add(source)

                return found, missing, external

            image_paths, missing_images, external_images = (
                collect_files(parser.sources)
            )

            css_paths, missing_css, external_css = (
                collect_files(parser.stylesheets)
            )

            js_paths, missing_js, external_js = (
                collect_files(parser.scripts)
            )

            def total_size(paths):
                size = 0
                for path in paths:
                    try:
                        size += path.stat().st_size
                    except OSError:
                        pass
                return size

            used_paths = {html_path}

            image_paths -= used_paths
            image_size = total_size(image_paths)
            used_paths.update(image_paths)

            css_paths -= used_paths
            css_size = total_size(css_paths)
            used_paths.update(css_paths)

            js_paths -= used_paths
            js_size = total_size(js_paths)

        except (OSError, UnicodeError, ValueError) as error:
            QMessageBox.warning(
                self, "Document Weight",
                f"Could not measure the document:\n{error}"
            )
            return

        total = html_size + image_size + css_size + js_size

        QMessageBox.information(
            self,
            "Document Weight",
            f"HTML: {html_size / 1024:.2f} KiB\n"
            f"Images: {image_size / 1024:.2f} KiB\n"
            f"Stylesheets: {css_size / 1024:.2f} KiB\n"
            f"JavaScript: {js_size / 1024:.2f} KiB\n"
            f"--------------------\n"
            f"Total: {total / 1024:.2f} KiB\n"
            f"Total: {total / 1_000_000:.4f} MB\n\n"
            f"Local images counted: {len(image_paths)}\n"
            f"Missing images: {len(missing_images)}\n"
            f"External images excluded: {len(external_images)}\n"
            f"Local stylesheets counted: {len(css_paths)}\n"
            f"Missing stylesheets: {len(missing_css)}\n"
            f"External stylesheets excluded: {len(external_css)}\n"
            f"Local scripts counted: {len(js_paths)}\n"
            f"Missing scripts: {len(missing_js)}\n"
            f"External scripts excluded: {len(external_js)}"
        )

    def backup_configuration(self):
        self.save_session()

        default_name = (
            Path.home()
            / f"TelaEdit-configuration-{datetime.now():%Y%m%d-%H%M%S}.zip"
        )

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Backup TelaEdit Configuration",
            str(default_name),
            "ZIP archives (*.zip)"
        )

        if not filename:
            return

        archive_path = Path(filename)

        if archive_path.suffix.lower() != ".zip":
            archive_path = archive_path.with_suffix(".zip")

        try:
            with zipfile.ZipFile(
                archive_path, "w", zipfile.ZIP_DEFLATED
            ) as archive:
                for file_path in CONFIG_DIR.rglob("*"):
                    if file_path.is_file():
                        if file_path.resolve() == archive_path.resolve():
                            continue

                        archive.write(
                            file_path,
                            file_path.relative_to(CONFIG_DIR).as_posix()
                        )

            QMessageBox.information(
                self,
                "Backup Complete",
                f"Configuration backed up to:\n{archive_path}"
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Backup Failed",
                f"Could not create the configuration backup:\n{exc}"
            )

    def create_menus(self):
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")

        for name in ("new", "open", "save", "saveall", "saveas", "close"):
            file_menu.addAction(self.actions[name])

        file_menu.addAction(self.actions["close_all"])

        self.recent_files_menu = file_menu.addMenu("Recent Files")
        self.update_recent_files_menu()

        file_menu.addSeparator()
        file_menu.addAction("New from Template...", self.new_from_template)
        file_menu.addAction("Save as Template...", self.save_as_template)

        file_menu.addSeparator()
        file_menu.addAction("Backup Configuration...", self.backup_configuration)

        file_menu.addSeparator()
        file_menu.addAction(self.actions["quit"])

        edit_menu = bar.addMenu("&Edit")
        edit_menu.addAction(self.actions["undo"])
        edit_menu.addAction(self.actions["redo"])
        edit_menu.addSeparator()

        menu_icon_dir = Path(__file__).resolve().parent / "icons" / "menu"

        self.actions["indent_line"].setIcon(QIcon(str(menu_icon_dir / "indent.png")))
        self.actions["indent_block"].setIcon(QIcon(str(menu_icon_dir / "indent.png")))
        self.actions["unindent_line"].setIcon(QIcon(str(menu_icon_dir / "outdent.png")))
        self.actions["unindent_block"].setIcon(QIcon(str(menu_icon_dir / "outdent.png")))

        edit_menu.addAction(self.actions["indent_line"])
        edit_menu.addAction(self.actions["unindent_line"])
        edit_menu.addAction(self.actions["indent_block"])
        edit_menu.addAction(self.actions["unindent_block"])

        search_menu = bar.addMenu("&Search")
        search_menu.addAction(self.actions["find"])
        search_menu.addAction(self.actions["findnext"])
        search_menu.addAction(self.actions["findprevious"])
        search_menu.addAction(self.actions["replaceall"]) 
        search_menu.addAction(self.actions["gotoline"])                      

        view_menu = bar.addMenu("&View")
        view_menu.addAction(self.actions["filepanel"])
        view_menu.addSeparator()
        view_menu.addAction(self.actions["wordwrap"])
        view_menu.addAction(self.actions["editorfont"])

        insert_menu = bar.addMenu("&Insert")

        for name in ("insert_paragraph", "insert_h1", "insert_h2"):
            insert_menu.addAction(self.actions[name])

        insert_menu.addSeparator()

        for name in ("insert_bold", "insert_italic", "insert_underline"):
            insert_menu.addAction(self.actions[name])

        insert_menu.addSeparator()

        insert_menu.addAction(self.actions["insert_div"])
        insert_menu.addAction(self.actions["insert_ul"])
        insert_menu.addAction(self.actions["insert_ol"])
        insert_menu.addAction(self.actions["insert_hr"])

        insert_menu.addSeparator()

        insert_menu.addAction(self.actions["insert_link"])
        insert_menu.addAction(self.actions["insert_image"])

        tools_menu = bar.addMenu("&Tools")

        tools_menu.addAction(
            QIcon(str(menu_icon_dir / "bgcolor.png")),
            "Tag-pair highlight colour...",
             self.choose_tag_pair_color
        )
        tools_menu.addAction(
             QIcon(str(menu_icon_dir / "bgcolor.png")),
             "Current-line highlight colour...",
             self.choose_current_line_color
        )
        tools_menu.addAction(
            QIcon(str(menu_icon_dir / "weight.png")),
            "Document Weight",
             self.show_document_weight
        )
        tools_menu.addAction(self.tidy_action)

        # External programs: separator, the user's tools, then Configure
        self.tools_menu = tools_menu
        tools_menu.addSeparator()
        self.external_tool_menu_actions = []
        self.configure_tools_action = tools_menu.addAction(
            "Configure external tools...",
            self.configure_external_tools
        )
        self.rebuild_external_tools_menu()

        self.browser_entries = self.load_browser_entries()
        self.browsers_menu = bar.addMenu("&Browsers")
        self.rebuild_browser_menu()


        settings_menu = bar.addMenu("&Settings")
        settings_menu.addAction("Default Template...", self.choose_default_template)
        settings_menu.addAction("Indentation Settings...", self.choose_indentation_settings)
        settings_menu.addAction("Keyboard Shortcuts...", self.show_keyboard_shortcuts)
        settings_menu.addAction("Toolbar icon size...", self.choose_toolbar_icon_size)
        settings_menu.addAction(
            "Syntax highlighting colours...", self.choose_syntax_colours
        )
        settings_menu.addSeparator()
        settings_menu.addAction(self.actions["tag_completion"])
        settings_menu.addAction(self.actions["spellcheck"])
        settings_menu.addAction("Spelling language...", self.choose_spelling_language)
        settings_menu.addAction("Spelling highlight style...", self.choose_spell_style)

        help_menu = bar.addMenu("&Help")
        help_menu.addAction(self.actions["help"])
        help_menu.addSeparator()
        help_menu.addAction(self.actions["about"])

    def launch_default_browser(self):
        for entry in self.browser_entries:
            if entry.get("is_default", False):
                command = entry.get("command", "").strip()
                if command:
                    self.launch_browser(command)
                    return

        QMessageBox.information(
            self, "TelaEdit",
            "Please select a default browser in Configure browsers."
        )

    def create_toolbar(self):
        self.main_toolbar_tabs = QTabWidget(self)
        self.main_toolbar_tabs.setMovable(False)
        self.main_toolbar_tabs.setDocumentMode(True)

        tab_definitions = [
            ("Standard", (
                "filepanel", "new", "open", "save", "close_all", "undo", "redo", 
            )),
            ("HTML", (
                "insert_paragraph", "insert_h1", "insert_h2",
                "insert_link", "insert_image", "insert_hr",
            )),
            ("Formatting", (
                "insert_bold", "insert_italic", "insert_underline",
                "indent_line", "unindent_line",
                "indent_block", "unindent_block",
            )),
            ("Lists", (
                "insert_ul", "insert_ol",
            )),
            ("Tools", ()),
        ]

        for tab_name, action_names in tab_definitions:
            page = QWidget()
            layout = QHBoxLayout(page)
            layout.setContentsMargins(4, 3, 4, 3)
            layout.setSpacing(3)

            # Special handling for the Tools tab so it can be dynamic
            if "tool" in tab_name.lower():
                # Save a reference to this layout so we can refresh it anytime
                self.tools_tab_layout = layout
                self.refresh_tools_toolbar()
            else:
                # Standard tabs
                for name in action_names:
                    action = self.actions.get(name)
                    if action:
                        button = QToolButton(page)
                        button.setDefaultAction(action)
                        
                        # Give tidy text beside its icon; keep others icon-only
                        if name == "tidy":
                            button.setToolButtonStyle(
                                Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                            )
                        else:
                            button.setToolButtonStyle(
                                Qt.ToolButtonStyle.ToolButtonIconOnly
                            )
                            
                        button.setToolTip(action.text())
                        layout.addWidget(button)
                layout.addStretch()

            self.main_toolbar_tabs.addTab(page, tab_name)

        browser_page = QWidget()
        self.browser_tab_layout = QHBoxLayout(browser_page)
        self.browser_tab_layout.setContentsMargins(4, 3, 4, 3)
        self.browser_tab_layout.setSpacing(6)
        self.browser_tab_layout.addStretch()
        self.main_toolbar_tabs.addTab(browser_page, "Browsers")

        toolbar = QToolBar("Main toolbar", self)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.addWidget(self.main_toolbar_tabs)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        self.refresh_browser_toolbar()
        self.apply_toolbar_icon_size()

    def load_toolbar_icon_size(self):
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            size = int(data.get("toolbar_icon_size", DEFAULT_TOOLBAR_ICON_SIZE))
        except (OSError, ValueError, TypeError):
            size = DEFAULT_TOOLBAR_ICON_SIZE

        return size if size in TOOLBAR_ICON_SIZES else DEFAULT_TOOLBAR_ICON_SIZE

    def apply_toolbar_icon_size(self):
        """Shows the toolbar icons at the chosen size (toolbar only; the
        Explorer file icons keep their own fixed size)."""
        if not hasattr(self, "main_toolbar_tabs"):
            return

        size = QSize(self.toolbar_icon_size, self.toolbar_icon_size)

        for index in range(self.main_toolbar_tabs.count()):
            page = self.main_toolbar_tabs.widget(index)

            for button in page.findChildren(QToolButton):
                button.setIconSize(size)

    def choose_toolbar_icon_size(self):
        sizes = [str(size) for size in TOOLBAR_ICON_SIZES]
        current = (
            sizes.index(str(self.toolbar_icon_size))
            if str(self.toolbar_icon_size) in sizes else 0
        )

        choice, accepted = QInputDialog.getItem(
            self,
            "Toolbar icon size",
            "Size of the toolbar icons, in pixels:",
            sizes,
            current,
            False
        )

        if not accepted:
            return

        self.toolbar_icon_size = int(choice)

        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}

        data["toolbar_icon_size"] = self.toolbar_icon_size

        try:
            STATE_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(
                self, "TelaEdit",
                f"Could not save the toolbar icon size:\n{error}"
            )

        self.apply_toolbar_icon_size()
        self.status.showMessage(
            f"Toolbar icon size set to {self.toolbar_icon_size}", 3000
        )

    def refresh_browser_toolbar(self):
        if not hasattr(self, "browser_tab_layout"):
            return

        while self.browser_tab_layout.count():
            item = self.browser_tab_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for entry in self.browser_entries:
            name = entry.get("name", "").strip()
            command = entry.get("command", "").strip()

            if not name or not command:
                continue

            button = QToolButton()
            button.setText(name)
            button.setIcon(browser_icon(entry.get("icon")))
            button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            )
            button.setToolTip(f"Open current document in {name}")
            button.clicked.connect(
                lambda checked=False, cmd=command: self.launch_browser(cmd)
            )
            self.browser_tab_layout.addWidget(button)

        self.browser_tab_layout.addStretch()
        self.apply_toolbar_icon_size()

    def refresh_tools_toolbar(self):
        """Refreshes the Tools tab with any custom external tools."""
        if not hasattr(self, "tools_tab_layout"):
            return

        # Clear existing buttons from the layout
        while self.tools_tab_layout.count():
            item = self.tools_tab_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        # Add any custom external tools from your list
        for entry in getattr(self, "external_tool_entries", []):
            name = entry.get("name", "").strip()
            command = entry.get("command", "").strip()

            if not name or not command:
                continue

            button = QToolButton()
            button.setText(name)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setToolTip(f"Run external tool: {name}")
            
            # Connect click to launch the external command
            button.clicked.connect(
                lambda checked=False, e=entry: self.launch_external_tool(e)
            )
            self.tools_tab_layout.addWidget(button)

        self.tools_tab_layout.addStretch()
        self.apply_toolbar_icon_size()

    def load_external_tools(self):
        """Reads the list of external programs saved in external_tools.json."""
        try:
            data = json.loads(EXTERNAL_TOOLS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

        if not isinstance(data, list):
            return []

        entries = []
        for item in data:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name", "")).strip()
            command = str(item.get("command", "")).strip()

            if name and command:
                entries.append({
                    "name": name,
                    "command": command,
                    "send_file": bool(item.get("send_file", False)),
                })

        return entries[:10]

    def rebuild_external_tools_menu(self):
        """Rebuilds the external program entries in the Tools menu."""
        for action in self.external_tool_menu_actions:
            self.tools_menu.removeAction(action)
            action.deleteLater()

        self.external_tool_menu_actions = []

        for entry in self.external_tool_entries:
            action = QAction(entry["name"], self)
            action.triggered.connect(
                lambda checked=False, e=entry: self.launch_external_tool(e)
            )
            self.tools_menu.insertAction(self.configure_tools_action, action)
            self.external_tool_menu_actions.append(action)

    def configure_external_tools(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Configure external tools")
        dialog.resize(850, 420)
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel(
            "Add programs to the Tools menu (and the Tools tab of the "
            "toolbar). Tick 'Open current file' to give the program the "
            "file you are editing when it starts.",
            dialog
        ))

        table = QTableWidget(10, 4, dialog)
        table.setHorizontalHeaderLabels(
            ["Name in menu", "Program", "Browse", "Open current file"]
        )
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        def browse_program(row):
            path, _ = QFileDialog.getOpenFileName(
                dialog,
                "Select program",
                "/usr/bin",
                "All files (*)"
            )

            if not path:
                return

            table.setItem(row, 1, QTableWidgetItem(path))

            name_item = table.item(row, 0)
            if not name_item or not name_item.text().strip():
                table.setItem(
                    row, 0, QTableWidgetItem(Path(path).name.title())
                )

        for row in range(10):
            entry = (
                self.external_tool_entries[row]
                if row < len(self.external_tool_entries)
                else {}
            )

            table.setItem(row, 0, QTableWidgetItem(entry.get("name", "")))
            table.setItem(row, 1, QTableWidgetItem(entry.get("command", "")))

            browse_button = QPushButton("Browse...")
            browse_button.clicked.connect(
                lambda checked=False, r=row: browse_program(r)
            )
            table.setCellWidget(row, 2, browse_button)

            check_item = QTableWidgetItem()
            check_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
            )
            check_item.setCheckState(
                Qt.CheckState.Checked
                if entry.get("send_file", False)
                else Qt.CheckState.Unchecked
            )
            table.setItem(row, 3, check_item)

        layout.addWidget(table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        entries = []

        for row in range(10):
            name_item = table.item(row, 0)
            command_item = table.item(row, 1)
            check_item = table.item(row, 3)

            name = name_item.text().strip() if name_item else ""
            command = command_item.text().strip() if command_item else ""

            if not command:
                continue

            if not name:
                try:
                    name = Path(shlex.split(command)[0]).name.title()
                except (ValueError, IndexError):
                    name = command

            entries.append({
                "name": name,
                "command": command,
                "send_file": (
                    check_item is not None
                    and check_item.checkState() == Qt.CheckState.Checked
                ),
            })

        try:
            EXTERNAL_TOOLS_FILE.parent.mkdir(parents=True, exist_ok=True)
            EXTERNAL_TOOLS_FILE.write_text(
                json.dumps(entries, indent=4),
                encoding="utf-8"
            )
        except OSError as error:
            QMessageBox.critical(
                self, "TelaEdit",
                f"Could not save external tools:\n{error}"
            )
            return

        self.external_tool_entries = entries
        self.rebuild_external_tools_menu()
        self.refresh_tools_toolbar()
        self.status.showMessage("External tools saved", 2500)

    def launch_external_tool(self, entry):
        """Starts an external program from the Tools menu or toolbar."""
        command = entry.get("command", "").strip()
        name = entry.get("name", command)

        try:
            arguments = shlex.split(command)
        except ValueError as error:
            QMessageBox.warning(
                self, "TelaEdit",
                f"Could not understand the command for {name}:\n{error}"
            )
            return

        if not arguments:
            return

        if entry.get("send_file", False):
            editor = self.editor()

            if not editor or not editor.path or not editor.path.exists():
                QMessageBox.information(
                    self, "TelaEdit",
                    f"Save this file before opening it in {name}."
                )
                return

            arguments.append(str(editor.path))

        try:
            subprocess.Popen(arguments, start_new_session=True)
            self.status.showMessage(f"Started {name}", 2500)
        except FileNotFoundError:
            QMessageBox.warning(
                self, "TelaEdit",
                f"Could not find the program for {name}:\n{arguments[0]}"
            )
        except Exception as error:
            QMessageBox.critical(
                self, "TelaEdit",
                f"Could not start {name}:\n{error}"
            )

    def editor(self):
        return self.tabs.currentWidget()
        
    def editor_call(self, method):
        editor = self.editor()
        if editor:
            getattr(editor, method)()

    def insert_tag_pair(self, opening, closing):
        editor = self.editor()
        if not editor:
            return

        if editor.hasSelectedText():
            selected = editor.selectedText()
            editor.replaceSelectedText(
                f"{opening}{selected}{closing}"
            )
        else:
            line, index = editor.getCursorPosition()
            editor.insertAt(opening + closing, line, index)
            editor.setCursorPosition(
                line, index + len(opening)
            )

        editor.setFocus()

    def insert_list(self, list_tag):
        editor = self.editor()
        if not editor:
            return

        if editor.hasSelectedText():
            selected = editor.selectedText()
            items = selected.splitlines() or [""]
            list_items = "\n".join(
                f"    <li>{item}</li>" for item in items
            )
            replacement = (
                f"<{list_tag}>\n"
                f"{list_items}\n"
                f"</{list_tag}>"
            )
            editor.replaceSelectedText(replacement)
        else:
            line, index = editor.getCursorPosition()
            replacement = (
                f"<{list_tag}>\n"
                "    <li></li>\n"
                f"</{list_tag}>"
            )
            editor.insertAt(replacement, line, index)
            editor.setCursorPosition(line + 1, 8)

        editor.setFocus()

    def insert_unordered_list(self):
        self.insert_list("ul")

    def insert_ordered_list(self):
        self.insert_list("ol")

    def insert_horizontal_rule(self):
        self.insert_self_closing_tag("<hr>")

    def insert_self_closing_tag(self, tag):
        editor = self.editor()
        if not editor:
            return

        if editor.hasSelectedText():
            editor.replaceSelectedText(tag)
        else:
            line, index = editor.getCursorPosition()
            editor.insertAt(tag, line, index)
            editor.setCursorPosition(
                line, index + len(tag)
            )

        editor.setFocus()

    def insert_link(self):
        editor = self.editor()
        if not editor:
            return

        if editor.path:
            base_dir = editor.path.resolve().parent
        else:
            base_dir = (
                Path(self.last_directory).resolve()
                if self.last_directory
                else Path.home()
            )

        dialog = QDialog(self)
        dialog.setWindowTitle("Insert Link")
        dialog.setModal(True)

        layout = QVBoxLayout(dialog)
        row = QHBoxLayout()

        url_input = QLineEdit(dialog)
        url_input.setPlaceholderText(
            "Enter a URL or browse for a local file..."
        )

        browse_button = QPushButton("Browse...", dialog)
        row.addWidget(url_input, 1)
        row.addWidget(browse_button)
        layout.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def browse_target(_checked=False):
            filename, _ = QFileDialog.getOpenFileName(
                dialog,
                "Select Link Target",
                str(base_dir),
                "Web files (*.html *.htm *.xhtml *.xml "
                "*.css *.js *.pdf *.jpg *.jpeg *.png "
                "*.gif *.webp *.svg);;All files (*)"
            )

            if not filename:
                return

            target = Path(filename).resolve()
            relative = os.path.relpath(
                str(target), str(base_dir)
            ).replace(os.sep, "/")

            url_input.setText(quote(relative, safe="/._~-"))

        browse_button.clicked.connect(browse_target)
        url_input.setFocus()

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        url = url_input.text().strip()
        if not url:
            return

        url = html.escape(url, quote=True)
        opening = f'<a href="{url}">'

        if editor.hasSelectedText():
            selected = editor.selectedText()
            editor.replaceSelectedText(
                f"{opening}{selected}</a>"
            )
        else:
            line, index = editor.getCursorPosition()
            editor.insertAt(opening + "</a>", line, index)
            editor.setCursorPosition(
                line, index + len(opening)
            )

        editor.setFocus()

    def insert_image(self):
        editor = self.editor()
        if not editor:
            return

        if editor.path:
            base_dir = editor.path.resolve().parent
        else:
            base_dir = (
                Path(self.last_directory).resolve()
                if self.last_directory
                else Path.home()
            )

        had_selection = editor.hasSelectedText()
        selected = editor.selectedText() if had_selection else ""
        selection = editor.getSelection() if had_selection else None
        cursor = editor.getCursorPosition()

        dialog = QDialog(self)
        dialog.setWindowTitle("Insert Image")
        dialog.setModal(True)

        layout = QVBoxLayout(dialog)

        source_row = QHBoxLayout()
        source_input = QLineEdit(dialog)
        source_input.setPlaceholderText(
            "Enter an image URL or browse for a file..."
        )
        browse_button = QPushButton("Browse...", dialog)

        source_row.addWidget(source_input, 1)
        source_row.addWidget(browse_button)
        layout.addLayout(source_row)

        preview_label = QLabel("Image preview", dialog)
        preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_label.setMinimumSize(240, 150)
        preview_label.setMaximumHeight(220)
        preview_label.setStyleSheet("border: 1px solid #999; padding: 4px;")
        layout.addWidget(preview_label)

        dimensions_label = QLabel("", dialog)
        dimensions_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(dimensions_label)

        def update_image_preview():
            source = source_input.text().strip()

            if not source or source.startswith(
                ("http://", "https://", "data:")
            ):
                preview_label.setPixmap(QPixmap())
                preview_label.setText(
                    "Preview unavailable"
                    if source else "Image preview"
                )
                return

            image_path = Path(unquote(source))

            if not image_path.is_absolute():
                image_path = base_dir / image_path

            pixmap = QPixmap(str(image_path))

            if pixmap.isNull():
                preview_label.setPixmap(QPixmap())
                preview_label.setText("Preview unavailable")
                dimensions_label.setText("")
                return

            width = pixmap.width()
            height = pixmap.height()
            dimensions_label.setText(
                f"Image dimensions: {width} × {height} pixels"
            )
            preview_label.setPixmap(
                pixmap.scaled(
                    preview_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            )

        layout.addWidget(QLabel("Alternative text:"))
        alt_input = QLineEdit(dialog)
        alt_input.setPlaceholderText("Describe the image")
        alt_input.setText(selected)
        layout.addWidget(alt_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def browse_image(_checked=False):
            filename, _ = QFileDialog.getOpenFileName(
                dialog,
                "Select Image",
                str(base_dir),
                "Images (*.png *.jpg *.jpeg *.gif *.webp "
                "*.svg *.avif *.bmp *.tif *.tiff);;All files (*)"
            )

            if not filename:
                return

            target = Path(filename).resolve()
            relative = os.path.relpath(
                str(target), str(base_dir)
            ).replace(os.sep, "/")

            source_input.setText(
                quote(relative, safe="/._~-")
            )

        browse_button.clicked.connect(browse_image)
        source_input.textChanged.connect(update_image_preview)

        image_width = 0
        image_height = 0

        source_input.setFocus()

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        source = source_input.text().strip()
        if not source:
            return

        source = html.escape(source, quote=True)
        alt = html.escape(alt_input.text().strip(), quote=True)

        image_path = Path(unquote(source_input.text().strip()))
        if not image_path.is_absolute():
            image_path = base_dir / image_path

        pixmap = QPixmap(str(image_path))
        if not pixmap.isNull():
            image_width = pixmap.width()
            image_height = pixmap.height()

        if image_width and image_height:
            tag = (
                f'<img src="{source}" '
                f'width="{image_width}" height="{image_height}" '
                f'alt="{alt}">'
            )
        else:
            tag = f'<img src="{source}" alt="{alt}">'

        if had_selection and selection:
            editor.setSelection(*selection)
            editor.replaceSelectedText(tag)
        else:
            line, index = cursor
            editor.setCursorPosition(line, index)
            editor.insertAt(tag, line, index)
            editor.setCursorPosition(
                line, index + len(tag)
            )

        editor.setFocus()

    def go_to_line(self):
        editor = self.editor()

        if not editor:
            return

        line_number, accepted = QInputDialog.getInt(
            self,
            "Go to Line",
            "Line number:",
            1,
            1,
            max(1, editor.lines()),
            1
        )

        if not accepted:
            return

        editor.setCursorPosition(line_number - 1, 0)
        editor.ensureLineVisible(line_number - 1)
        editor.setFocus()            

    def update_cursor_position(self, *_):
        editor = self.editor()

        if not editor:
            self.position_label.setText("Line 1, Column 1")
            return

        line, column = editor.getCursorPosition()
        self.position_label.setText(
            f"Line {line + 1}, Column {column + 1}"
        )           

    def show_find_replace(self):
        editor = self.editor()

        if not editor:
            return

        if not hasattr(self, "find_replace_dialog"):
            self.find_replace_dialog = FindReplaceDialog(self)

        if editor.hasSelectedText():
            self.find_replace_dialog.find_input.setText(
                editor.selectedText()
            )

        self.find_replace_dialog.update_match_count()
        self.find_replace_dialog.show()
        self.find_replace_dialog.raise_()
        self.find_replace_dialog.activateWindow()

    def show_replace_dialog(self):
        self.show_find_replace()
        self.find_replace_dialog.replace_input.setFocus()
        
    def find_next_in_dialog(self):
        if not hasattr(self, "find_replace_dialog"):
            self.show_find_replace()
            return

        if not self.find_replace_dialog.isVisible():
            self.find_replace_dialog.show()

        self.find_replace_dialog.find_next()
        
    def find_previous_in_dialog(self):
        if not hasattr(self, "find_replace_dialog"):
            self.show_find_replace()
            return

        if not self.find_replace_dialog.isVisible():
            self.find_replace_dialog.show()

        self.find_replace_dialog.find_previous()                

    def toggle_word_wrap(self, checked):
        mode = (
            QsciScintilla.WrapMode.WrapWord
            if checked
            else QsciScintilla.WrapMode.WrapNone
        )

        for index in range(self.tabs.count()):
            self.tabs.widget(index).setWrapMode(mode)

    def show_about(self):
        """Help > About: shows the logo, the version and what TelaEdit
        is built on."""
        dialog = QDialog(self)
        dialog.setWindowTitle("About TelaEdit")

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(28, 22, 28, 16)
        layout.setSpacing(12)

        # The wide logo lives in the help folder (help/logo.png)
        logo_file = Path(__file__).resolve().parent / "help" / "logo.png"
        logo = QPixmap(str(logo_file))

        if not logo.isNull():
            if logo.width() > 300:
                logo = logo.scaledToWidth(
                    300, Qt.TransformationMode.SmoothTransformation
                )

            logo_label = QLabel(dialog)
            logo_label.setPixmap(logo)
            logo_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(logo_label)
            heading = f"Version {html.escape(APP_VERSION)}"
        else:
            heading = f"TelaEdit {html.escape(APP_VERSION)}"

        info = QLabel(dialog)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        info.setText(
            f"<h3 align='center'>{heading}</h3>"
            "<p align='center'>An HTML editor for Linux.</p>"
            f"<p align='center'>Python {sys.version.split()[0]} &middot; "
            f"Qt {QT_VERSION_STR} &middot; PyQt {PYQT_VERSION_STR}</p>"
            "<p align='center'>Released under the GNU General Public "
            "License v3.</p>"
        )
        layout.addWidget(info)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)

        dialog.exec()

    def show_help(self):
        """Opens the help file (help/help.html) in the default browser."""
        help_file = Path(__file__).resolve().parent / "help" / "help.html"

        if not help_file.is_file():
            QMessageBox.information(
                self,
                "TelaEdit Help",
                "The help file was not found:\n"
                f"{help_file}\n\n"
                "Put help.html in a folder called 'help' next to telaedit.py."
            )
            return

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(help_file))):
            QMessageBox.warning(
                self,
                "TelaEdit Help",
                f"Could not open the help file:\n{help_file}"
            )

    def spell_error(self, message):
        """Tells the user, once per session, that spell checking can't run."""
        if self.spell_error_shown:
            return

        self.spell_error_shown = True
        QMessageBox.warning(self, "Spell checking", message)

    def recheck_all_spelling(self):
        for index in range(self.tabs.count()):
            editor = self.tabs.widget(index)

            if editor:
                editor.run_spell_check()

    def toggle_spell_check(self, checked):
        SPELLER.enabled = checked
        SPELLER.failed = False
        SPELLER.save_settings()

        if checked and not SPELLER.available():
            self.spell_error_shown = True
            QMessageBox.warning(
                self, "Spell checking",
                SPELL_INSTALL_HELP.format(
                    language=SPELLER.language,
                    details="no spell checker program found"
                )
            )

        self.recheck_all_spelling()

    def choose_spell_style(self):
        names = list(SPELL_STYLES)
        current = names.index(SPELLER.style) if SPELLER.style in names else 0

        style, accepted = QInputDialog.getItem(
            self,
            "Spelling highlight style",
            "How should misspelled words be marked?",
            names,
            current,
            False
        )

        if not accepted:
            return

        SPELLER.style = style
        SPELLER.save_settings()

        for index in range(self.tabs.count()):
            editor = self.tabs.widget(index)

            if editor:
                editor.apply_spell_style()
                editor.viewport().update()

    def choose_spelling_language(self):
        language, accepted = QInputDialog.getText(
            self,
            "Spelling language",
            "Dictionary code (for example en_GB or en_US):",
            text=SPELLER.language
        )

        language = language.strip()

        if not accepted or not language:
            return

        if not re.fullmatch(r"[A-Za-z_]{2,20}", language):
            QMessageBox.warning(
                self, "Spell checking",
                "Please enter a dictionary code such as en_GB or en_US."
            )
            return

        SPELLER.set_language(language)
        self.spell_error_shown = False
        self.recheck_all_spelling()
        self.status.showMessage(f"Spelling language set to {language}", 3000)

    def toggle_tag_completion(self, checked):
        for index in range(self.tabs.count()):
            self.tabs.widget(index).tag_completion_enabled = checked

    def indent_line(self):
        editor = self.editor()
        if editor:
            line, _ = editor.getCursorPosition()
            editor.indent(line)

    def unindent_line(self):
        editor = self.editor()
        if editor:
            line, _ = editor.getCursorPosition()
            editor.unindent(line)

    def indent_block(self):
        editor = self.editor()
        if not editor:
            return

        line_from, index_from, line_to, index_to = editor.getSelection()
        if line_from < 0:
            return

        editor.beginUndoAction()
        try:
            for line in range(line_from, line_to + 1):
                editor.indent(line)
        finally:
            editor.endUndoAction()

    def unindent_block(self):
        editor = self.editor()
        if not editor:
            return

        line_from, index_from, line_to, index_to = editor.getSelection()
        if line_from < 0:
            return

        editor.beginUndoAction()
        try:
            for line in range(line_from, line_to + 1):
                editor.unindent(line)
        finally:
            editor.endUndoAction()
            
    def choose_editor_font(self):
        editor = self.editor()
        if not editor:
            return

        chosen_font, accepted = QFontDialog.getFont(
            editor.font(), self, "Choose editor font"
        )

        if not accepted:
            return

        for index in range(self.tabs.count()):
            editor = self.tabs.widget(index)
            editor.setFont(chosen_font)

            lexer = editor.lexer()
            if lexer:
                lexer.setDefaultFont(chosen_font)
                for style in range(128):
                    lexer.setFont(chosen_font, style)

                # Put back any bold settings from Syntax highlighting colours
                editor.apply_syntax_overrides(chosen_font)

        settings = STATE_FILE

        try:
            data = json.loads(settings.read_text())
        except (OSError, ValueError):
            data = {}

        data["editor_font"] = chosen_font.toString()

        try:
            settings.write_text(json.dumps(data, indent=4))
        except OSError as error:
            QMessageBox.warning(
                self, "TelaEdit",
                f"Could not save editor font:\n{error}"
            )

        self.status.showMessage("Editor font saved", 2500)

    def choose_bookmark_color(self):
        editor = self.editor()
        if not editor:
            return

        color = QColorDialog.getColor(
            editor.bookmark_color,
            self,
            "Choose bookmark colour"
        )

        if not color.isValid():
            return

        color_name = color.name()

        for index in range(self.tabs.count()):
            self.tabs.widget(index).set_bookmark_color(color)

        settings = STATE_FILE

        try:
            data = json.loads(settings.read_text())
        except (OSError, ValueError):
            data = {}

        data["bookmark_color"] = color_name

        try:
            settings.write_text(json.dumps(data, indent=4))
        except OSError as error:
            QMessageBox.warning(
                self, "TelaEdit",
                f"Could not save bookmark colour:\n{error}"
            )

        self.status.showMessage("Bookmark colour saved", 2500)

    def choose_tag_pair_color(self):
        editor = self.editor()
        if not editor:
            return

        color = QColorDialog.getColor(
            editor.tag_pair_color,
            self,
            "Choose tag-pair highlight colour"
        )

        if not color.isValid():
            return

        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            tab.tag_pair_color = QColor(color)
            tab.setIndicatorForegroundColor(color, tab.tag_indicator)

        settings = STATE_FILE

        try:
            data = json.loads(settings.read_text())
        except (OSError, ValueError):
            data = {}

        data["tag_pair_color"] = color.name()

        try:
            settings.write_text(json.dumps(data, indent=4))
        except OSError as error:
            QMessageBox.warning(
                self,
                "TelaEdit",
                f"Could not save tag-pair colour:\n{error}"
            )

        self.status.showMessage(
            "Tag-pair highlight colour saved", 2500
        )

    def choose_current_line_color(self):
        editor = self.editor()
        if not editor:
            return

        color = QColorDialog.getColor(
            editor.current_line_color,
            self,
            "Choose current-line highlight colour"
        )

        if not color.isValid():
            return

        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            tab.current_line_color = QColor(color)
            tab.setCaretLineBackgroundColor(color)

        settings = STATE_FILE

        try:
            data = json.loads(settings.read_text())
        except (OSError, ValueError):
            data = {}

        data["current_line_color"] = color.name()

        try:
            settings.write_text(json.dumps(data, indent=4))
        except OSError as error:
            QMessageBox.warning(
                self,
                "TelaEdit",
                f"Could not save current-line colour:\n{error}"
            )

        self.status.showMessage(
            "Current-line highlight colour saved", 2500
        )

    def choose_syntax_colours(self):
        """Settings > Syntax highlighting colours: change the colour and
        bold/normal weight of each element (tags, attributes, comments...)."""
        working = {
            key: {number: dict(entry) for number, entry in styles.items()}
            for key, styles in load_syntax_overrides().items()
        }

        dialog = QDialog(self)
        dialog.setWindowTitle("Syntax highlighting colours")
        dialog.resize(640, 580)
        layout = QVBoxLayout(dialog)

        language_row = QHBoxLayout()
        language_row.addWidget(QLabel("Language:", dialog))
        language_box = QComboBox(dialog)

        for key, name in SYNTAX_LANGUAGES.items():
            language_box.addItem(name, key)

        # Start on the language of the document you are editing
        current_editor = self.editor()
        current_key = getattr(current_editor, "syntax_key", None)

        if current_key:
            language_box.setCurrentIndex(max(0, language_box.findData(current_key)))

        language_row.addWidget(language_box, 1)
        layout.addLayout(language_row)

        hint = QLabel(
            "Click a colour button to change that element's colour. "
            "Tick Bold to show it in bold.",
            dialog
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        table = QTableWidget(0, 3, dialog)
        table.setHorizontalHeaderLabels(["Element", "Colour", "Bold"])
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(table)

        reset_button = QPushButton("Reset this language to the default colours", dialog)
        reset_row = QHBoxLayout()
        reset_row.addWidget(reset_button)
        reset_row.addStretch()
        layout.addLayout(reset_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog
        )
        layout.addWidget(buttons)

        def text_colour_for(background):
            return "#000000" if QColor(background).lightness() > 128 else "#ffffff"

        def populate():
            key = language_box.currentData()
            lexer = new_syntax_lexer(key)
            styles = working.setdefault(key, {})

            table.blockSignals(True)
            table.setRowCount(0)

            for number in range(128):
                description = lexer.description(number)

                if not description:
                    continue

                row = table.rowCount()
                table.insertRow(row)

                default_color = lexer.color(number).name()
                paper = lexer.paper(number).name()

                entry = styles.get(number, {})
                color = entry.get("color") or default_color
                bold = bool(entry.get("bold", False))

                name_item = QTableWidgetItem(description)
                name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                name_item.setBackground(QBrush(QColor(paper)))
                table.setItem(row, 0, name_item)

                colour_button = QPushButton(color, dialog)
                table.setCellWidget(row, 1, colour_button)

                bold_item = QTableWidgetItem()
                bold_item.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                )
                bold_item.setCheckState(
                    Qt.CheckState.Checked if bold else Qt.CheckState.Unchecked
                )
                table.setItem(row, 2, bold_item)

                def show_row(name_item=name_item, colour_button=colour_button,
                             color=color, bold=bold):
                    font = name_item.font()
                    font.setBold(bold)
                    name_item.setFont(font)
                    name_item.setForeground(QBrush(QColor(color)))
                    colour_button.setText(color)
                    colour_button.setStyleSheet(
                        f"QPushButton {{ background-color: {color}; "
                        f"color: {text_colour_for(color)}; }}"
                    )

                show_row()

                def store(number=number, default_color=default_color,
                          styles=styles, name_item=name_item,
                          colour_button=colour_button, bold_item=bold_item,
                          show_row=show_row):
                    color = colour_button.text()
                    bold = bold_item.checkState() == Qt.CheckState.Checked

                    if color == default_color and not bold:
                        styles.pop(number, None)
                    else:
                        styles[number] = {
                            "color": "" if color == default_color else color,
                            "bold": bold,
                        }

                    show_row(color=color, bold=bold)

                def pick_colour(checked=False, colour_button=colour_button,
                                store=store):
                    chosen = QColorDialog.getColor(
                        QColor(colour_button.text()), dialog,
                        "Choose colour"
                    )

                    if chosen.isValid():
                        colour_button.setText(chosen.name())
                        store()

                colour_button.clicked.connect(pick_colour)

                # Tick/untick Bold: find the row again from the item
                bold_item.setData(Qt.ItemDataRole.UserRole, number)
                bold_handlers[number] = store

            table.blockSignals(False)

        bold_handlers = {}

        def bold_changed(item):
            if item.column() != 2:
                return

            handler = bold_handlers.get(item.data(Qt.ItemDataRole.UserRole))

            if handler:
                handler()

        def language_changed(*_):
            bold_handlers.clear()
            populate()

        def reset_language():
            working[language_box.currentData()] = {}
            language_changed()

        table.itemChanged.connect(bold_changed)
        language_box.currentIndexChanged.connect(language_changed)
        reset_button.clicked.connect(reset_language)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        populate()

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        if not save_syntax_overrides(working):
            QMessageBox.warning(
                self, "TelaEdit", "Could not save the syntax colours."
            )
            return

        # Rebuild the colouring in every open document
        for index in range(self.tabs.count()):
            editor = self.tabs.widget(index)

            if editor and getattr(editor, "syntax_key", None):
                editor.set_language()

        self.status.showMessage("Syntax highlighting colours saved", 2500)

    def new_file(self):
        template_directory = TEMPLATES_DIR
        template_directory.mkdir(parents=True, exist_ok=True)

        default_template = template_directory / "HTML5 Standard.html"

        boilerplate = (
            '<!DOCTYPE html>\n'
            '<html lang="en">\n'
            '<head>\n'
            '<meta charset="UTF-8">\n'
            '\n'
            '<title>Page Title</title>\n'
            '\n'
            '\t<!-- Responsive and mobile friendly stuff -->\n'
            '\t<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '\t<meta name="description" content="Description of website.">\n'
            '\t<meta name="keywords" content="List, of, keywords">\n'
            '\t<meta name="HandheldFriendly" content="True">\n'
            '\t<meta name="MobileOptimized" content="320">\n'
            '\t\n'
            '\t<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">\n'
            '\t<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">\n'
            '\t<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">\n'
            '\t<link rel="manifest" href="/site.webmanifest">\n'
            '\n'
            '\t<link rel="stylesheet" href="css/styles.css">\n'
            '\n'
            '</head>\n'
            '<body>\n'
            '\n'
            '\n'
            '\n'
            '</body>\n'
            '</html>'
        )

        if not default_template.exists():
            default_template.write_text(boilerplate, encoding="utf-8")

        settings = STATE_FILE

        try:
            data = json.loads(settings.read_text())
        except (OSError, ValueError):
            data = {}

        selected_template = Path(
            data.get("default_template", str(default_template))
        )

        if not selected_template.is_file():
            selected_template = default_template

        try:
            template_text = selected_template.read_text(encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(
                self,
                "TelaEdit",
                f"Could not load the default template:\n{error}"
            )
            template_text = ""

        editor = CodeEditor()
        editor.cursorPositionChanged.connect(self.update_cursor_position)

        if self.actions["wordwrap"].isChecked():
            editor.setWrapMode(QsciScintilla.WrapMode.WrapWord)
        
        editor.tag_completion_enabled = self.actions["tag_completion"].isChecked()

        editor.modificationChanged.connect(
            lambda modified, e=editor: self.update_tab_label(e)
        )

        index = self.tabs.addTab(editor, "Untitled")
        self.tabs.setCurrentIndex(index)
        editor.setText(template_text)
        editor.setModified(False)
        editor.setFocus()

    def choose_indentation_settings(self):
        settings = STATE_FILE

        try:
            data = json.loads(settings.read_text())
        except (OSError, ValueError):
            data = {}

        current_tabs = data.get("indent_use_tabs", False)
        current_width = data.get("indent_width", 4)

        dialog = QDialog(self)
        dialog.setWindowTitle("Indentation Settings")

        layout = QFormLayout(dialog)

        mode_box = QComboBox()
        mode_box.addItem("Spaces", False)
        mode_box.addItem("Tabs", True)
        mode_box.setCurrentIndex(
            mode_box.findData(bool(current_tabs))
        )

        width_box = QSpinBox()
        width_box.setRange(1, 8)
        width_box.setValue(
            max(1, min(8, int(current_width)))
        )

        layout.addRow("Indent using:", mode_box)
        layout.addRow("Indent width:", width_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        data["indent_use_tabs"] = mode_box.currentData()
        data["indent_width"] = width_box.value()

        try:
            settings.write_text(json.dumps(data, indent=4))
        except OSError as error:
            QMessageBox.warning(
                self,
                "TelaEdit",
                f"Could not save indentation settings:\n{error}"
            )
            return

        use_tabs = bool(data["indent_use_tabs"])
        indent_width = data["indent_width"]

        for index in range(self.tabs.count()):
            editor = self.tabs.widget(index)
            editor.setIndentationsUseTabs(use_tabs)
            editor.setTabWidth(indent_width)
            editor.setIndentationWidth(indent_width)

        self.status.showMessage(
            "Indentation settings saved and applied.",
            4000
        )

    def choose_default_template(self):
        template_directory = TEMPLATES_DIR
        template_directory.mkdir(parents=True, exist_ok=True)

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Default Template",
            str(template_directory),
            "HTML Templates (*.html);;All files (*)"
        )

        if not path:
            return

        settings = STATE_FILE

        try:
            data = json.loads(settings.read_text())
        except (OSError, ValueError):
            data = {}

        data["default_template"] = str(Path(path).resolve())

        try:
            settings.write_text(json.dumps(data, indent=4))
        except OSError as error:
            QMessageBox.warning(
                self,
                "TelaEdit",
                f"Could not save default template:\n{error}"
            )
            return

        self.status.showMessage(
            f"Default template set to {Path(path).name}", 3000
        )

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open source file", self.last_directory,
            "Web files (*.html *.htm *.xhtml *.xml *.svg "
            "*.css *.js *.mjs *.txt);;All files (*)"
        )

        if path:
            self.open_path(path)

    def open_path(self, path, record_recent=True):
        target = Path(path).resolve()
        self.remember_directory(target.parent)
        
        for index in range(self.tabs.count()):
            editor = self.tabs.widget(index)

            if editor.path == target:
                self.tabs.setCurrentIndex(index)

                if record_recent:
                    self.add_recent_file(target)

                return

        try:
            editor = CodeEditor(target)
            editor.cursorPositionChanged.connect(self.update_cursor_position)
            if self.actions["wordwrap"].isChecked():
                editor.setWrapMode(QsciScintilla.WrapMode.WrapWord)
            
            editor.tag_completion_enabled = self.actions["tag_completion"].isChecked()
                
            editor.modificationChanged.connect(
                lambda modified, e=editor: self.update_tab_label(e)
            )

            index = self.tabs.addTab(editor, target.name)
            self.tabs.setCurrentIndex(index)
            if record_recent:
                self.add_recent_file(target)

        except Exception as error:
            QMessageBox.critical(
                self, "TelaEdit", f"Could not open file:\n{error}"
            )

    def open_tree_file(self, index):
        """Double-click in the lower panel: open the file in the editor."""
        path = self.file_model.filePath(self.file_proxy.mapToSource(index))

        if Path(path).is_file():
            self.open_path(path)

    def save_file(self):
        editor = self.editor()
        if not editor:
            return

        if not editor.path:
            self.save_as()
            return

        try:
            editor.save_file()
            self.update_tab_label(editor)
            self.status.showMessage("File saved", 2500)

        except Exception as error:
            QMessageBox.critical(
                self, "TelaEdit", f"Could not save file:\n{error}"
            )

    def save_all(self):
        saved_count = 0
        failed = False

        for index in range(self.tabs.count()):
            editor = self.tabs.widget(index)

            if not editor or not editor.isModified():
                continue

            if not editor.path:
                self.tabs.setCurrentIndex(index)
                self.save_as()

                if not editor.path or editor.isModified():
                    failed = True
                else:
                    saved_count += 1

                continue

            try:
                if editor.save_file():
                    self.update_tab_label(editor)
                    saved_count += 1
                else:
                    failed = True

            except Exception as error:
                QMessageBox.critical(
                    self,
                    "TelaEdit",
                    f"Could not save {editor.path}:\n{error}"
                )
                failed = True

        if failed:
            self.status.showMessage(
                "Save All finished with unsaved file(s)", 4000
            )
        else:
            self.status.showMessage(
                f"Saved {saved_count} changed file(s)", 2500
            )

    def save_as(self):
        editor = self.editor()
        if not editor:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save source file", self.last_directory,
            "HTML (*.html);;CSS (*.css);;JavaScript (*.js);;All files (*)"
        )

        if path:
            try:
                editor.save_file(path)
                self.remember_directory(Path(path).parent)
                self.update_tab_label(editor)

            except Exception as error:
                QMessageBox.critical(
                    self, "TelaEdit",
                    f"Could not save file:\n{error}"
                )

    def save_as_template(self):
        editor = self.editor()
        if not editor:
            return

        template_directory = TEMPLATES_DIR
        template_directory.mkdir(parents=True, exist_ok=True)

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save as Template",
            str(template_directory / "my-template.html"),
            "HTML Templates (*.html);;All files (*)"
        )

        if not path:
            return

        try:
            Path(path).write_text(editor.text(), encoding="utf-8")
            self.status.showMessage("Template saved", 2500)

        except OSError as error:
            QMessageBox.warning(
                self,
                "TelaEdit",
                f"Could not save template:\n{error}"
            )

    def new_from_template(self):
        template_directory = TEMPLATES_DIR

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Template",
            str(template_directory),
            "HTML Templates (*.html);;All files (*)"
        )

        if not path:
            return

        try:
            template_text = Path(path).read_text(encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(
                self,
                "TelaEdit",
                f"Could not open template:\n{error}"
            )
            return

        self.new_file()
        editor = self.editor()
        editor.setText(template_text)
        editor.setModified(False)
        editor.setFocus()

    def update_tab_label(self, editor):
        index = self.tabs.indexOf(editor)

        if index != -1:
            name = editor.path.name if editor.path else "Untitled"

            if editor.isModified():
                name = "*" + name
                self.tabs.tabBar().setTabTextColor(index, QColor("red"))
            else:
                self.tabs.tabBar().setTabTextColor(index, QColor())

            self.tabs.setTabText(index, name)

        self.update_title()

    def update_title(self, *_):
        editor = self.editor()

        if not editor:
            return

        name = str(editor.path) if editor.path else "Untitled"
        if editor.isModified():
            name = "*" + name

        self.setWindowTitle(f"TelaEdit — {name}")

        if not editor.path or not hasattr(self, "folder_tree"):
            return

        # Show this document's folder in the Explorer
        self.go_to_folder(str(editor.path.parent.resolve()))

    def closeEvent(self, event):
        for index in range(self.tabs.count()):
            editor = self.tabs.widget(index)

            if not editor or not editor.isModified():
                continue

            self.tabs.setCurrentIndex(index)

            answer = QMessageBox.question(
                self,
                "Unsaved Changes",
                f"Save changes to {editor.path or 'untitled document'} before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel
            )

            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return

            if answer == QMessageBox.StandardButton.Save:
                self.save_file()

                if editor.isModified():
                    event.ignore()
                    return
        self.save_session()
        event.accept()

    def close_tab(self, index):
        editor = self.tabs.widget(index)

        if not editor:
            return

        if editor.isModified():
            answer = QMessageBox.question(
                self, "TelaEdit",
                "Save changes before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel
            )

            if answer == QMessageBox.StandardButton.Cancel:
                return

            if answer == QMessageBox.StandardButton.Save:
                self.tabs.setCurrentIndex(index)
                self.save_file()

                if editor.isModified():
                    return
        self.tabs.removeTab(index)
        editor.deleteLater()

    def close_all_tabs(self):
        index = self.tabs.count() - 1

        while index >= 0:
            count_before = self.tabs.count()
            self.tabs.setCurrentIndex(index)
            self.close_tab(index)

            if self.tabs.count() == count_before:
                return

            index -= 1

    def snippets_file(self):
        directory = os.path.join(
            os.path.expanduser("~"),
            ".config",
            "TelaEdit",
            "snippets"
        )
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, "snippets.json")

    def load_snippets(self):
        path = self.snippets_file()

        if not os.path.isfile(path):
            return []

        try:
            with open(path, "r", encoding="utf-8") as file:
                snippets = json.load(file)
        except (OSError, json.JSONDecodeError):
            return []

        return snippets if isinstance(snippets, list) else []

    def save_snippets(self, snippets):
        path = self.snippets_file()

        try:
            with open(path, "w", encoding="utf-8") as file:
                json.dump(
                    snippets,
                    file,
                    indent=4,
                    ensure_ascii=False
                )
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Snippets",
                f"Could not save snippets:\n{exc}"
            )

    def snippet_branch_order(self):
        """The order you have put the snippet branches in (a list of names)."""
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

        order = data.get("snippet_branch_order", [])

        if not isinstance(order, list):
            return []

        return [str(name) for name in order]

    def save_snippet_branch_order(self, order):
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}

        data["snippet_branch_order"] = list(order)

        try:
            STATE_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Snippets",
                f"Could not save the branch order:\n{exc}"
            )

    def ordered_branches(self, names):
        """Branch names in the order you set. Branches you have not placed
        yet follow in alphabetical order."""
        names = set(names)
        result = []

        for name in self.snippet_branch_order():
            if name in names and name not in result:
                result.append(name)

        result.extend(sorted(names - set(result)))
        return result

    def refresh_snippet_list(self):
        if not hasattr(self, "snippet_list"):
            return

        self.snippet_list.clear()
        snippets = self.load_snippets()
        branches = {}

        for snippet in snippets:
            branch = snippet.get("branch", "").strip() or "General"
            branches.setdefault(branch, []).append(snippet)

        for branch_name in self.ordered_branches(branches):
            branch_snippets = branches[branch_name]
            branch_item = QTreeWidgetItem(self.snippet_list, [branch_name])
            branch_item.setExpanded(True)
            for snippet in branch_snippets:
                item = QTreeWidgetItem(branch_item, [snippet["name"]])
                item.setData(0, Qt.ItemDataRole.UserRole, snippet)

        self.register_snippet_shortcuts()

    def register_snippet_shortcuts(self):
        """Makes each snippet's Shortcut key insert that snippet."""
        for old in getattr(self, "snippet_shortcuts", []):
            old.setEnabled(False)
            old.setParent(None)
            old.deleteLater()

        self.snippet_shortcuts = []

        # Keys already used by menu commands win; a snippet can't take them
        # (self.actions is only TelaEdit's own command list once it has been
        # created; before that the name belongs to a built-in Qt function)
        commands = getattr(self, "actions", None)

        if not isinstance(commands, dict):
            commands = {}

        taken = {
            action.shortcut().toString()
            for action in commands.values()
            if not action.shortcut().isEmpty()
        }

        for snippet in self.load_snippets():
            text = str(snippet.get("shortcut", "")).strip()

            if not text:
                continue

            keys = QKeySequence(text)

            if keys.isEmpty() or keys.toString() in taken:
                continue

            taken.add(keys.toString())

            shortcut = QShortcut(keys, self)
            shortcut.activated.connect(
                lambda s=snippet: self.insert_snippet_content(s)
            )
            self.snippet_shortcuts.append(shortcut)

    def insert_selected_snippet(self, item, column=0):
        snippet = item.data(0, Qt.ItemDataRole.UserRole)
        if snippet:
            self.insert_snippet_content(snippet)

    def insert_snippet_content(self, snippet):
        editor = self.editor()
        if not editor:
            QMessageBox.information(
                self,
                "Snippets",
                "Open a document before inserting a snippet."
            )
            return

        before = snippet.get("before", "")
        after = snippet.get("after", "")

        if editor.hasSelectedText():
            selected = editor.selectedText()
            editor.replaceSelectedText(
                before + selected + after
            )
        else:
            line, column = editor.getCursorPosition()
            editor.insertAt(
                before + after, line, column
            )

            new_line = line + before.count("\n")
            if "\n" in before:
                new_column = len(before.rsplit("\n", 1)[-1])
            else:
                new_column = column + len(before)

            editor.setCursorPosition(new_line, new_column)

        editor.setFocus()

    def manage_snippets(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Manage Snippets")
        dialog.resize(550, 400)

        snippets = self.load_snippets()

        for snippet in snippets:
            if "before" not in snippet:
                snippet["before"] = snippet.pop("code", "")
            snippet.setdefault("after", "")
            snippet.setdefault("branch", "")

        layout = QVBoxLayout(dialog)

        hint = QLabel(
            "To rename a branch or move it up or down, click its heading "
            "and use Edit, Move Up, Move Down or Rename Branch.",
            dialog
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        snippet_list = QTreeWidget()
        snippet_list.setHeaderHidden(True)
        layout.addWidget(snippet_list)

        def refresh_list(select_index=None, select_branch=None):
            snippet_list.clear()
            branches = {}
            for index, snippet in enumerate(snippets):
                branch = snippet.get("branch", "").strip() or "General"
                branches.setdefault(branch, []).append((index, snippet))

            for branch_name in self.ordered_branches(branches):
                branch_snippets = branches[branch_name]
                branch_item = QTreeWidgetItem(snippet_list, [branch_name])
                branch_item.setExpanded(True)

                if select_branch is not None and branch_name == select_branch:
                    snippet_list.setCurrentItem(branch_item)

                for index, snippet in branch_snippets:
                    item = QTreeWidgetItem(branch_item, [snippet["name"]])
                    item.setData(0, Qt.ItemDataRole.UserRole, index)

                    if index == select_index:
                        snippet_list.setCurrentItem(item)

        def snippet_dialog(title, snippet=None):
            edit_dialog = QDialog(dialog)
            edit_dialog.setWindowTitle(title)
            edit_dialog.resize(500, 500)

            form = QVBoxLayout(edit_dialog)

            form.addWidget(QLabel("Branch / Category"))
            branch_edit = QLineEdit()
            form.addWidget(branch_edit)

            form.addWidget(QLabel("Name"))
            name_edit = QLineEdit()
            form.addWidget(name_edit)

            form.addWidget(QLabel("Shortcut"))
            shortcut_edit = QKeySequenceEdit()
            form.addWidget(shortcut_edit)

            form.addWidget(QLabel("Before text"))
            before_edit = QTextEdit()
            before_edit.setMinimumHeight(80)
            form.addWidget(before_edit)

            form.addWidget(QLabel("After text"))
            after_edit = QTextEdit()
            after_edit.setMinimumHeight(80)
            form.addWidget(after_edit)

            if snippet:
                branch_edit.setText(snippet.get("branch", ""))
                name_edit.setText(snippet["name"])
                shortcut_edit.setKeySequence(
                    QKeySequence(snippet.get("shortcut", ""))
                )
                before_edit.setPlainText(snippet.get("before", ""))
                after_edit.setPlainText(snippet.get("after", ""))

            buttons = QHBoxLayout()
            save_button = QPushButton("Save")
            cancel_button = QPushButton("Cancel")

            def save():
                if not name_edit.text().strip():
                    QMessageBox.warning(
                        edit_dialog,
                        "Snippets",
                        "Please enter a snippet name."
                    )
                    return

                new_key = shortcut_edit.keySequence().toString()

                if new_key:
                    for action in self.actions.values():
                        if action.shortcut().toString() == new_key:
                            QMessageBox.warning(
                                edit_dialog,
                                "Snippets",
                                f'"{new_key}" is already used by the command '
                                f'"{action.text().replace("&", "")}". '
                                'Please choose a different shortcut.'
                            )
                            return

                    for other in snippets:
                        if (
                            other is not snippet
                            and other.get("shortcut", "") == new_key
                        ):
                            QMessageBox.warning(
                                edit_dialog,
                                "Snippets",
                                f'"{new_key}" is already used by the snippet '
                                f'"{other.get("name", "")}". '
                                'Please choose a different shortcut.'
                            )
                            return

                edit_dialog.accept()

            save_button.clicked.connect(save)
            cancel_button.clicked.connect(edit_dialog.reject)

            buttons.addWidget(save_button)
            buttons.addWidget(cancel_button)
            form.addLayout(buttons)

            if edit_dialog.exec() != QDialog.DialogCode.Accepted:
                return None

            return {
                "branch": branch_edit.text().strip(),
                "name": name_edit.text().strip(),
                "shortcut": shortcut_edit.keySequence().toString(),
                "before": before_edit.toPlainText(),
                "after": after_edit.toPlainText()
            }

        def add_snippet():
            snippet = snippet_dialog("Add Snippet")
            if snippet is None:
                return

            snippets.append(snippet)
            self.save_snippets(snippets)
            self.refresh_snippet_list()            
            refresh_list()

        def edit_snippet():
            item = snippet_list.currentItem()
            if not item:
                return
            index = item.data(0, Qt.ItemDataRole.UserRole)
            if index is None:
                # A branch heading is selected: Edit renames it
                rename_branch()
                return

            snippet = snippet_dialog(
                "Edit Snippet", snippets[index]
            )
            if snippet is None:
                return

            snippets[index] = snippet
            self.save_snippets(snippets)
            self.refresh_snippet_list()
            refresh_list()

        def delete_snippet():
            item = snippet_list.currentItem()
            if not item:
                return
            index = item.data(0, Qt.ItemDataRole.UserRole)
            if index is None:
                return

            answer = QMessageBox.question(
                dialog,
                "Delete Snippet",
                f"Delete '{snippets[index]['name']}'?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

            snippets.pop(index)
            self.save_snippets(snippets)
            self.refresh_snippet_list()
            refresh_list()

        def insert_snippet():
            item = snippet_list.currentItem()
            if not item:
                return
            index = item.data(0, Qt.ItemDataRole.UserRole)
            if index is None:
                return

            editor = self.editor()
            if not editor:
                QMessageBox.information(
                    dialog,
                    "Snippets",
                    "Open a document before inserting a snippet."
                )
                return

            snippet = snippets[index]
            self.insert_snippet_content(snippet)
            dialog.accept()

        def branch_key(snippet):
            return snippet.get("branch", "").strip() or "General"

        def move_branch(name, direction):
            """Moves a branch heading up (-1) or down (+1) the list."""
            names = self.ordered_branches(
                {branch_key(snippet) for snippet in snippets}
            )

            if name not in names:
                return

            current = names.index(name)
            target = current + direction

            if target < 0 or target >= len(names):
                return

            names[current], names[target] = names[target], names[current]

            self.save_snippet_branch_order(names)
            self.refresh_snippet_list()
            refresh_list(select_branch=name)

        def move_snippet(direction):
            """Moves the selected snippet, or the selected branch heading,
            up (-1) or down (+1). A snippet moves within its branch."""
            item = snippet_list.currentItem()
            if not item:
                return
            index = item.data(0, Qt.ItemDataRole.UserRole)
            if index is None:
                move_branch(item.text(0), direction)
                return

            same_branch = [
                i for i, other in enumerate(snippets)
                if branch_key(other) == branch_key(snippets[index])
            ]
            position = same_branch.index(index) + direction

            if position < 0 or position >= len(same_branch):
                return

            target = same_branch[position]
            snippets[index], snippets[target] = snippets[target], snippets[index]

            self.save_snippets(snippets)
            self.refresh_snippet_list()
            refresh_list(select_index=target)

        def rename_branch(*_):
            item = snippet_list.currentItem()
            if not item:
                QMessageBox.information(
                    dialog, "Snippets",
                    "Click a branch (or a snippet in it) first."
                )
                return

            # A snippet is selected: use the branch it sits in
            branch_item = item.parent() if item.parent() else item
            old_name = branch_item.text(0)

            new_name, accepted = QInputDialog.getText(
                dialog,
                "Rename Branch",
                "Branch name:",
                text=old_name
            )

            new_name = new_name.strip()

            if not accepted or not new_name or new_name == old_name:
                return

            existing = {branch_key(snippet) for snippet in snippets}

            if new_name in existing:
                answer = QMessageBox.question(
                    dialog,
                    "Rename Branch",
                    f"A branch called '{new_name}' already exists.\n\n"
                    "Move these snippets into it?"
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return

            # Keep the renamed branch where it was in the order
            order = self.ordered_branches(
                {branch_key(snippet) for snippet in snippets}
            )
            renamed_order = []

            for name in order:
                name = new_name if name == old_name else name

                if name not in renamed_order:
                    renamed_order.append(name)

            for snippet in snippets:
                if branch_key(snippet) == old_name:
                    snippet["branch"] = new_name

            self.save_snippets(snippets)
            self.save_snippet_branch_order(renamed_order)
            self.refresh_snippet_list()
            refresh_list(select_branch=new_name)

        def branch_double_clicked(item, column):
            # Double-clicking a branch heading renames it
            if item.parent() is None:
                rename_branch()

        snippet_list.itemDoubleClicked.connect(branch_double_clicked)

        buttons = QHBoxLayout()

        for label, callback in (
            ("Add", add_snippet),
            ("Edit", edit_snippet),
            ("Delete", delete_snippet),
            ("Insert", insert_snippet),
            ("Close", dialog.reject),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)

        layout.addLayout(buttons)

        order_buttons = QHBoxLayout()

        for label, callback in (
            ("Move Up", lambda: move_snippet(-1)),
            ("Move Down", lambda: move_snippet(1)),
            ("Rename Branch...", rename_branch),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            order_buttons.addWidget(button)

        layout.addLayout(order_buttons)

        refresh_list()
        dialog.exec()

    def setup_tidy_tool(self):
        """Sets up the Check HTML with Tidy action and button icon."""
        self.tidy_action = QAction("Run HTML Tidy", self)
        
        from pathlib import Path
        base_dir = Path(__file__).resolve().parent
        
        # Check a few common places where the icon might live
        possible_paths = [
            base_dir / "menu" / "tidy.png",
            base_dir / "icons" / "tidy.png",
            base_dir / "icons" / "menu" / "tidy.png"
        ]
        
        icon_found = False
        for icon_path in possible_paths:
            if icon_path.is_file():
                self.tidy_action.setIcon(QIcon(str(icon_path)))
                icon_found = True
                break
                
        if not icon_found:
            print("Notice: Tidy icon image not found, using text/default fallback.")
        
        self.tidy_action.triggered.connect(self.run_html_tidy)
        
        self.actions = getattr(self, 'actions', {})
        self.actions["tidy"] = self.tidy_action

        # Tidy is created after the other actions, so it has to be given
        # its default shortcut entry and its saved shortcut here
        self.default_shortcuts["tidy"] = ""
        self.tidy_action.setShortcutContext(
            Qt.ShortcutContext.ApplicationShortcut
        )
        self.addAction(self.tidy_action)

        try:
            saved = json.loads(
                self.shortcut_settings_file.read_text(encoding="utf-8")
            )
            if "tidy" in saved:
                self.tidy_action.setShortcut(saved["tidy"])
        except (OSError, ValueError):
            pass

    def run_html_tidy(self):
        """Executes the HTML check on the currently active editor tab using HTML Tidy."""

        current_editor = self.editor() if hasattr(self, 'editor') else None
        
        if current_editor is None:
            self.log_message("Active editor not found for Tidy check.")
            return

        html_content = current_editor.text()
        
        if not html_content.strip():
            self.log_message("The editor is empty. Nothing to tidy.")
            return

        self.log_message("--- Running HTML Tidy check ---")
        
        try:
            import subprocess
            # Run html tidy, passing the editor content via stdin
            process = subprocess.run(
                ["tidy", "-q", "-errors"],
                input=html_content,
                text=True,
                capture_output=True
            )
            
            # Tidy reports errors and warnings on stderr
            errors = process.stderr.strip()
            output = process.stdout.strip()
            
            if errors:
                self.log_message("Tidy Report:\n" + errors)
            elif output:
                self.log_message("Tidy Output:\n" + output)
            else:
                self.log_message("HTML Tidy check completed: No errors or warnings found! ✨")
                
        except FileNotFoundError:
            self.log_message("Error: 'tidy' command-line tool is not installed or not found in system PATH.")
            self.log_message("Tip: You can install it via your terminal using 'sudo apt install tidy'.")
        except Exception as e:
            self.log_message(f"An unexpected error occurred while running Tidy: {e}")

    def create_output_panel(self):
        """Creates the bottom output/message dock panel.

        The panel has its own title bar with the title on the left and the
        close button (close-2.png) in the top-right corner, in the same way
        as the Explorer panel.
        """
        base_dir = Path(__file__).resolve().parent

        self.output_dock = QDockWidget("Output / Messages", self)
        self.output_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea |
            Qt.DockWidgetArea.TopDockWidgetArea
        )

        # --- Custom title bar: title on the left, close button in the corner ---
        title_bar = QWidget(self.output_dock)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(6, 2, 4, 2)

        title_label = QLabel("Output / Messages", title_bar)
        title_layout.addWidget(title_label)
        title_layout.addStretch()

        # Look for close-2.png in the usual icon folders
        close_icon = None
        for folder in (
            base_dir / "icons" / "menu",
            base_dir / "icons",
            base_dir / "menu",
        ):
            candidate = folder / "close-2.png"
            if candidate.is_file():
                icon = QIcon(str(candidate))
                if not icon.isNull():
                    close_icon = icon
                    break

        output_close_btn = QToolButton(title_bar)
        if close_icon is not None:
            output_close_btn.setIcon(close_icon)
            output_close_btn.setIconSize(QSize(20, 20))
        else:
            # Never leave an invisible button if the icon can't be found
            output_close_btn.setText("✕")
            print("Notice: close-2.png not found, using text fallback.")
        output_close_btn.setAutoRaise(True)
        output_close_btn.setToolTip("Close output window")
        output_close_btn.clicked.connect(self.output_dock.hide)
        title_layout.addWidget(output_close_btn)

        self.output_dock.setTitleBarWidget(title_bar)
        # -----------------------------------------------------------------------

        # Container widget to hold the action bar and the text box
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Action bar at the top of the output panel
        top_bar_layout = QHBoxLayout()

        run_tidy_btn = QPushButton("Run HTML Tidy", self)
        if hasattr(self, 'actions') and "tidy" in self.actions:
            run_tidy_btn.setIcon(self.actions["tidy"].icon())
        run_tidy_btn.clicked.connect(self.run_html_tidy)

        top_bar_layout.addWidget(run_tidy_btn)
        top_bar_layout.addStretch()

        layout.addLayout(top_bar_layout)

        # Text area for logs and error messages
        self.output_text = ClickableOutput(self, self.go_to_line_column)
        self.output_text.setReadOnly(True)
        font = QFont("Monospace", 10)
        self.output_text.setFont(font)

        layout.addWidget(self.output_text)
        self.output_dock.setWidget(container)

        # Add it to the bottom of the main window by default
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.output_dock)
        self.output_dock.hide()
        
    def go_to_line_column(self, line_number, column):
        """Moves the cursor in the current editor to a 1-based line and column."""
        editor = self.editor()
        if not editor:
            return

        line = max(0, min(line_number - 1, editor.lines() - 1))
        index = max(0, min(column - 1, len(editor.text(line).rstrip("\r\n"))))

        editor.setCursorPosition(line, index)
        editor.ensureLineVisible(line)
        editor.setFocus()

    def log_message(self, message):
        """Appends a message to the bottom output panel."""
        if hasattr(self, 'output_text'):
            # Insert as plain text so tags such as <div> in Tidy's messages
            # are shown literally and every report line is its own line
            self.output_text.moveCursor(QTextCursor.MoveOperation.End)
            self.output_text.insertPlainText(message + "\n")
            self.output_text.moveCursor(QTextCursor.MoveOperation.End)
            # Ensure the dock is visible when messages arrive
            if hasattr(self, 'output_dock'):
                self.output_dock.show()

app = QApplication(sys.argv)
app.setApplicationName("TelaEdit")
app_icon_file = Path(__file__).resolve().parent / "icons" / "telaedit-icon.png"
app.setWindowIcon(QIcon(str(app_icon_file)))
palette = app.palette()
palette.setColor(QPalette.ColorRole.Window, QColor("#efefef"))
palette.setColor(QPalette.ColorRole.Button, QColor("#efefef"))
app.setPalette(palette)

window = TelaEdit()

if window.restore_maximized:
    window.showMaximized()
else:
    window.show()

sys.exit(app.exec())
