"""In-memory generation of the zero-config ordpresse.koplugin.zip for KOReader."""

import io
import re
import zipfile

PLUGIN_VERSION = "1.2.2"
PLUGIN_RELEASE_NOTES = "Rettet: Anna-Fetch crash under installation (ffi/archiver entry.path + sikker staging-udpakning) og stabiliseret OTA-opdatering/genstart."

META_LUA = """local _ = require("gettext")
return {
    name = "ordpresse",
    fullname = _("Den Danske Ordpresse (Online Opslag)"),
    description = _("Slå danske og historiske 1700-1900-tals ord op via din private Ordpresse-server."),
    version = "1.2.2",
}
"""

MAIN_LUA = """local ConfirmBox = require("ui/widget/confirmbox")
local InfoMessage = require("ui/widget/infomessage")
local NetworkMgr = require("ui/network/manager")
local TextViewer = require("ui/widget/textviewer")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local json = require("json")
local ltn12 = require("ltn12")
local socket = require("socket")
local http = require("socket.http")
local url = require("socket.url")
local _ = require("gettext")

local PLUGIN_VERSION = "1.2.2"
local ok_util, util = pcall(require, "util")
local ok_ffiutil, ffiUtil = pcall(require, "ffi/util")
local ok_lfs, lfs = pcall(require, "libs/libkoreader-lfs")
local ok_device, Device = pcall(require, "device")

local plugin_dir = (debug.getinfo(1, "S").source:match("@?(.*/)") or "./")
local ok_cfg, config = pcall(dofile, plugin_dir .. "config.lua")
if not ok_cfg or type(config) ~= "table" then
    config = { endpoint_url = "", api_key = "", timeout_seconds = 10 }
end

local Ordpresse = WidgetContainer:extend{
    name = "ordpresse",
    version = PLUGIN_VERSION,
    is_doc_only = false,
}

local function cleanWord(raw_word)
    local word = (raw_word or ""):gsub("^%s+", ""):gsub("%s+$", "")
    if ok_util and util and util.cleanupSelectedText then
        local ok, cleaned = pcall(util.cleanupSelectedText, word)
        if ok and type(cleaned) == "string" and cleaned ~= "" then
            word = cleaned:gsub("^%s+", ""):gsub("%s+$", "")
        end
    end
    return word
end

local function closeWidgetSafe(widget)
    if widget then
        pcall(function() UIManager:close(widget) end)
    end
end

local function getPluginDir(self)
    if self and type(self.path) == "string" and self.path ~= "" then
        return self.path:gsub("/+$", "") .. "/"
    end
    if plugin_dir:sub(-1) == "/" then
        return plugin_dir
    end
    return plugin_dir .. "/"
end

local function shellEscapeArg(str)
    str = tostring(str or "")
    if ok_util and util and util.shell_escape then
        return util.shell_escape({ str })
    end
    return "'" .. str:gsub("'", "'\\\\''") .. "'"
end

local function ensureDir(path)
    if not path or path == "" then return false end
    if ok_util and util and util.makePath then
        local ok, res = pcall(util.makePath, path)
        if ok and res then return true end
    end
    local ret = os.execute("mkdir -p " .. shellEscapeArg(path))
    return ret == 0 or ret == true
end

local function removePath(path)
    if not path or path == "" or path == "/" then return false end
    if ok_lfs and lfs and lfs.attributes then
        local mode = lfs.attributes(path, "mode")
        if mode == nil then
            pcall(os.remove, path)
            return lfs.attributes(path, "mode") == nil
        elseif mode == "file" then
            local ok = os.remove(path)
            if ok then return true end
        elseif mode == "directory" and ok_ffiutil and ffiUtil and ffiUtil.purgeDir then
            local ok_p, res_p = pcall(ffiUtil.purgeDir, path)
            if ok_p and res_p then return true end
        end
    end
    local ret = os.execute("rm -rf " .. shellEscapeArg(path))
    return ret == 0 or ret == true
end

local function writeFileSafe(path, content)
    local f, err = io.open(path, "wb")
    if not f then
        return false, err
    end
    local ok_w, err_w = f:write(content)
    local ok_c, err_c = f:close()
    if not ok_w or ok_c == false then
        pcall(os.remove, path)
        return false, err_w or err_c or "write failed"
    end
    return true
end

local function writeFileAtomic(path, content)
    local tmp_path = path .. ".tmp"
    local ok_w, err_w = writeFileSafe(tmp_path, content)
    if not ok_w then
        return false, err_w
    end
    local ok_mv, err_mv = os.rename(tmp_path, path)
    if not ok_mv then
        pcall(os.remove, tmp_path)
        return false, err_mv
    end
    return true
end

local function validateLuaSyntax(code_str)
    if type(code_str) ~= "string" or code_str == "" then
        return false, "empty lua code"
    end
    if ok_util and util and util.checkLuaSyntax then
        local syntax_err = util.checkLuaSyntax(code_str)
        if syntax_err then
            return false, syntax_err
        end
        return true
    end
    if loadstring then
        local fn, err = loadstring(code_str)
        if not fn then
            return false, err
        end
    end
    return true
end

function Ordpresse:triggerRestart()
    if ok_device and Device and Device.canRestart and not Device:canRestart() then
        UIManager:show(InfoMessage:new{
            text = _("Genstart venligst KOReader manuelt for at aktivere ændringen."),
        })
        return
    end
    UIManager:nextTick(function()
        if self.ui and self.ui.menu and self.ui.menu.exitOrRestart then
            local ok_exit = pcall(function()
                self.ui.menu:exitOrRestart(function()
                    if UIManager.restartKOReader then
                        UIManager:restartKOReader()
                    end
                end)
            end)
            if ok_exit then
                return
            end
        end
        if UIManager.restartKOReader then
            UIManager:restartKOReader()
        else
            UIManager:show(InfoMessage:new{
                text = _("Genstart venligst KOReader manuelt for at fuldføre ændringen."),
            })
        end
    end)
end

function Ordpresse:init()
    if self.ui and self.ui.menu and self.ui.menu.registerToMainMenu then
        self.ui.menu:registerToMainMenu(self)
    end
    self:registerHooks()
end

function Ordpresse:onReaderReady()
    if self.ui and self.ui.menu and self.ui.menu.registerToMainMenu then
        self.ui.menu:registerToMainMenu(self)
    end
    self:registerHooks()
end

function Ordpresse:registerHooks()
    if not self.ui then return end

    -- Register to Main Menu (Tools -> More tools)
    if self.ui.menu and self.ui.menu.registerToMainMenu then
        pcall(function() self.ui.menu:registerToMainMenu(self) end)
    end

    -- 1. Hook Highlight Dialog (when text or a word is selected/highlighted)
    if self.ui.highlight and self.ui.highlight.addToHighlightDialog then
        pcall(function()
            self.ui.highlight:addToHighlightDialog("09_ordpresse_online", function(this)
                return {
                    text = _("⚡ Slå op i Ordpresse (Online)"),
                    enabled = true,
                    callback = function()
                        local word = ""
                        if this.selected_text then
                            if type(this.selected_text) == "table" and this.selected_text.text then
                                word = this.selected_text.text
                            elseif type(this.selected_text) == "string" then
                                word = this.selected_text
                            end
                        end
                        if this.onClose then
                            pcall(function() this:onClose() end)
                        end
                        self:performLookup(word)
                    end,
                }
            end)
        end)
    end

    -- 2. Hook Dictionary Lookup popup window (modern KOReader Dictionary Button API)
    if self.ui.dictionary and self.ui.dictionary.addToDictButtons then
        pcall(function()
            self.ui.dictionary:addToDictButtons({
                id = "ordpresse_online",
                menu_text = _("Ordpresse Online"),
                text = _("⚡ Ordpresse Online"),
                insert_first = true,
                font_bold = true,
                callback = function(dict_popup)
                    local word = dict_popup.word or dict_popup.lookupword or ""
                    self:performLookup(word)
                end,
            })
            self._dict_hooked = true
        end)
    end
end

-- Fallback for older KOReader versions without ReaderDictionary:addToDictButtons
function Ordpresse:onDictButtonsReady(dict_popup, buttons)
    if self._dict_hooked then return end
    if type(buttons) ~= "table" then return end

    for _, row in ipairs(buttons) do
        if type(row) == "table" then
            for _, btn in ipairs(row) do
                if type(btn) == "table" and btn.id == "ordpresse_online" then
                    return
                end
            end
        end
    end

    table.insert(buttons, 1, {
        {
            id = "ordpresse_online",
            text = _("⚡ Ordpresse Online"),
            font_bold = true,
            callback = function()
                local word = dict_popup.word or dict_popup.lookupword or ""
                self:performLookup(word)
            end,
        }
    })
end

-- Top Reader / Main Menu search & update integration
function Ordpresse:addToMainMenu(menu_items)
    menu_items.ordpresse_online = {
        text = _("⚡ Ordpresse Online Opslag"),
        sorting_hint = "more_tools",
        callback = function()
            self:showManualLookupDialog()
        end,
    }
    menu_items.ordpresse_update = {
        text = _("🔄 Ordpresse: Søg efter opdateringer"),
        sorting_hint = "more_tools",
        callback = function()
            self:checkForUpdates(true)
        end,
    }
    menu_items.ordpresse_store = {
        text = _("🛍️ Ordpresse Plugin Butik"),
        sorting_hint = "more_tools",
        callback = function()
            self:openPluginStore()
        end,
    }
end

function Ordpresse:showManualLookupDialog()
    local ok_input, InputDialog = pcall(require, "ui/widget/inputdialog")
    if not ok_input or not InputDialog then return end

    local dialog
    dialog = InputDialog:new{
        title = _("Den Danske Ordpresse"),
        input_hint = _("Indtast opslagsord..."),
        buttons = {
            {
                {
                    text = _("Annuller"),
                    id = "close",
                    callback = function()
                        UIManager:close(dialog)
                    end,
                },
                {
                    text = _("Slå op"),
                    is_enter_default = true,
                    callback = function()
                        local word = dialog:getInputText()
                        UIManager:close(dialog)
                        if word and word ~= "" then
                            self:performLookup(word)
                        end
                    end,
                },
            },
        },
    }
    UIManager:show(dialog)
    if dialog.onShowKeyboard then
        pcall(function() dialog:onShowKeyboard() end)
    end
end

function Ordpresse:formatFallback(data)
    if not data or type(data.entries) ~= "table" or #data.entries == 0 then
        return _("Ingen resultater fundet i Den Danske Ordpresse for: ") .. tostring(data and data.query or "")
    end
    local lines = {}
    for i, entry in ipairs(data.entries) do
        local header = entry.headword or data.query or ""
        if entry.homonym_index and #data.entries > 1 then
            header = string.format("%d. %s", entry.homonym_index, header)
        end
        if entry.part_of_speech and entry.part_of_speech ~= "" then
            header = header .. " (" .. entry.part_of_speech .. ")"
        end
        table.insert(lines, header)
        if entry.inflections and entry.inflections ~= "" then
            table.insert(lines, "Bøjning: " .. entry.inflections)
        end
        if type(entry.definitions) == "table" then
            for _, def in ipairs(entry.definitions) do
                table.insert(lines, "  • " .. tostring(def))
            end
        end
        if entry.etymology and entry.etymology ~= "" then
            table.insert(lines, "  [Etymologi / Kontekst]: " .. entry.etymology)
        end
        if i < #data.entries then
            table.insert(lines, "\\n" .. string.rep("─", 28) .. "\\n")
        end
    end
    return table.concat(lines, "\\n")
end

function Ordpresse:httpGet(req_url, timeout_override)
    local base_timeout = tonumber(config.timeout_seconds) or 10
    if base_timeout < 10 then base_timeout = 10 end
    local block_timeout = tonumber(timeout_override) or base_timeout
    if block_timeout < base_timeout then block_timeout = base_timeout end
    local total_timeout = block_timeout + 15

    http.TIMEOUT = block_timeout
    local request_fn = http.request
    if req_url:sub(1, 8) == "https://" then
        local ok_https, https = pcall(require, "ssl.https")
        if ok_https and https then
            https.TIMEOUT = block_timeout
            request_fn = https.request
        end
    end

    local ok_su, socketutil = pcall(require, "socketutil")

    local function doAttempt()
        local resp_body = {}
        local sink
        if ok_su and socketutil and socketutil.set_timeout then
            pcall(socketutil.set_timeout, socketutil, block_timeout, total_timeout)
        end
        if ok_su and socketutil and socketutil.table_sink then
            sink = socketutil.table_sink(resp_body)
        else
            sink = ltn12.sink.table(resp_body)
        end

        local ok, res, code_or_err = pcall(function()
            return request_fn({
                url = req_url,
                method = "GET",
                headers = {
                    ["X-API-Key"] = config.api_key or "",
                    ["Accept"] = "application/json, */*",
                    ["User-Agent"] = "KOReader-Ordpresse/" .. PLUGIN_VERSION,
                    ["Connection"] = "close",
                },
                sink = sink,
            })
        end)

        if ok_su and socketutil and socketutil.reset_timeout then
            pcall(socketutil.reset_timeout, socketutil)
        end

        return ok, res, code_or_err, table.concat(resp_body)
    end

    local ok, res, status_code, body = doAttempt()
    -- Cold Wi-Fi / power-save wake-up: retry once automatically if the first attempt glitched
    if not ok or not res then
        pcall(socket.sleep, 0.3)
        ok, res, status_code, body = doAttempt()
    end

    return ok, res, status_code, body
end

function Ordpresse:performLookup(raw_word)
    if type(config.endpoint_url) ~= "string" or config.endpoint_url == "" then
        UIManager:show(InfoMessage:new{ text = _("Ordpresse mangler server-adresse i config.lua.") })
        return
    end
    local word = cleanWord(raw_word)
    if word == "" then
        UIManager:show(InfoMessage:new{ text = _("Intet ord valgt.") })
        return
    end

    if NetworkMgr and NetworkMgr.willRerunWhenOnline then
        if NetworkMgr:willRerunWhenOnline(function() self:performLookup(word) end) then
            return
        end
    end

    local loading = InfoMessage:new{
        text = _("⚡ Henter fra Ordpresse..."),
    }
    UIManager:show(loading)

    UIManager:scheduleIn(0.05, function()
        self:fetchFromServer(word, loading)
    end)
end

function Ordpresse:fetchFromServer(word, loading)
    local req_url = string.format("%s?q=%s", config.endpoint_url, url.escape(word))
    local ok, res, status_code, body = self:httpGet(req_url)

    closeWidgetSafe(loading)

    if not ok or not res then
        local err_detail = tostring(status_code or "timeout")
        UIManager:show(InfoMessage:new{
            text = _("Kunne ikke kontakte Ordpresse-serveren (") .. err_detail .. _(")."),
        })
        return
    end

    if status_code == 401 then
        UIManager:show(InfoMessage:new{
            text = _("Adgang nægtet (HTTP 401): Enhedens API-nøgle er udløbet eller tilbagekaldt."),
        })
        return
    elseif status_code == 422 then
        UIManager:show(InfoMessage:new{
            text = _("Ugyldigt opslag (for lang markering eller ugyldige tegn)."),
        })
        return
    elseif status_code ~= 200 then
        UIManager:show(InfoMessage:new{
            text = _("Fejl fra Ordpresse-server (HTTP ") .. tostring(status_code) .. ").",
        })
        return
    end

    local decode_ok, parsed = pcall(json.decode, body)
    if not decode_ok or type(parsed) ~= "table" then
        UIManager:show(InfoMessage:new{ text = _("Ugyldigt svar fra Ordpresse-serveren.") })
        return
    end

    local text = parsed.display_text
    if not text or text == "" then
        text = self:formatFallback(parsed)
    end

    UIManager:show(TextViewer:new{
        title = _("Den Danske Ordpresse: ") .. word,
        text = text,
    })
end

function Ordpresse:checkForUpdates(interactive)
    if type(config.endpoint_url) ~= "string" or config.endpoint_url == "" then
        if interactive then
            UIManager:show(InfoMessage:new{ text = _("Ordpresse mangler server-adresse i config.lua.") })
        end
        return
    end
    if NetworkMgr and NetworkMgr.willRerunWhenOnline then
        if NetworkMgr:willRerunWhenOnline(function() self:checkForUpdates(interactive) end) then
            return
        end
    end

    local loading = InfoMessage:new{
        text = _("🔄 Søger efter opdateringer til Ordpresse..."),
    }
    UIManager:show(loading)

    UIManager:scheduleIn(0.05, function()
        local check_url = string.format("%s?action=check_update&client_version=%s", config.endpoint_url, url.escape(PLUGIN_VERSION))
        local ok, res, status_code, body = self:httpGet(check_url)

        closeWidgetSafe(loading)

        if not ok or not res or status_code ~= 200 then
            local err_msg = tostring(status_code or "timeout")
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke søge efter opdateringer (") .. err_msg .. _(")."),
            })
            return
        end

        local decode_ok, data = pcall(json.decode, body)
        if not decode_ok or type(data) ~= "table" then
            UIManager:show(InfoMessage:new{ text = _("Ugyldigt opdateringssvar fra serveren.") })
            return
        end

        local latest = data.latest_version
        if not latest or latest == PLUGIN_VERSION then
            if interactive then
                UIManager:show(InfoMessage:new{
                    text = _("Du har allerede den nyeste version af Ordpresse (v") .. PLUGIN_VERSION .. _(")."),
                })
            end
            return
        end

        local notes = data.release_notes and ("\\n\\n" .. _("Nyheder:") .. "\\n" .. tostring(data.release_notes)) or ""
        UIManager:show(ConfirmBox:new{
            text = _("Ny version af Ordpresse-pluginet er tilgængelig!") .. "\\n\\n"
                .. _("Nuværende: v") .. PLUGIN_VERSION .. "\\n"
                .. _("Nyeste: v") .. tostring(latest) .. notes .. "\\n\\n"
                .. _("Vil du hente og installere opdateringen nu?"),
            ok_text = _("Opdater nu"),
            cancel_text = _("Senere"),
            ok_callback = function()
                UIManager:nextTick(function()
                    self:downloadAndApplyUpdate(latest)
                end)
            end,
        })
    end)
end

function Ordpresse:downloadAndApplyUpdate(target_version)
    if type(config.endpoint_url) ~= "string" or config.endpoint_url == "" then
        UIManager:show(InfoMessage:new{ text = _("Ordpresse mangler server-adresse i config.lua.") })
        return
    end
    if NetworkMgr and NetworkMgr.willRerunWhenOnline then
        if NetworkMgr:willRerunWhenOnline(function() self:downloadAndApplyUpdate(target_version) end) then
            return
        end
    end

    local loading = InfoMessage:new{
        text = _("🔄 Henter og installerer opdatering..."),
    }
    UIManager:show(loading)

    UIManager:scheduleIn(0.05, function()
        local dl_url = string.format("%s?action=download_update", config.endpoint_url)
        local ok, res, status_code, body = self:httpGet(dl_url, 30)

        closeWidgetSafe(loading)

        if not ok or not res or status_code ~= 200 then
            local err_msg = tostring(status_code or "timeout")
            UIManager:show(InfoMessage:new{
                text = _("Fejl under hentning af opdatering (") .. err_msg .. _(")."),
            })
            return
        end

        local decode_ok, data = pcall(json.decode, body)
        if not decode_ok or type(data) ~= "table" or type(data.main_lua) ~= "string" or #data.main_lua < 500 then
            UIManager:show(InfoMessage:new{ text = _("Modtog ugyldig plugin-kode fra serveren.") })
            return
        end

        local syntax_ok, syntax_err = validateLuaSyntax(data.main_lua)
        if not syntax_ok then
            UIManager:show(InfoMessage:new{
                text = _("Modtog ugyldig Lua-syntaks fra serveren: ") .. tostring(syntax_err),
            })
            return
        end

        local base_dir = getPluginDir(self)
        local file_path = base_dir .. "main.lua"
        local write_ok, write_err = writeFileAtomic(file_path, data.main_lua)
        if not write_ok then
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke gemme opdatering: ") .. tostring(write_err),
            })
            return
        end

        if type(data.meta_lua) == "string" and #data.meta_lua > 50 and validateLuaSyntax(data.meta_lua) then
            writeFileAtomic(base_dir .. "_meta.lua", data.meta_lua)
        end

        local shown_version = tostring(data.version or target_version or "")
        UIManager:show(ConfirmBox:new{
            text = _("✅ Ordpresse er opdateret til v") .. shown_version .. _("!") .. "\\n\\n"
                .. _("KOReader skal genstartes for at aktivere den nye version.") .. "\\n"
                .. _("Vil du genstarte KOReader nu?"),
            ok_text = _("Genstart nu"),
            cancel_text = _("Senere"),
            ok_callback = function()
                self:triggerRestart()
            end,
        })
    end)
end

function Ordpresse:openPluginStore()
    if type(config.endpoint_url) ~= "string" or config.endpoint_url == "" then
        UIManager:show(InfoMessage:new{ text = _("Ordpresse mangler server-adresse i config.lua.") })
        return
    end
    if NetworkMgr and NetworkMgr.willRerunWhenOnline then
        if NetworkMgr:willRerunWhenOnline(function() self:openPluginStore() end) then
            return
        end
    end

    local loading = InfoMessage:new{
        text = _("🛍️ Henter plugin-katalog..."),
    }
    UIManager:show(loading)

    UIManager:scheduleIn(0.05, function()
        local catalog_url = string.format("%s?action=store_catalog", config.endpoint_url)
        local ok, res, status_code, body = self:httpGet(catalog_url)

        closeWidgetSafe(loading)

        if not ok or not res or status_code ~= 200 then
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke hente plugin-kataloget (") .. tostring(status_code or "timeout") .. _(")."),
            })
            return
        end

        local decode_ok, catalog = pcall(json.decode, body)
        if not decode_ok or type(catalog) ~= "table" then
            UIManager:show(InfoMessage:new{ text = _("Ugyldigt katalog-svar fra serveren.") })
            return
        end

        if #catalog == 0 then
            UIManager:show(InfoMessage:new{ text = _("Plugin-butikken er tom.") })
            return
        end

        self:showPluginCatalog(catalog)
    end)
end

function Ordpresse:showPluginCatalog(catalog)
    self._last_catalog = catalog
    local ok_bd, ButtonDialog = pcall(require, "ui/widget/buttondialog")
    if not ok_bd or not ButtonDialog then
        -- Fallback: vis som tekst
        local lines = {}
        for _, p in ipairs(catalog) do
            table.insert(lines, p.name .. " (v" .. p.version .. ")")
            table.insert(lines, "  " .. p.description)
            table.insert(lines, "")
        end
        UIManager:show(TextViewer:new{
            title = _("🛍️ Plugin Butik"),
            text = table.concat(lines, "\\n"),
        })
        return
    end

    local buttons = {}
    for _, p in ipairs(catalog) do
        table.insert(buttons, {
            {
                text = p.name .. "  (v" .. p.version .. ")",
                callback = function()
                    self:showPluginDetail(p)
                end,
            },
        })
    end

    -- Tilføj luk-knap
    table.insert(buttons, {
        {
            text = _("Luk"),
            callback = function()
                closeWidgetSafe(self._store_dialog)
                self._store_dialog = nil
            end,
        },
    })

    self._store_dialog = ButtonDialog:new{
        title = _("🛍️ Ordpresse Plugin Butik"),
        buttons = buttons,
    }
    UIManager:show(self._store_dialog)
end

function Ordpresse:showPluginDetail(plugin)
    closeWidgetSafe(self._store_dialog)
    self._store_dialog = nil

    local detail_lines = {
        plugin.name,
        "",
        plugin.description,
        "",
        "Forfatter: " .. (plugin.author or "ukendt"),
        "Version: " .. (plugin.version or "?"),
        "Licens: " .. (plugin.license or "?"),
        "Størrelse: ~" .. tostring(plugin.size_estimate_kb or "?") .. " KB",
    }
    if plugin.install_note and plugin.install_note ~= "" then
        table.insert(detail_lines, "")
        table.insert(detail_lines, "ℹ️ " .. plugin.install_note)
    end

    UIManager:show(ConfirmBox:new{
        text = table.concat(detail_lines, "\\n"),
        ok_text = _("📦 Installer"),
        cancel_text = _("Tilbage"),
        ok_callback = function()
            UIManager:nextTick(function()
                self:installPlugin(plugin)
            end)
        end,
        cancel_callback = function()
            if self._last_catalog then
                UIManager:nextTick(function()
                    self:showPluginCatalog(self._last_catalog)
                end)
            end
        end,
    })
end

function Ordpresse:installPlugin(plugin)
    if type(plugin) ~= "table" or type(plugin.id) ~= "string" or type(plugin.koplugin_name) ~= "string" then
        UIManager:show(InfoMessage:new{ text = _("Ugyldige plugin-data.") })
        return
    end
    if type(config.endpoint_url) ~= "string" or config.endpoint_url == "" then
        UIManager:show(InfoMessage:new{ text = _("Ordpresse mangler server-adresse i config.lua.") })
        return
    end
    if NetworkMgr and NetworkMgr.willRerunWhenOnline then
        if NetworkMgr:willRerunWhenOnline(function() self:installPlugin(plugin) end) then
            return
        end
    end

    local loading = InfoMessage:new{
        text = _("📦 Henter ") .. tostring(plugin.name or plugin.id) .. _("..."),
    }
    UIManager:show(loading)

    UIManager:scheduleIn(0.05, function()
        local dl_url = string.format(
            "%s?action=store_download&id=%s",
            config.endpoint_url,
            url.escape(plugin.id)
        )

        -- Download zip-data via httpGet (60s block timeout for større zipballs)
        local ok, res, status_code, body = self:httpGet(dl_url, 60)

        closeWidgetSafe(loading)

        if not ok or not res or status_code ~= 200 then
            UIManager:show(InfoMessage:new{
                text = _("Fejl ved download af plugin (") .. tostring(status_code or "timeout") .. _(")."),
            })
            return
        end

        if not body or #body < 100 then
            UIManager:show(InfoMessage:new{
                text = _("Modtog tom eller ugyldig plugin-pakke."),
            })
            return
        end

        local ok_ds, DataStorage = pcall(require, "datastorage")
        if not ok_ds or not DataStorage then
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke tilgå KOReader-datamappen."),
            })
            return
        end

        -- Gem zip-filen midlertidigt i cache/
        local data_dir = DataStorage:getDataDir()
        local tmp_dir = data_dir .. "/cache"
        if not ensureDir(tmp_dir) then
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke oprette cache-mappen."),
            })
            return
        end

        local safe_id = plugin.id:gsub("[^%w%._%-]", "_")
        local zip_path = tmp_dir .. "/ordpresse_" .. safe_id .. ".zip"
        local zip_ok, zip_err = writeFileSafe(zip_path, body)
        if not zip_ok then
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke gemme plugin-filen: ") .. tostring(zip_err),
            })
            return
        end

        -- Udpak til midlertidig staging-mappe (.tmp suffix ignoreres af KOReaders PluginLoader)
        local safe_koplugin = plugin.koplugin_name:gsub("[^%w%._%-]", "_")
        local plugins_dir = data_dir .. "/plugins"
        local target_dir = plugins_dir .. "/" .. safe_koplugin
        local staging_dir = plugins_dir .. "/." .. safe_koplugin .. ".tmp"

        if not ensureDir(plugins_dir) then
            pcall(os.remove, zip_path)
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke oprette plugins-mappen."),
            })
            return
        end

        removePath(staging_dir)

        local extract_ok = false
        local extracted_files = 0
        local extract_err = nil

        -- Metode 1: Forsøg ffi/archiver (moderne KOReader)
        -- Bemærk: reader:iterate() returnerer plain Lua tables { path = ..., mode = ..., size = ... }
        local ok_arc, Archiver = pcall(require, "ffi/archiver")
        if ok_arc and Archiver and Archiver.Reader and ensureDir(staging_dir) then
            local arc_ok, arc_err = pcall(function()
                local reader = Archiver.Reader:new()
                if not reader:open(zip_path) then
                    error(reader.err or "kunne ikke åbne zip-arkiv")
                end
                local prefix = nil
                local run_ok, run_err = pcall(function()
                    for entry in reader:iterate() do
                        local raw_path = entry and entry.path
                        if type(raw_path) == "string" and raw_path ~= "" then
                            local clean_path = raw_path:gsub("^%./+", "")
                            if prefix == nil then
                                prefix = clean_path:match("^([^/]+/)") or ""
                            end

                            local relative = clean_path
                            if prefix ~= "" then
                                if clean_path:sub(1, #prefix) == prefix then
                                    relative = clean_path:sub(#prefix + 1)
                                else
                                    relative = ""
                                end
                            end

                            local is_dir = (entry.mode == "directory") or (relative:sub(-1) == "/")
                            local has_traversal = ("/" .. relative .. "/"):find("/../", 1, true) ~= nil
                            if relative ~= ""
                                and relative:sub(1, 1) ~= "/"
                                and not relative:match("^%.")
                                and not has_traversal then
                                local rel_clean = is_dir and relative:gsub("/+$", "") or relative
                                if rel_clean ~= "" then
                                    local dest = staging_dir .. "/" .. rel_clean
                                    if is_dir then
                                        if not ensureDir(dest) then
                                            error("kunne ikke oprette mappe: " .. rel_clean)
                                        end
                                    elseif entry.mode == "file" or entry.mode == nil then
                                        local parent = dest:match("^(.*)/[^/]+$")
                                        if parent and not ensureDir(parent) then
                                            error("kunne ikke oprette mappe til fil: " .. rel_clean)
                                        end
                                        local entry_size = tonumber(entry.size) or 0
                                        local content
                                        if entry_size == 0 then
                                            content = ""
                                        else
                                            content = reader:extractToMemory(raw_path)
                                            if content == nil then
                                                error(reader.err or ("udpakningsfejl: " .. rel_clean))
                                            end
                                        end
                                        local w_ok, w_err = writeFileSafe(dest, content)
                                        if not w_ok then
                                            error("skrivefejl: " .. tostring(w_err))
                                        end
                                        extracted_files = extracted_files + 1
                                    end
                                end
                            end
                        end
                    end
                end)
                pcall(reader.close, reader)
                if not run_ok then
                    error(run_err)
                end
            end)
            if arc_ok and extracted_files > 0 then
                extract_ok = true
            else
                extract_err = arc_err
                removePath(staging_dir)
                extracted_files = 0
            end
        end

        -- Metode 2: Fallback til system unzip (ældre KOReader / Kindle / hvis ffi/archiver fejler)
        if not extract_ok then
            removePath(staging_dir)
            local tmp_extract = tmp_dir .. "/ordpresse_extract_" .. safe_id
            removePath(tmp_extract)
            if ensureDir(tmp_extract) then
                local unzip_ret = os.execute("unzip -o -q " .. shellEscapeArg(zip_path) .. " -d " .. shellEscapeArg(tmp_extract))
                if unzip_ret == 0 or unzip_ret == true then
                    local entries = {}
                    if ok_lfs and lfs and lfs.dir then
                        local ok_d, iter, dir_obj = pcall(lfs.dir, tmp_extract)
                        if ok_d then
                            for name in iter, dir_obj do
                                if name ~= "." and name ~= ".." then
                                    table.insert(entries, name)
                                end
                            end
                        end
                    else
                        local handle = io.popen("ls -1A " .. shellEscapeArg(tmp_extract))
                        if handle then
                            for line in handle:lines() do
                                if line ~= "" then
                                    table.insert(entries, line)
                                end
                            end
                            handle:close()
                        end
                    end

                    local src_dir = nil
                    if #entries == 1 then
                        local candidate = tmp_extract .. "/" .. entries[1]
                        if not ok_lfs or not lfs or lfs.attributes(candidate, "mode") == "directory" then
                            src_dir = candidate
                        end
                    elseif #entries > 1 then
                        src_dir = tmp_extract
                    end

                    if src_dir then
                        local mv_ok = os.rename(src_dir, staging_dir)
                        if not mv_ok then
                            local mv_ret = os.execute("mv " .. shellEscapeArg(src_dir) .. " " .. shellEscapeArg(staging_dir))
                            mv_ok = (mv_ret == 0 or mv_ret == true)
                        end
                        if mv_ok then
                            extract_ok = true
                        end
                    end
                end
            end
            removePath(tmp_extract)
        end

        pcall(os.remove, zip_path)

        -- Bekræft at den udpakkede mappe faktisk indeholder gyldige plugin-filer
        if extract_ok and ok_lfs and lfs and lfs.attributes then
            local has_main = lfs.attributes(staging_dir .. "/main.lua", "mode") == "file"
            local has_meta = lfs.attributes(staging_dir .. "/_meta.lua", "mode") == "file"
            if not has_main and not has_meta then
                extract_ok = false
                extract_err = "manglende main.lua/_meta.lua i arkiv"
            end
        end

        if not extract_ok then
            removePath(staging_dir)
            local detail = extract_err and (" (" .. tostring(extract_err) .. ")") or ""
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke udpakke plugin-pakken") .. detail .. _(". Prøv evt. at genstarte KOReader og forsøg igen."),
            })
            return
        end

        -- Flyt staging_dir atomisk til target_dir (fjerner også evt. gammel tom mappe)
        if not removePath(target_dir) then
            removePath(staging_dir)
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke erstatte den eksisterende plugin-mappe."),
            })
            return
        end

        local final_ok, rename_err = os.rename(staging_dir, target_dir)
        if not final_ok then
            local mv_ret = os.execute("mv " .. shellEscapeArg(staging_dir) .. " " .. shellEscapeArg(target_dir))
            final_ok = (mv_ret == 0 or mv_ret == true)
        end

        if not final_ok then
            removePath(staging_dir)
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke færdiggøre installationen: ") .. tostring(rename_err or "flytning fejlede"),
            })
            return
        end

        -- Succes! Spørg om genstart
        UIManager:show(ConfirmBox:new{
            text = _("✅ ") .. tostring(plugin.name or plugin.id) .. _(" er installeret!") .. "\\n\\n"
                .. _("KOReader skal genstartes for at aktivere pluginet.") .. "\\n"
                .. _("Vil du genstarte KOReader nu?"),
            ok_text = _("Genstart nu"),
            cancel_text = _("Senere"),
            ok_callback = function()
                self:triggerRestart()
            end,
        })
    end)
end

return Ordpresse
"""


def _escape_lua_string(val: str) -> str:
    """Escape backslashes and double quotes for Lua string literals."""
    return val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")


def slugify_device_name(name: str) -> str:
    """Generate a clean URL/filename slug for the device name."""
    clean = re.sub(r"[^a-zA-Z0-9_\-]+", "-", name.strip().lower())
    clean = re.sub(r"-+", "-", clean).strip("-")
    return clean or "device"


def build_koplugin_zip(device_name: str, raw_api_key: str, endpoint_url: str) -> bytes:
    """Generate in-memory ordpresse.koplugin.zip for KOReader."""
    clean_endpoint = endpoint_url.strip() or "https://ordpresse.seame.click/api/v1/koreader/lookup"
    config_lua = f"""return {{
    endpoint_url = "{_escape_lua_string(clean_endpoint)}",
    api_key = "{_escape_lua_string(raw_api_key)}",
    device_name = "{_escape_lua_string(device_name)}",
    timeout_seconds = 10,
}}
"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ordpresse.koplugin/_meta.lua", META_LUA)
        zf.writestr("ordpresse.koplugin/config.lua", config_lua)
        zf.writestr("ordpresse.koplugin/main.lua", MAIN_LUA)

    return buf.getvalue()

