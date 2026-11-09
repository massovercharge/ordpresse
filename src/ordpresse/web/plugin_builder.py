"""In-memory generation of the zero-config ordpresse.koplugin.zip for KOReader."""

import io
import re
import zipfile

PLUGIN_VERSION = "1.2.1"
PLUGIN_RELEASE_NOTES = "Ny: 🛍️ OTA Plugin Butik. Rettet: Menupunkter i 'Flere værktøjer' registreres nu korrekt i KOReader."

META_LUA = """local _ = require("gettext")
return {
    name = "ordpresse",
    fullname = _("Den Danske Ordpresse (Online Opslag)"),
    description = _("Slå danske og historiske 1700-1900-tals ord op via din private Ordpresse-server."),
    version = "1.2.1",
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

local PLUGIN_VERSION = "1.2.1"
local ok_util, util = pcall(require, "util")

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

function Ordpresse:httpGet(req_url)
    local timeout = tonumber(config.timeout_seconds) or 10
    if timeout < 10 then timeout = 10 end

    socket.http.TIMEOUT = timeout
    local request_fn = http.request
    if req_url:sub(1, 8) == "https://" then
        local ok_https, https = pcall(require, "ssl.https")
        if ok_https and https then
            https.TIMEOUT = timeout
            request_fn = https.request
        end
    end

    local ok_su, socketutil = pcall(require, "socketutil")

    local function doAttempt()
        local resp_body = {}
        if ok_su and socketutil and socketutil.set_timeout then
            pcall(socketutil.set_timeout, socketutil, timeout, timeout + 5)
        end

        local ok, res, code_or_err = pcall(function()
            return request_fn({
                url = req_url,
                method = "GET",
                headers = {
                    ["X-API-Key"] = config.api_key,
                    ["Accept"] = "application/json",
                    ["User-Agent"] = "KOReader-Ordpresse/" .. PLUGIN_VERSION,
                    ["Connection"] = "close",
                },
                sink = ltn12.sink.table(resp_body),
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
        socket.sleep(0.3)
        ok, res, status_code, body = doAttempt()
    end

    return ok, res, status_code, body
end

function Ordpresse:performLookup(raw_word)
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

    if loading then
        pcall(function() UIManager:close(loading) end)
    end

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

        UIManager:close(loading)

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
                self:downloadAndApplyUpdate(latest)
            end,
        })
    end)
end

function Ordpresse:downloadAndApplyUpdate(target_version)
    local loading = InfoMessage:new{
        text = _("🔄 Henter og installerer opdatering..."),
    }
    UIManager:show(loading)

    UIManager:scheduleIn(0.05, function()
        local dl_url = string.format("%s?action=download_update", config.endpoint_url)
        local ok, res, status_code, body = self:httpGet(dl_url)

        UIManager:close(loading)

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

        local file_path = plugin_dir .. "main.lua"
        local f, err = io.open(file_path, "w")
        if not f then
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke gemme opdatering: ") .. tostring(err),
            })
            return
        end

        f:write(data.main_lua)
        f:close()

        UIManager:show(ConfirmBox:new{
            text = _("✅ Ordpresse er opdateret til v") .. tostring(target_version) .. _("!") .. "\\n\\n"
                .. _("KOReader skal genstartes for at aktivere den nye version.") .. "\\n"
                .. _("Vil du genstarte KOReader nu?"),
            ok_text = _("Genstart nu"),
            cancel_text = _("Senere"),
            ok_callback = function()
                if UIManager.restartKOReader then
                    UIManager:restartKOReader()
                else
                    UIManager:show(InfoMessage:new{
                        text = _("Genstart venligst KOReader manuelt for at fuldføre opdateringen."),
                    })
                end
            end,
        })
    end)
end

function Ordpresse:openPluginStore()
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

        UIManager:close(loading)

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
                UIManager:close(self._store_dialog)
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
    if self._store_dialog then
        UIManager:close(self._store_dialog)
    end

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
            self:installPlugin(plugin)
        end,
        cancel_callback = function()
            if self._last_catalog then
                self:showPluginCatalog(self._last_catalog)
            end
        end,
    })
end

function Ordpresse:installPlugin(plugin)
    local loading = InfoMessage:new{
        text = _("📦 Henter ") .. plugin.name .. _("..."),
    }
    UIManager:show(loading)

    UIManager:scheduleIn(0.05, function()
        local dl_url = string.format(
            "%s?action=store_download&id=%s",
            config.endpoint_url,
            url.escape(plugin.id)
        )

        -- Download zip-data via httpGet (som returner body som string)
        local ok, res, status_code, body = self:httpGet(dl_url)

        UIManager:close(loading)

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

        -- Gem zip-filen midlertidigt
        local DataStorage = require("datastorage")
        local tmp_dir = DataStorage:getDataDir() .. "/cache"
        os.execute("mkdir -p " .. tmp_dir)
        local zip_path = tmp_dir .. "/" .. plugin.id .. ".zip"

        local f = io.open(zip_path, "wb")
        if not f then
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke gemme plugin-filen."),
            })
            return
        end
        f:write(body)
        f:close()

        -- Udpak med KOReaders ffi/archiver (kræver KOReader 2025.08+)
        -- Fallback til unzip-kommando for ældre versioner
        local plugins_dir = DataStorage:getDataDir() .. "/plugins"
        local target_dir = plugins_dir .. "/" .. plugin.koplugin_name

        local extract_ok = false

        -- Metode 1: Forsøg ffi/archiver (moderne KOReader)
        local ok_arc, Archiver = pcall(require, "ffi/archiver")
        if ok_arc and Archiver and Archiver.Reader then
            local reader = Archiver.Reader:new()
            if reader:open(zip_path) then
                -- GitHub zipballs har en top-level mappe: "owner-repo-hash/"
                -- Vi skal finde den og mappe filerne til target_dir
                os.execute("mkdir -p " .. target_dir)

                local prefix = nil
                for entry in reader:iterate() do
                    local entry_path = entry:getPath() or entry.path or ""
                    -- Detekter GitHub's top-level mappe-præfiks
                    if not prefix and entry_path:match("^[^/]+/$") then
                        prefix = entry_path
                    end

                    if prefix and entry_path:sub(1, #prefix) == prefix then
                        local relative = entry_path:sub(#prefix + 1)
                        if relative ~= "" and not relative:match("^%.") then
                            local dest = target_dir .. "/" .. relative
                            if entry_path:sub(-1) == "/" then
                                os.execute("mkdir -p " .. dest)
                            else
                                local content = reader:extractToMemory(entry_path)
                                if content then
                                    -- Sørg for at parent-mappen eksisterer
                                    local parent = dest:match("(.*/)")
                                    if parent then
                                        os.execute("mkdir -p " .. parent)
                                    end
                                    local out = io.open(dest, "wb")
                                    if out then
                                        out:write(content)
                                        out:close()
                                    end
                                end
                            end
                        end
                    end
                end
                reader:close()
                extract_ok = true
            end
        end

        -- Metode 2: Fallback til system unzip (ældre KOReader / Kindle)
        if not extract_ok then
            -- Udpak til tmp, find mappen, flyt til plugins
            local tmp_extract = tmp_dir .. "/plugin_extract_" .. plugin.id
            os.execute("rm -rf " .. tmp_extract)
            os.execute("mkdir -p " .. tmp_extract)
            local unzip_ret = os.execute("unzip -o -q " .. zip_path .. " -d " .. tmp_extract)
            if unzip_ret == 0 or unzip_ret == true then
                -- Find den udpakkede top-level mappe (GitHub zipball)
                local handle = io.popen("ls -1 " .. tmp_extract)
                if handle then
                    local extracted_name = handle:read("*l")
                    handle:close()
                    if extracted_name and extracted_name ~= "" then
                        local src = tmp_extract .. "/" .. extracted_name
                        os.execute("rm -rf " .. target_dir)
                        os.execute("mv " .. src .. " " .. target_dir)
                        extract_ok = true
                    end
                end
            end
            os.execute("rm -rf " .. tmp_extract)
        end

        -- Ryd op
        os.remove(zip_path)

        if not extract_ok then
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke udpakke plugin-pakken. Prøv at genstarte KOReader og forsøg igen."),
            })
            return
        end

        -- Succes! Spørg om genstart
        UIManager:show(ConfirmBox:new{
            text = _("✅ ") .. plugin.name .. _(" er installeret!") .. "\\n\\n"
                .. _("KOReader skal genstartes for at aktivere pluginet.") .. "\\n"
                .. _("Vil du genstarte KOReader nu?"),
            ok_text = _("Genstart nu"),
            cancel_text = _("Senere"),
            ok_callback = function()
                if UIManager.restartKOReader then
                    UIManager:restartKOReader()
                else
                    UIManager:show(InfoMessage:new{
                        text = _("Genstart venligst KOReader manuelt."),
                    })
                end
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

