"""Real LuaJIT 2.1 VM runtime tests for Ordpresse KOReader plugins & User Patches (v1.6.0).

Uses lupa.luajit21 (matching KOReader's exact LuaJIT 2.1 / Lua 5.1 ABI) to compile and
execute ordpresse.koplugin, all 3 User Patches, and all 3 new Punkt-5 plugins end-to-end
against real temp directories and the live FastAPI TestClient.
"""

from __future__ import annotations

import json as py_json
import os
from pathlib import Path
import shutil
import sqlite3
from urllib.parse import quote, unquote, urlparse

from lupa.luajit21 import LuaRuntime
import pytest

from ordpresse.db import Database
from ordpresse.web.plugin_builder import BRIDGE_LUA, MAIN_LUA, META_LUA


def _lua_to_py(val):
    """Recursively convert lupa LuaTable objects to Python dicts/lists."""
    if val is None or isinstance(val, (str, int, float, bool)):
        return val
    if hasattr(val, "items"):
        keys = list(val.keys())
        if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
            return [_lua_to_py(val[k]) for k in range(1, len(keys) + 1)]
        return {k: _lua_to_py(v) for k, v in val.items()}
    return val


class KOReaderTestEnv:
    """Self-contained LuaJIT 2.1 harness simulating KOReader APIs & HTTP against FastAPI TestClient."""

    def __init__(self, tmp_path: Path, client=None, api_key: str = "", device_name: str = "Kindle Test"):
        self.tmp_path = tmp_path
        self.data_dir = tmp_path / "koreader"
        self.settings_dir = self.data_dir / "settings"
        self.home_dir = tmp_path / "home"
        self.plugins_dir = self.data_dir / "plugins"
        self.patches_dir = self.data_dir / "patches"
        for d in (self.data_dir, self.settings_dir, self.home_dir, self.plugins_dir, self.patches_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.client = client
        self.api_key = api_key
        self.device_name = device_name
        self.shown_widgets: list[dict] = []
        self.restarted = False
        self.opened_reader_paths: list[str] = []

        # Write realistic sibling config.lua for ordpresse.koplugin
        ord_dir = self.plugins_dir / "ordpresse.koplugin"
        ord_dir.mkdir(parents=True, exist_ok=True)
        (ord_dir / "config.lua").write_text(
            f'return {{\n'
            f'    endpoint_url = "http://testserver/api/v1/koreader/lookup",\n'
            f'    api_key = "{api_key}",\n'
            f'    device_name = "{device_name}",\n'
            f'    timeout_seconds = 10,\n'
            f'}}\n',
            encoding="utf-8",
        )
        (ord_dir / "bridge.lua").write_text(BRIDGE_LUA, encoding="utf-8")
        (ord_dir / "_meta.lua").write_text(META_LUA, encoding="utf-8")
        (ord_dir / "main.lua").write_text(MAIN_LUA, encoding="utf-8")

        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self._install_koreader_shims()

    def _py_http_request(self, req_url: str, method: str, headers_tbl, body_str):
        """Bridge LuaSocket socket.http.request() directly into FastAPI TestClient."""
        assert self.client is not None, "TestClient not configured in KOReaderTestEnv"
        parsed = urlparse(req_url)
        path_qs = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        headers = {}
        if headers_tbl is not None:
            for k, v in headers_tbl.items():
                headers[str(k)] = str(v)
        m = (method or "GET").upper()
        kwargs = {"headers": headers}
        if body_str is not None and m in ("POST", "PUT", "PATCH"):
            kwargs["content"] = body_str.encode("utf-8") if isinstance(body_str, str) else body_str
        resp = self.client.request(m, path_qs, **kwargs)
        resp_headers = self.lua.table_from({k.lower(): v for k, v in resp.headers.items()})
        return resp.status_code, resp.content, resp_headers

    def _install_koreader_shims(self):
        env = self

        def lfs_attributes(path: str, attr: str = None):
            p = Path(path)
            if not p.exists():
                return None
            if attr == "mode":
                return "directory" if p.is_dir() else "file"
            if attr == "size":
                return p.stat().st_size
            return env.lua.table_from({
                "mode": "directory" if p.is_dir() else "file",
                "size": p.stat().st_size,
            })

        def py_make_path(path: str):
            Path(path).mkdir(parents=True, exist_ok=True)
            return True

        def py_purge_dir(path: str):
            p = Path(path)
            if p.is_dir():
                shutil.rmtree(p)
                return True
            if p.is_file():
                p.unlink()
                return True
            return False

        def py_record_widget(kind: str, widget):
            env.shown_widgets.append({"kind": kind, "widget": widget})

        def py_mark_restarted():
            env.restarted = True

        def py_open_reader(path: str):
            env.opened_reader_paths.append(path)

        def py_json_encode(obj):
            return py_json.dumps(_lua_to_py(obj))

        def py_json_decode(s):
            parsed = py_json.loads(s)

            def _to_lua(x):
                if isinstance(x, dict):
                    return env.lua.table_from({k: _to_lua(v) for k, v in x.items()})
                if isinstance(x, list):
                    return env.lua.table_from([_to_lua(v) for v in x])
                return x

            return _to_lua(parsed)

        def py_url_escape(s):
            return quote(str(s), safe="")

        def py_url_unescape(s):
            return unquote(str(s))

        def py_sqlite_open(db_path: str, mode: str = "ro"):
            conn = sqlite3.connect(db_path)

            def prepare(self_obj, sql: str):
                cur = conn.execute(sql)
                rows_list = cur.fetchall()

                def rows_iter(stmt_obj):
                    idx = 0

                    def nxt(*_args):
                        nonlocal idx
                        if idx >= len(rows_list):
                            return None
                        r = rows_list[idx]
                        idx += 1
                        return env.lua.table_from(list(r))

                    return nxt

                return env.lua.table_from({
                    "rows": rows_iter,
                    "close": lambda stmt_obj: cur.close(),
                })

            return env.lua.table_from({
                "prepare": prepare,
                "close": lambda self_obj: conn.close(),
            })

        g = self.lua.globals()
        g._PY_DATA_DIR = str(self.data_dir)
        g._PY_SETTINGS_DIR = str(self.settings_dir)
        g._PY_HOME_DIR = str(self.home_dir)
        g._PY_LFS_ATTR = lfs_attributes
        g._PY_MAKE_PATH = py_make_path
        g._PY_PURGE_DIR = py_purge_dir
        g._PY_RECORD_WIDGET = py_record_widget
        g._PY_MARK_RESTARTED = py_mark_restarted
        g._PY_OPEN_READER = py_open_reader
        g._PY_JSON_ENCODE = py_json_encode
        g._PY_JSON_DECODE = py_json_decode
        g._PY_HTTP_REQUEST = self._py_http_request
        g._PY_URL_ESCAPE = py_url_escape
        g._PY_URL_UNESCAPE = py_url_unescape
        g._PY_SQLITE_OPEN = py_sqlite_open

        self.lua.execute(
            """
            G_reader_settings = {
                _data = { home_dir = _PY_HOME_DIR },
                readSetting = function(self, k) return self._data[k] end,
                saveSetting = function(self, k, v) self._data[k] = v end,
                flush = function() end,
            }

            package.loaded["gettext"] = function(s) return s end
            package.loaded["logger"] = { info = function() end, warn = function() end, err = function() end, dbg = function() end }
            package.loaded["datastorage"] = {
                getDataDir = function() return _PY_DATA_DIR end,
                getSettingsDir = function() return _PY_SETTINGS_DIR end,
            }
            package.loaded["libs/libkoreader-lfs"] = {
                attributes = function(path, attr) return _PY_LFS_ATTR(path, attr) end,
            }
            package.loaded["ffi/util"] = {
                purgeDir = function(p) return _PY_PURGE_DIR(p) end,
                basename = function(p) return (p:match("([^/]+)$") or p) end,
                dirname = function(p) return (p:match("^(.*)/[^/]+$") or ".") end,
            }
            package.loaded["util"] = {
                makePath = function(p) return _PY_MAKE_PATH(p) end,
                checkLuaSyntax = function(src)
                    local fn, err = loadstring(src)
                    if not fn then return err end
                    return nil
                end,
                partialMD5 = function(path) return "11112222333344445555666677778888" end,
                cleanupSelectedText = function(s) return s end,
                shell_escape = function(t) return "'" .. tostring(t[1] or "") .. "'" end,
            }
            package.loaded["device"] = {
                canRestart = function() return true end,
                screen = {
                    scaleBySize = function(_, n) return n end,
                    getWidth = function() return 1072 end,
                    getSize = function() return { w = 1072, h = 1448 } end,
                },
            }
            package.loaded["json"] = {
                encode = function(tbl) return _PY_JSON_ENCODE(tbl) end,
                decode = function(str) return _PY_JSON_DECODE(str) end,
            }
            package.loaded["socket"] = { sleep = function() end }
            package.loaded["socket.url"] = {
                escape = function(s) return _PY_URL_ESCAPE(s) end,
                unescape = function(s) return _PY_URL_UNESCAPE(s) end,
            }
            package.loaded["ltn12"] = {
                sink = {
                    table = function(t)
                        return function(chunk)
                            if chunk then table.insert(t, chunk) end
                            return 1
                        end
                    end,
                    file = function(fh)
                        return function(chunk)
                            if chunk then
                                fh:write(chunk)
                            else
                                pcall(function() fh:close() end)
                            end
                            return 1
                        end
                    end,
                },
                source = {
                    string = function(s)
                        local sent = false
                        return function()
                            if sent then return nil end
                            sent = true
                            return s
                        end
                    end,
                },
            }
            package.loaded["socket.http"] = {
                TIMEOUT = 10,
                request = function(opts)
                    local body_parts = {}
                    if opts.source then
                        while true do
                            local chunk = opts.source()
                            if not chunk then break end
                            table.insert(body_parts, chunk)
                        end
                    end
                    local body_str = (#body_parts > 0) and table.concat(body_parts) or nil
                    local code, raw_bytes, resp_headers = _PY_HTTP_REQUEST(opts.url, opts.method or "GET", opts.headers, body_str)
                    if opts.sink then
                        opts.sink(raw_bytes)
                        opts.sink(nil)
                    end
                    return 1, code, resp_headers
                end,
            }
            package.loaded["ui/network/manager"] = {
                isConnected = function() return true end,
                runWhenConnected = function(_, cb) if cb then cb() end end,
                willRerunWhenOnline = function() return false end,
            }
            package.loaded["ui/uimanager"] = {
                show = function(_, w) _PY_RECORD_WIDGET(w._kind or "widget", w) end,
                close = function() end,
                forceRePaint = function() end,
                scheduleIn = function(_, _delay, cb) if cb then cb() end end,
                nextTick = function(_, cb) if cb then cb() end end,
                restartKOReader = function() _PY_MARK_RESTARTED() end,
            }

            local function makeWidgetClass(kind)
                local cls = { _kind = kind }
                cls.__index = cls
                function cls:new(o)
                    o = o or {}
                    o._kind = kind
                    return setmetatable(o, self)
                end
                function cls:extend(o)
                    o = o or {}
                    o.__index = o
                    return setmetatable(o, { __index = self })
                end
                return cls
            end

            package.loaded["ui/widget/container/widgetcontainer"] = makeWidgetClass("WidgetContainer")
            package.loaded["ui/widget/infomessage"] = makeWidgetClass("InfoMessage")
            package.loaded["ui/widget/confirmbox"] = makeWidgetClass("ConfirmBox")
            package.loaded["ui/widget/buttondialog"] = makeWidgetClass("ButtonDialog")
            package.loaded["ui/widget/textviewer"] = makeWidgetClass("TextViewer")
            package.loaded["ui/widget/multiconfirmbox"] = makeWidgetClass("MultiConfirmBox")
            package.loaded["apps/reader/readerui"] = {
                showReader = function(_, path) _PY_OPEN_READER(path) end,
            }
            package.loaded["lua-ljsqlite3/init"] = {
                open = function(path, mode) return _PY_SQLITE_OPEN(path, mode) end,
            }
            """
        )

    def load_ordpresse_plugin(self):
        plugin_path = str(self.plugins_dir / "ordpresse.koplugin" / "main.lua")
        plugin_cls = self.lua.eval(f'dofile("{plugin_path}")')
        instance = plugin_cls.new(plugin_cls, self.lua.table_from({
            "path": str(self.plugins_dir / "ordpresse.koplugin"),
            "ui": self.lua.table_from({
                "menu": self.lua.table_from({"registerToMainMenu": lambda *a: None}),
            }),
        }))
        return instance


# =====================================================================
# 1. Real LuaJIT 2.1 Compilation of EVERY Lua file in the project
# =====================================================================


def test_luajit21_compiles_all_lua_sources():
    """Compile every generated and vendored Lua file in real LuaJIT 2.1 (KOReader's exact VM)."""
    rt = LuaRuntime()
    compile_check = rt.eval(
        "function(src, name) local fn, err = loadstring(src, name) if not fn then return err end return nil end"
    )

    targets = [
        ("MAIN_LUA", MAIN_LUA),
        ("BRIDGE_LUA", BRIDGE_LUA),
        ("META_LUA", META_LUA),
    ]
    for p in sorted(Path("src/ordpresse/vendor/patches").glob("*.lua")):
        targets.append((str(p), p.read_text(encoding="utf-8")))
    for d in (
        "ordpresse-kosync.koplugin",
        "ordpresse-udklip.koplugin",
        "ordpresse-avisen.koplugin",
        "bogdoktor.koplugin",
        "ordpresse_opds.koplugin",
    ):
        for p in sorted((Path("src/ordpresse/vendor") / d).glob("*.lua")):
            targets.append((str(p), p.read_text(encoding="utf-8")))

    for name, src in targets:
        err = compile_check(src, f"@{name}")
        assert err is None, f"LuaJIT 2.1 compile error in {name}: {err}"


# =====================================================================
# 2. Live LuaJIT 2.1 Execution: Store Patch & Plugin Lifecycle
# =====================================================================


def test_luajit21_patch_install_disable_enable_uninstall_lifecycle(tmp_path, client):
    """Execute ordpresse.koplugin v1.6.0 in LuaJIT 2.1: install patch -> [||] pause -> [>] resume -> [×] uninstall."""
    db = Database()
    raw_key, _ = db.create_api_key("LuaJIT Kindle")
    env = KOReaderTestEnv(tmp_path, client=client, api_key=raw_key, device_name="LuaJIT Kindle")
    ordpresse = env.load_ordpresse_plugin()

    # Fetch live catalog via openPluginStore() -> populates ButtonDialog
    ordpresse.openPluginStore(ordpresse)
    assert any(w["kind"] == "ButtonDialog" for w in env.shown_widgets)

    patch_pkg = env.lua.table_from({
        "id": "patch-clean-statusbar",
        "name": "[Patch] Typografisk Bundlinje (Statusbar)",
        "description": "Rolig bundlinje",
        "version": "1.6.0",
        "author": "Ordpresse",
        "category": "patch",
        "target_type": "patch",
        "pkg_type": "patch",
        "target_dir": "patches",
        "koplugin_name": "2-ordpresse-clean-statusbar.lua",
        "target_name": "2-ordpresse-clean-statusbar.lua",
        "can_uninstall": True,
    })

    active_file = env.patches_dir / "2-ordpresse-clean-statusbar.lua"
    disabled_file = env.patches_dir / "2-ordpresse-clean-statusbar.lua.disabled"

    # 1. Install patch over HTTP from FastAPI
    ordpresse.installPlugin(ordpresse, patch_pkg)
    assert active_file.is_file(), "Patch .lua file was not written to koreader/patches/"
    assert "@version 1.6.0" in active_file.read_text(encoding="utf-8")
    assert not disabled_file.exists()

    # 2. [||] Deaktiver -> atomically renames to .lua.disabled
    ordpresse.togglePackageEnable(ordpresse, patch_pkg)
    assert not active_file.exists()
    assert disabled_file.is_file()
    last_confirm = env.shown_widgets[-1]["widget"]
    assert "[||]" in str(last_confirm["text"])

    # Open detail dialog while paused -> must offer [>] Aktiver and show paused status
    env.shown_widgets.clear()
    ordpresse.showPluginDetail(ordpresse, patch_pkg)
    detail_dlg = env.shown_widgets[-1]["widget"]
    assert "[||] Installeret, men sat på pause" in str(detail_dlg["title"])
    btn_texts = [
        str(btn["text"])
        for row in _lua_to_py(detail_dlg["buttons"])
        for btn in row
    ]
    assert "[>] Aktiver" in btn_texts
    assert "[×] Afinstaller fra enhed" in btn_texts

    # 3. [>] Aktiver -> atomically renames back to .lua
    ordpresse.togglePackageEnable(ordpresse, patch_pkg)
    assert active_file.is_file()
    assert not disabled_file.exists()
    assert "[>]" in str(env.shown_widgets[-1]["widget"]["text"])

    # 4. [×] Afinstaller -> confirm in ConfirmBox -> removes patch file completely
    env.shown_widgets.clear()
    ordpresse.uninstallPackage(ordpresse, patch_pkg)
    confirm_box = env.shown_widgets[-1]["widget"]
    assert "[×] Afinstaller" == str(confirm_box["ok_text"])
    confirm_box["ok_callback"]()
    assert not active_file.exists()
    assert not disabled_file.exists()


def test_luajit21_plugin_dir_disable_enable_uninstall_and_core_lock(tmp_path, client):
    """Verify plugin directory pause/resume/uninstall in LuaJIT 2.1 and core 'ordpresse' lock."""
    db = Database()
    raw_key, _ = db.create_api_key("LuaJIT Plugin Dir Tester")
    env = KOReaderTestEnv(tmp_path, client=client, api_key=raw_key)
    ordpresse = env.load_ordpresse_plugin()

    # Seed an installed plugin directory
    p_dir = env.plugins_dir / "ordpresse-kosync.koplugin"
    p_dir.mkdir(parents=True)
    (p_dir / "_meta.lua").write_text('return { name = "ordpresse_kosync", version = "1.6.0" }', encoding="utf-8")
    (p_dir / "main.lua").write_text("return {}", encoding="utf-8")

    plugin_pkg = env.lua.table_from({
        "id": "ordpresse-kosync",
        "name": "[Synk] Ordpresse Læse-Synk",
        "version": "1.6.0",
        "category": "sync",
        "target_type": "plugin",
        "target_dir": "plugins",
        "koplugin_name": "ordpresse-kosync.koplugin",
        "target_name": "ordpresse-kosync.koplugin",
        "can_uninstall": True,
    })

    # Pause plugin dir -> becomes .koplugin.disabled
    ordpresse.togglePackageEnable(ordpresse, plugin_pkg)
    disabled_dir = env.plugins_dir / "ordpresse-kosync.koplugin.disabled"
    assert not p_dir.exists()
    assert disabled_dir.is_dir()
    assert (disabled_dir / "main.lua").is_file()

    # Resume plugin dir -> back to .koplugin
    ordpresse.togglePackageEnable(ordpresse, plugin_pkg)
    assert p_dir.is_dir()
    assert not disabled_dir.exists()

    # Uninstall plugin dir -> completely purged
    env.shown_widgets.clear()
    ordpresse.uninstallPackage(ordpresse, plugin_pkg)
    env.shown_widgets[-1]["widget"]["ok_callback"]()
    assert not p_dir.exists()
    assert not disabled_dir.exists()

    # Core plugin 'ordpresse' must NEVER be paused or uninstalled
    core_pkg = env.lua.table_from({
        "id": "ordpresse",
        "name": "[DK] Den Danske Ordpresse (Kerne)",
        "koplugin_name": "ordpresse.koplugin",
        "can_uninstall": False,
    })
    core_dir = env.plugins_dir / "ordpresse.koplugin"
    env.shown_widgets.clear()
    ordpresse.togglePackageEnable(ordpresse, core_pkg)
    assert core_dir.is_dir()
    assert "kan ikke sættes på pause" in str(env.shown_widgets[-1]["widget"]["text"])

    env.shown_widgets.clear()
    ordpresse.uninstallPackage(ordpresse, core_pkg)
    assert core_dir.is_dir()
    assert "kan ikke afinstalleres" in str(env.shown_widgets[-1]["widget"]["text"])


# =====================================================================
# 3. Live LuaJIT 2.1 Execution of All 3 User Patches
# =====================================================================


def test_luajit21_execute_all_three_user_patches(tmp_path):
    """Execute all 3 KOReader User Patches inside LuaJIT 2.1 and assert their UI behavior."""
    env = KOReaderTestEnv(tmp_path)
    lua = env.lua

    # Patch 3.1: 2-ordpresse-clean-bookshelf.lua
    lua.execute(
        """
        local registered_fn = nil
        package.loaded["userpatch"] = {
            registerPatchPluginFunc = function(name, fn)
                if name == "coverbrowser" then registered_fn = fn end
            end,
        }
        local MosaicMenu = {
            updateItems = function(self, n) return self.item_table end,
        }
        package.loaded["mosaicmenu"] = MosaicMenu
        dofile("src/ordpresse/vendor/patches/2-ordpresse-clean-bookshelf.lua")
        assert(registered_fn ~= nil, "coverbrowser patch function was not registered")
        registered_fn({})

        local mock_menu = {
            item_table = {
                { text = "Lykke-Per.epub", is_directory = false },
                { text = "Artikel.PDF", is_directory = false },
                { text = "Roman.mobi", is_directory = false },
                { text = "Mappe.epub", is_directory = true },
            },
        }
        MosaicMenu.updateItems(mock_menu, 1)
        assert(mock_menu.item_table[1].text == "Lykke-Per", "failed stripping .epub")
        assert(mock_menu.item_table[2].text == "Artikel", "failed stripping .PDF")
        assert(mock_menu.item_table[3].text == "Roman", "failed stripping .mobi")
        assert(mock_menu.item_table[4].text == "Mappe.epub", "directory name should not be stripped")
        """
    )

    # Patch 3.2: 2-ordpresse-clean-statusbar.lua
    lua.execute(
        """
        local ReaderFooter = {
            init = function(self) self.settings = { items_separator = " | " } end,
        }
        package.loaded["apps/reader/modules/readerfooter"] = ReaderFooter
        dofile("src/ordpresse/vendor/patches/2-ordpresse-clean-statusbar.lua")
        local footer = {}
        ReaderFooter.init(footer)
        assert(footer.settings.items_separator == "   ·   ", "unexpected separator: " .. tostring(footer.settings.items_separator))
        assert(footer.settings.progress_style_thin == true, "progress_style_thin was not enabled")
        """
    )

    # Patch 3.3: 2-ordpresse-exlibris-screensaver.lua
    lua.execute(
        """
        local function mkW(kind)
            return { new = function(_, o) o._wtype = kind return o end }
        end
        package.loaded["ffi/blitbuffer"] = { COLOR_WHITE = 255, COLOR_BLACK = 0 }
        package.loaded["ui/font"] = { getFace = function(_, name, sz) return name .. ":" .. tostring(sz) end }
        package.loaded["ui/widget/container/centercontainer"] = mkW("CenterContainer")
        package.loaded["ui/widget/container/framecontainer"] = mkW("FrameContainer")
        package.loaded["ui/widget/overlapgroup"] = mkW("OverlapGroup")
        package.loaded["ui/widget/textwidget"] = mkW("TextWidget")
        package.loaded["ui/widget/verticalgroup"] = mkW("VerticalGroup")
        package.loaded["ui/widget/verticalspan"] = mkW("VerticalSpan")

        local Screensaver = { show = function(self) self.screensaver_widget = { _wtype = "OriginalCover" } end }
        package.loaded["ui/screensaver"] = Screensaver
        dofile("src/ordpresse/vendor/patches/2-ordpresse-exlibris-screensaver.lua")

        local ss = {
            ui = {
                document = {},
                doc_props = { display_title = "Kongens Fald", authors = "Johannes V. Jensen" },
                rolling = { getLastPercent = function() return 0.42 end },
            },
        }
        Screensaver.show(ss)
        assert(ss.screensaver_widget._wtype == "OverlapGroup", "screensaver widget not wrapped")
        local card = ss.screensaver_widget[2][1]
        local vg = card[1]
        assert(vg[1].text == "— EX LIBRIS · ORDPRESSE —")
        assert(vg[3].text == "Kongens Fald")
        assert(vg[4].text == "Johannes V. Jensen  ·  Læst: 42 %")
        """
    )


# =====================================================================
# 4. Live LuaJIT 2.1 Execution of Punkt-5 Plugins against FastAPI!
# =====================================================================


def test_luajit21_kosync_plugin_push_and_pull_live_api(tmp_path, client):
    """Execute ordpresse-kosync.koplugin/main.lua in LuaJIT 2.1 against live FastAPI sync endpoints."""
    db = Database()
    key_a, _ = db.create_api_key("CrossPoint LuaJIT")
    key_b, _ = db.create_api_key("Kindle LuaJIT")

    # Device A pushes progress 72.5%
    env_a = KOReaderTestEnv(tmp_path / "dev_a", client=client, api_key=key_a, device_name="CrossPoint LuaJIT")
    kosync_cls_a = env_a.lua.eval('dofile("src/ordpresse/vendor/ordpresse-kosync.koplugin/main.lua")')
    inst_a = kosync_cls_a.new(kosync_cls_a, env_a.lua.table_from({
        "ui": env_a.lua.table_from({
            "document": env_a.lua.table_from({
                "file": "/books/lykke_per.epub",
                "getXPointer": lambda *a: "/body/DocFragment[9]/body/p[4]",
            }),
            "doc_props": env_a.lua.table_from({"display_title": "Lykke-Per VM", "authors": "Henrik Pontoppidan"}),
            "rolling": env_a.lua.table_from({"getLastPercent": lambda *a: 0.725}),
        }),
    }))
    inst_a.pushProgress(inst_a, True)
    assert "[✓] Læseposition gemt (72.5 %)." == str(env_a.shown_widgets[-1]["widget"]["text"])

    # Device B at 20.0% pulls progress -> gets ConfirmBox -> clicking OK jumps to XPointer
    env_b = KOReaderTestEnv(tmp_path / "dev_b", client=client, api_key=key_b, device_name="Kindle LuaJIT")
    jumped = []
    kosync_cls_b = env_b.lua.eval('dofile("src/ordpresse/vendor/ordpresse-kosync.koplugin/main.lua")')
    inst_b = kosync_cls_b.new(kosync_cls_b, env_b.lua.table_from({
        "ui": env_b.lua.table_from({
            "document": env_b.lua.table_from({
                "file": "/books/lykke_per.epub",
                "getXPointer": lambda *a: "/body/DocFragment[2]/body/p[1]",
            }),
            "doc_props": env_b.lua.table_from({"display_title": "Lykke-Per VM", "authors": "Henrik Pontoppidan"}),
            "rolling": env_b.lua.table_from({
                "getLastPercent": lambda *a: 0.20,
                "onGotoXPointer": lambda _self, xp: jumped.append(xp),
            }),
        }),
    }))
    inst_b.pullProgress(inst_b, False)
    confirm = env_b.shown_widgets[-1]["widget"]
    assert "[↻] Du læste videre på 'CrossPoint LuaJIT' (72.5 %)." in str(confirm["text"])
    confirm["ok_callback"]()
    assert jumped == ["/body/DocFragment[9]/body/p[4]"]


def test_luajit21_udklip_plugin_syncs_annotations_and_vocab_live_api(tmp_path, client, auth_client):
    """Execute ordpresse-udklip.koplugin/main.lua in LuaJIT 2.1 with real SQLite vocabulary_builder.sqlite3."""
    import uuid

    db = Database()
    raw_key, _ = db.create_api_key("Kindle Udklip VM")
    env = KOReaderTestEnv(tmp_path, client=client, api_key=raw_key, device_name="Kindle Udklip VM")

    uniq = uuid.uuid4().hex[:8]
    vocab_word = f"forfærdelig_{uniq}"
    quote_text = f"Citatsætning fra LuaJIT VM ({uniq})."

    # Create a real KOReader vocabulary_builder.sqlite3 in settings_dir!
    vocab_db = env.settings_dir / "vocabulary_builder.sqlite3"
    conn = sqlite3.connect(str(vocab_db))
    conn.execute("CREATE TABLE book_title (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("CREATE TABLE vocabulary (word TEXT, prev_context TEXT, next_context TEXT, create_time INTEGER, title_id INTEGER)")
    conn.execute("INSERT INTO book_title (id, title) VALUES (1, 'Fru Marie Grubbe')")
    conn.execute(
        "INSERT INTO vocabulary (word, prev_context, next_context, create_time, title_id) VALUES (?, 'en', 'storm', 1700000123, 1)",
        (vocab_word,),
    )
    conn.commit()
    conn.close()

    udklip_cls = env.lua.eval('dofile("src/ordpresse/vendor/ordpresse-udklip.koplugin/main.lua")')
    inst = udklip_cls.new(udklip_cls, env.lua.table_from({
        "ui": env.lua.table_from({
            "doc_props": env.lua.table_from({"display_title": "Fru Marie Grubbe", "authors": "J.P. Jacobsen"}),
            "doc_settings": env.lua.table_from({
                "data": env.lua.table_from({
                    "annotations": env.lua.table_from([
                        env.lua.table_from({"text": quote_text, "note": "VM-note", "chapter": "Kap 1", "pageno": 7}),
                    ]),
                }),
            }),
        }),
    }))

    inst.syncAllToServer(inst, True)
    msg = str(env.shown_widgets[-1]["widget"]["text"])
    assert "[✓] Synkroniseret til Ordpresse Web UI!" in msg
    assert "Bogens citater: 1" in msg
    assert "Ord i glosebog: 1" in msg

    # Verify stored on FastAPI server
    lan_res = auth_client.get("/api/v1/udklip")
    assert lan_res.status_code == 200
    body = lan_res.json()
    assert any(h["quote_text"] == quote_text for h in body["highlights"])
    assert any(v["word"] == vocab_word for v in body["vocab_words"])


def test_luajit21_avisen_plugin_downloads_and_opens_washed_article(tmp_path, client, auth_client):
    """Execute ordpresse-avisen.koplugin/main.lua in LuaJIT 2.1: downloads washed EPUB to Artikler/ and opens it."""
    db = Database()
    raw_key, _ = db.create_api_key("Kindle Avis VM")

    # Save a plain-text article via LAN API
    save_res = auth_client.post(
        "/api/v1/articles",
        json={
            "title": "LuaJIT Avis Artikel",
            "author": "Zetland",
            "raw_content": 'Første afsnit om "dansk bogtryk" på e-ink.\n\nAndet afsnit med endnu mere indhold til læseren.',
        },
    )
    assert save_res.status_code == 200
    art = save_res.json()["article"]

    try:
        env = KOReaderTestEnv(tmp_path, client=client, api_key=raw_key, device_name="Kindle Avis VM")
        avisen_cls = env.lua.eval('dofile("src/ordpresse/vendor/ordpresse-avisen.koplugin/main.lua")')
        inst = avisen_cls.new(avisen_cls, env.lua.table_from({}))

        inst.fetchNewArticles(inst)
        confirm = env.shown_widgets[-1]["widget"]
        assert "[✓] Hentede" in str(confirm["text"])
        confirm["ok_callback"]()

        assert len(env.opened_reader_paths) == 1
        downloaded_epub = Path(env.opened_reader_paths[0])
        assert downloaded_epub.is_file()
        assert downloaded_epub.parent.name == "Artikler"
        assert downloaded_epub.read_bytes().startswith(b"PK\x03\x04")
    finally:
        auth_client.delete(f"/api/v1/articles/{art['md5']}")
