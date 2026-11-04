"""In-memory generation of the zero-config ordpresse.koplugin.zip for KOReader."""

import io
import re
import zipfile

META_LUA = """local _ = require("gettext")
return {
    name = "ordpresse",
    fullname = _("Den Danske Ordpresse (Online Opslag)"),
    description = _("Slå danske og historiske 1700-1900-tals ord op via din private Ordpresse-server."),
}
"""

MAIN_LUA = """local InfoMessage = require("ui/widget/infomessage")
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

local ok_util, util = pcall(require, "util")

local plugin_dir = (debug.getinfo(1, "S").source:match("@?(.*/)") or "./")
local ok_cfg, config = pcall(dofile, plugin_dir .. "config.lua")
if not ok_cfg or type(config) ~= "table" then
    config = { endpoint_url = "", api_key = "", timeout_seconds = 10 }
end

local Ordpresse = WidgetContainer:extend{
    name = "ordpresse",
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
    self:registerHooks()
end

function Ordpresse:onReaderReady()
    self:registerHooks()
end

function Ordpresse:registerHooks()
    if not self.ui then return end

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

-- Top Reader / Main Menu search integration
function Ordpresse:addToMainMenu(menu_items)
    menu_items.ordpresse_online = {
        text = _("⚡ Ordpresse Online Opslag"),
        sorting_hint = "more_tools",
        callback = function()
            self:showManualLookupDialog()
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

    local function doRequest()
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
                    ["User-Agent"] = "KOReader-Ordpresse/1.0",
                    ["Connection"] = "close",
                },
                sink = ltn12.sink.table(resp_body),
            })
        end)

        if ok_su and socketutil and socketutil.reset_timeout then
            pcall(socketutil.reset_timeout, socketutil)
        end

        return ok, res, code_or_err, resp_body
    end

    local ok, res, status_code, resp_body = doRequest()

    -- Cold Wi-Fi / power-save wake-up: retry once automatically if the first attempt glitched
    if not ok or not res then
        socket.sleep(0.3)
        ok, res, status_code, resp_body = doRequest()
    end

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

    local decode_ok, parsed = pcall(json.decode, table.concat(resp_body))
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

