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

MAIN_LUA = """local DictQuickLookup = require("ui/widget/dictquicklookup")
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

local plugin_dir = (debug.getinfo(1, "S").source:match("@?(.*/)") or "./")
local ok_cfg, config = pcall(dofile, plugin_dir .. "config.lua")
if not ok_cfg or type(config) ~= "table" then
    config = { endpoint_url = "", api_key = "", timeout_seconds = 5 }
end

local Ordpresse = WidgetContainer:extend{
    name = "ordpresse",
    is_doc_only = false,
}

function Ordpresse:init()
    self:registerHighlightMenu()
    self:hookDictQuickLookup()
end

function Ordpresse:registerHighlightMenu()
    if not self.ui or not self.ui.highlight then return end
    pcall(function()
        self.ui.highlight:addToHighlightDialog("09_ordpresse_online", function(this)
            return {
                text = _("⚡ Slå op i Ordpresse (Online)"),
                enabled = true,
                callback = function()
                    local selected = this.selected_text and this.selected_text.text or ""
                    this:onClose()
                    self:performLookup(selected)
                end,
            }
        end)
    end)
end

function Ordpresse:hookDictQuickLookup()
    if DictQuickLookup._ordpresse_hooked then return end
    DictQuickLookup._ordpresse_hooked = true

    local orig_tweak = DictQuickLookup.tweakButtons
    DictQuickLookup.tweakButtons = function(dict_popup, buttons)
        if orig_tweak then
            pcall(orig_tweak, dict_popup, buttons)
        end
        pcall(function()
            if type(buttons) == "table" then
                table.insert(buttons, 1, {
                    {
                        id = "ordpresse_online_btn",
                        text = _("⚡ Ordpresse Online"),
                        font_bold = true,
                        callback = function()
                            local word = dict_popup.word or dict_popup.lookupword or ""
                            self:performLookup(word)
                        end,
                    }
                })
            end
        end)
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
    local word = (raw_word or ""):gsub("^%s+", ""):gsub("%s+$", "")
    if word == "" then
        UIManager:show(InfoMessage:new{ text = _("Intet ord valgt.") })
        return
    end
    NetworkMgr:runWhenOnline(function()
        self:fetchFromServer(word)
    end)
end

function Ordpresse:fetchFromServer(word)
    local req_url = string.format("%s?q=%s", config.endpoint_url, url.escape(word))
    local resp_body = {}
    local timeout = config.timeout_seconds or 5
    socket.http.TIMEOUT = timeout

    local request_fn = http.request
    if req_url:sub(1, 8) == "https://" then
        local ok_https, https = pcall(require, "ssl.https")
        if ok_https and https then
            https.TIMEOUT = timeout
            request_fn = https.request
        end
    end

    local ok, res, status_code = pcall(function()
        return request_fn({
            url = req_url,
            method = "GET",
            headers = {
                ["X-API-Key"] = config.api_key,
                ["Accept"] = "application/json",
                ["User-Agent"] = "KOReader-Ordpresse/1.0",
            },
            sink = ltn12.sink.table(resp_body),
        })
    end)

    if not ok or not res then
        UIManager:show(InfoMessage:new{
            text = _("Kunne ikke kontakte Ordpresse-serveren (timeout eller netværksfejl)."),
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
    timeout_seconds = 5,
}}
"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ordpresse.koplugin/_meta.lua", META_LUA)
        zf.writestr("ordpresse.koplugin/config.lua", config_lua)
        zf.writestr("ordpresse.koplugin/main.lua", MAIN_LUA)

    return buf.getvalue()
