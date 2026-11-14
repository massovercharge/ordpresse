"""In-memory generation of the zero-config ordpresse.koplugin.zip for KOReader."""

import io
import re
import zipfile

PLUGIN_VERSION = "1.3.0"
PLUGIN_RELEASE_NOTES = "Ny v1.3.0: Samlet Ordpresse Bridge (ordpresse.bridge), resources butik-kategori (skrifttyper og dansk orddeling), versions-tjek med opdateringsindikator og Opdater alle, samt sikker rollback ved fejl og crash."

META_LUA = """local _ = require("gettext")
return {
    name = "ordpresse",
    fullname = _("Den Danske Ordpresse (Online Opslag & Butik)"),
    description = _("Slå danske og historiske 1700-1900-tals ord op via din private Ordpresse-server og administrer plugins/ressourcer."),
    version = "1.3.0",
}
"""

BRIDGE_LUA = """-- Ordpresse Bridge: Shared API client, Wi-Fi orchestration, and network helper for KOReader
-- Accessible via require("ordpresse_bridge"), package.loaded["ordpresse.bridge"], or dofile("plugins/ordpresse.koplugin/bridge.lua")

local InfoMessage = require("ui/widget/infomessage")
local NetworkMgr = require("ui/network/manager")
local UIManager = require("ui/uimanager")
local json = require("json")
local ltn12 = require("ltn12")
local socket = require("socket")
local http = require("socket.http")
local url = require("socket.url")
local _ = require("gettext")

local ok_https, https = pcall(require, "ssl.https")
local ok_su, socketutil = pcall(require, "socketutil")

local plugin_dir = (debug.getinfo(1, "S").source:match("@?(.*/)") or "./")
local ok_cfg, config = pcall(dofile, plugin_dir .. "config.lua")
if not ok_cfg or type(config) ~= "table" then
    config = { endpoint_url = "", api_key = "", device_name = "default", timeout_seconds = 10 }
end

local Bridge = {
    version = "1.3.0",
    config = config,
}

function Bridge:getConfig()
    if not self.config or self.config.endpoint_url == "" then
        local _, fresh_cfg = pcall(dofile, plugin_dir .. "config.lua")
        if fresh_cfg and type(fresh_cfg) == "table" then
            self.config = fresh_cfg
        end
    end
    return self.config or config
end

function Bridge:isConfigured()
    local cfg = self:getConfig()
    return type(cfg.endpoint_url) == "string" and cfg.endpoint_url ~= ""
end

function Bridge:runWhenOnline(callback)
    if not callback then return false end
    if NetworkMgr and NetworkMgr.runWhenConnected then
        NetworkMgr:runWhenConnected(callback)
        return true
    elseif NetworkMgr and NetworkMgr.willRerunWhenOnline then
        if NetworkMgr:willRerunWhenOnline(callback) then
            return true
        end
    end
    callback()
    return true
end

function Bridge:request(opts)
    opts = opts or {}
    local cfg = self:getConfig()
    local req_url = opts.url
    if not req_url or req_url == "" then
        req_url = cfg.endpoint_url
        local sep = req_url:find("?") and "&" or "?"
        if opts.action and opts.action ~= "" then
            req_url = req_url .. sep .. "action=" .. url.escape(opts.action)
            sep = "&"
        end
        if opts.params and type(opts.params) == "table" then
            for k, v in pairs(opts.params) do
                if v ~= nil then
                    req_url = req_url .. sep .. url.escape(tostring(k)) .. "=" .. url.escape(tostring(v))
                    sep = "&"
                end
            end
        end
    end

    local method = (opts.method or "GET"):upper()
    local body = opts.body
    local base_timeout = tonumber(cfg.timeout_seconds) or 10
    if base_timeout < 10 then base_timeout = 10 end
    local block_timeout = tonumber(opts.timeout) or base_timeout
    if block_timeout < base_timeout then block_timeout = base_timeout end
    local total_timeout = block_timeout + 15

    local request_fn = http.request
    if req_url:sub(1, 8) == "https://" and ok_https and https then
        https.TIMEOUT = block_timeout
        request_fn = https.request
    else
        http.TIMEOUT = block_timeout
    end

    local resp_body = {}
    local sink
    if ok_su and socketutil and socketutil.table_sink then
        sink = socketutil.table_sink(resp_body)
    else
        sink = ltn12.sink.table(resp_body)
    end

    local source = nil
    local content_length = nil
    if body and type(body) == "string" and #body > 0 then
        source = ltn12.source.string(body)
        content_length = tostring(#body)
    end

    local headers = {
        ["X-API-Key"] = cfg.api_key or "",
        ["Accept"] = "application/json, */*",
        ["User-Agent"] = "KOReader-Ordpresse-Bridge/" .. self.version,
        ["Connection"] = "close",
    }
    if content_length then
        headers["Content-Length"] = content_length
        headers["Content-Type"] = opts.content_type or "application/json"
    end
    if opts.headers and type(opts.headers) == "table" then
        for hk, hv in pairs(opts.headers) do
            headers[hk] = hv
        end
    end

    local function doAttempt()
        if ok_su and socketutil and socketutil.set_timeout then
            pcall(socketutil.set_timeout, socketutil, block_timeout, total_timeout)
        end

        local ok, res, code_or_err = pcall(function()
            return request_fn({
                url = req_url,
                method = method,
                headers = headers,
                source = source,
                sink = sink,
            })
        end)

        if ok_su and socketutil and socketutil.reset_timeout then
            pcall(socketutil.reset_timeout, socketutil)
        end

        return ok, res, code_or_err, table.concat(resp_body)
    end

    local ok, res, status_code, raw_res = doAttempt()
    if not ok or not res then
        pcall(socket.sleep, 0.3)
        ok, res, status_code, raw_res = doAttempt()
    end

    if not ok or not res or status_code ~= 200 then
        return false, nil, status_code or "timeout", raw_res
    end

    if opts.raw then
        return true, raw_res, status_code, nil
    end

    local decode_ok, decoded = pcall(json.decode, raw_res)
    if decode_ok and type(decoded) == "table" then
        return true, decoded, status_code, nil
    end
    return true, raw_res, status_code, nil
end

function Bridge:httpGet(req_url, timeout_override)
    local ok, data, code, raw = self:request({
        url = req_url,
        timeout = timeout_override,
        raw = true,
    })
    return ok, ok, code, (ok and data or raw)
end

function Bridge:apiCall(action, params, callback, options)
    options = options or {}
    if not self:isConfigured() then
        if not options.silent then
            UIManager:show(InfoMessage:new{ text = _("Ordpresse mangler server-adresse i config.lua.") })
        end
        if callback then callback(nil, "unconfigured") end
        return
    end

    self:runWhenOnline(function()
        local loading = nil
        if options.loading_text and options.loading_text ~= "" then
            loading = InfoMessage:new{ text = options.loading_text }
            UIManager:show(loading)
        end

        UIManager:scheduleIn(0.05, function()
            local ok, data, code, raw = self:request({
                action = action,
                params = params,
                timeout = options.timeout,
                method = options.method or "GET",
                body = options.body,
                raw = options.raw or false,
            })

            if loading then
                pcall(function() UIManager:close(loading) end)
            end

            if not ok then
                if not options.silent then
                    local msg = options.error_text or (_("Fejl ved serverkald (") .. tostring(code or "afbrudt") .. _(")."))
                    UIManager:show(InfoMessage:new{ text = msg })
                end
                if callback then callback(nil, code or "error") end
                return
            end

            if callback then callback(data, nil) end
        end)
    end)
end

package.loaded["ordpresse.bridge"] = Bridge
package.loaded["ordpresse_bridge"] = Bridge

return Bridge
"""

MAIN_LUA_TEMPLATE = """local ConfirmBox = require("ui/widget/confirmbox")
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

local PLUGIN_VERSION = "1.3.0"
local ok_util, util = pcall(require, "util")
local ok_ffiutil, ffiUtil = pcall(require, "ffi/util")
local ok_lfs, lfs = pcall(require, "libs/libkoreader-lfs")
local ok_device, Device = pcall(require, "device")
local ok_ds, DataStorage = pcall(require, "datastorage")

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

local EMBEDDED_BRIDGE_CODE = [===[__EMBEDDED_BRIDGE_LUA__]===]

-- Indlaes og registrer Ordpresse Bridge for sekundære plugins (med self-healing bootstrap ved OTA opdatering fra v1.2.2)
local ok_bridge, Bridge = pcall(dofile, plugin_dir .. "bridge.lua")
if (not ok_bridge or type(Bridge) ~= "table") and type(EMBEDDED_BRIDGE_CODE) == "string" and #EMBEDDED_BRIDGE_CODE > 50 then
    writeFileAtomic(plugin_dir .. "bridge.lua", EMBEDDED_BRIDGE_CODE)
    ok_bridge, Bridge = pcall(dofile, plugin_dir .. "bridge.lua")
end
if ok_bridge and type(Bridge) == "table" then
    package.loaded["ordpresse.bridge"] = Bridge
    package.loaded["ordpresse_bridge"] = Bridge
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

    if self.ui.menu and self.ui.menu.registerToMainMenu then
        pcall(function() self.ui.menu:registerToMainMenu(self) end)
    end

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
        text = _("🛍️ Ordpresse Butik (Plugins & Ressourcer)"),
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
    local bridge = package.loaded["ordpresse.bridge"]
    if bridge and bridge.httpGet then
        return bridge:httpGet(req_url, timeout_override)
    end

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
        text = _("⚡ Slår '") .. word .. _("' op i Ordpresse..."),
    }
    UIManager:show(loading)

    UIManager:scheduleIn(0.05, function()
        local req_url = string.format("%s?q=%s", config.endpoint_url, url.escape(word))
        local ok, res, status_code, body = self:httpGet(req_url)

        closeWidgetSafe(loading)

        if not ok or not res or status_code ~= 200 then
            local err_msg = tostring(status_code or "timeout")
            if status_code == 401 then
                err_msg = _("Ugyldig API-nøgle.")
            elseif status_code == 429 then
                err_msg = _("For mange forespørgsler (hastighedsgrænse nået).")
            end
            UIManager:show(InfoMessage:new{
                text = _("Fejl under opslag (") .. err_msg .. _(")."),
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
    end)
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

        -- Sikkerhedskopi af eksisterende kernefiler foer overskrivning
        pcall(function()
            local old_m = io.open(file_path, "rb")
            if old_m then
                local content = old_m:read("*a")
                old_m:close()
                if content then writeFileSafe(base_dir .. ".main.lua.bak", content) end
            end
            local old_meta = io.open(base_dir .. "_meta.lua", "rb")
            if old_meta then
                local content = old_meta:read("*a")
                old_meta:close()
                if content then writeFileSafe(base_dir .. "._meta.lua.bak", content) end
            end
            local old_b = io.open(base_dir .. "bridge.lua", "rb")
            if old_b then
                local content = old_b:read("*a")
                old_b:close()
                if content then writeFileSafe(base_dir .. ".bridge.lua.bak", content) end
            end
        end)

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

        if type(data.bridge_lua) == "string" and #data.bridge_lua > 50 and validateLuaSyntax(data.bridge_lua) then
            writeFileAtomic(base_dir .. "bridge.lua", data.bridge_lua)
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

-- ===================================================================
-- 🛍️ Butik: Pakke-inspektion, Versions-tjek og Mappe-routing
-- ===================================================================

local function findPackageDest(item)
    local data_dir = (ok_ds and DataStorage and DataStorage.getDataDir) and DataStorage:getDataDir() or "./"
    local cat = item.category or "utility"
    local target_type = item.target_type or (cat == "resources" and "font" or "plugin")
    local rel_dir = item.target_dir
    if not rel_dir or rel_dir == "" then
        if target_type == "font" then
            rel_dir = "fonts"
        elseif target_type == "hyph" then
            rel_dir = "data/hyph"
        else
            rel_dir = "plugins"
        end
    end
    local safe_name = (item.koplugin_name or item.id):gsub("[^%w%._%-]", "_")
    local base_dir = data_dir .. "/" .. rel_dir
    local dest_dir = base_dir .. "/" .. safe_name
    local staging_dir = base_dir .. "/." .. safe_name .. ".tmp"
    local backup_dir = base_dir .. "/." .. safe_name .. ".bak"
    return {
        data_dir = data_dir,
        target_type = target_type,
        rel_dir = rel_dir,
        base_dir = base_dir,
        safe_name = safe_name,
        dest_dir = dest_dir,
        staging_dir = staging_dir,
        backup_dir = backup_dir,
    }
end

local function inspectInstalledPackage(item)
    local paths = findPackageDest(item)
    local is_installed = false
    local installed_version = nil
    local has_backup = false

    if ok_lfs and lfs and lfs.attributes then
        if lfs.attributes(paths.backup_dir, "mode") ~= nil then
            has_backup = true
        end

        local dest_mode = lfs.attributes(paths.dest_dir, "mode")
        if dest_mode ~= nil then
            is_installed = true
            -- 1. Tjek .version fil først (bruges af fonts og hyph ressourcer)
            local vf = io.open(paths.dest_dir .. "/.version", "r")
            if vf then
                local line = vf:read("*l")
                vf:close()
                if line and line ~= "" then
                    installed_version = line:gsub("^%s+", ""):gsub("%s+$", "")
                end
            end

            -- 2. Hvis plugin, læs _meta.lua
            if not installed_version and paths.target_type == "plugin" then
                local mf = io.open(paths.dest_dir .. "/_meta.lua", "r")
                if mf then
                    local mcontent = mf:read("*a")
                    mf:close()
                    if mcontent then
                        local ver = mcontent:match('version%s*=%s*"(.-)"') or mcontent:match("version%s*=%s*'(.-)'")
                        if ver and ver ~= "" then
                            installed_version = ver
                        end
                    end
                end
            end
        end
    end

    local status = "not_installed"
    if is_installed then
        if installed_version and installed_version ~= "" and item.version and item.version ~= "" then
            if installed_version == item.version then
                status = "installed"
            else
                status = "update_available"
            end
        else
            status = "installed"
        end
    end

    return {
        is_installed = is_installed,
        installed_version = installed_version,
        has_backup = has_backup,
        status = status,
        paths = paths,
    }
end

-- ===================================================================
-- 🛍️ Butik: Katalog, Detaljevisning og Opdater Alle
-- ===================================================================

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
        text = _("🛍️ Henter butik-katalog..."),
    }
    UIManager:show(loading)

    UIManager:scheduleIn(0.05, function()
        local catalog_url = string.format("%s?action=store_catalog", config.endpoint_url)
        local ok, res, status_code, body = self:httpGet(catalog_url)

        closeWidgetSafe(loading)

        if not ok or not res or status_code ~= 200 then
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke hente butik-kataloget (") .. tostring(status_code or "timeout") .. _(")."),
            })
            return
        end

        local decode_ok, catalog = pcall(json.decode, body)
        if not decode_ok or type(catalog) ~= "table" then
            UIManager:show(InfoMessage:new{ text = _("Ugyldigt katalog-svar fra serveren.") })
            return
        end

        if #catalog == 0 then
            UIManager:show(InfoMessage:new{ text = _("Butikskataloget er tomt.") })
            return
        end

        self:showPluginCatalog(catalog)
    end)
end

function Ordpresse:showPluginCatalog(catalog)
    self._last_catalog = catalog

    local outdated = {}
    for _, p in ipairs(catalog) do
        p._info = inspectInstalledPackage(p)
        p._status = p._info.status
        p._installed_version = p._info.installed_version
        p._has_backup = p._info.has_backup
        p._paths = p._info.paths
        if p._status == "update_available" then
            table.insert(outdated, p)
        end
    end

    local ok_bd, ButtonDialog = pcall(require, "ui/widget/buttondialog")
    if not ok_bd or not ButtonDialog then
        local lines = {}
        if #outdated > 0 then
            table.insert(lines, "=== " .. tostring(#outdated) .. " OPDATERINGER TILGÆNGELIGE ===")
            table.insert(lines, "")
        end
        for _, p in ipairs(catalog) do
            local mark = p._status == "update_available" and "🔄 [Opdater]"
                or (p._status == "installed" and "✅ [Installeret]" or "📦 [Ny]")
            table.insert(lines, mark .. " " .. p.name .. " (v" .. p.version .. ")")
            table.insert(lines, "  " .. p.description)
            table.insert(lines, "")
        end
        UIManager:show(TextViewer:new{
            title = _("🛍️ Ordpresse Butik"),
            text = table.concat(lines, "\\n"),
        })
        return
    end

    local buttons = {}

    -- Top-knap hvis der findes opdateringer
    if #outdated > 0 then
        table.insert(buttons, {
            {
                text = _("🔄 Opdater alle (") .. tostring(#outdated) .. _(" opdateringer)"),
                font_bold = true,
                callback = function()
                    closeWidgetSafe(self._store_dialog)
                    self._store_dialog = nil
                    self:updateAllPlugins(outdated)
                end,
            }
        })
    end

    for _, p in ipairs(catalog) do
        local prefix = "📦 "
        local suffix = "  (v" .. p.version .. ")"
        if p._status == "update_available" then
            prefix = "🔄 "
            local from_ver = p._installed_version and ("v" .. p._installed_version .. " → ") or ""
            suffix = "  (" .. from_ver .. "v" .. p.version .. ")"
        elseif p._status == "installed" then
            prefix = "✅ "
            suffix = "  (v" .. (p._installed_version or p.version) .. ")"
        end

        table.insert(buttons, {
            {
                text = prefix .. p.name .. suffix,
                callback = function()
                    self:showPluginDetail(p)
                end,
            },
        })
    end

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
        title = _("🛍️ Ordpresse Butik (Plugins & Ressourcer)"),
        buttons = buttons,
    }
    UIManager:show(self._store_dialog)
end

function Ordpresse:showPluginDetail(plugin)
    closeWidgetSafe(self._store_dialog)
    self._store_dialog = nil

    if not plugin._info then
        plugin._info = inspectInstalledPackage(plugin)
        plugin._status = plugin._info.status
        plugin._installed_version = plugin._info.installed_version
        plugin._has_backup = plugin._info.has_backup
        plugin._paths = plugin._info.paths
    end

    local category_label = (plugin.category or "utility"):upper()
    if category_label == "RESOURCES" then
        category_label = "RESSOURCER (TYPOGRAFI)"
    end

    local target_desc = (plugin._paths and plugin._paths.rel_dir) or "plugins"

    local detail_lines = {
        plugin.name,
        "",
        plugin.description,
        "",
        "Kategori: " .. category_label,
        "Forfatter: " .. (plugin.author or "ukendt"),
        "Katalog-version: v" .. (plugin.version or "?"),
        "Placering på enhed: " .. target_desc .. "/" .. (plugin._paths and plugin._paths.safe_name or plugin.koplugin_name),
        "Licens: " .. (plugin.license or "?"),
        "Størrelse: ~" .. tostring(plugin.size_estimate_kb or "?") .. " KB",
    }

    if plugin._status == "update_available" then
        table.insert(detail_lines, "")
        table.insert(detail_lines, "Status: 🔄 Opdatering tilgængelig! (Installeret: v" .. tostring(plugin._installed_version) .. ")")
    elseif plugin._status == "installed" then
        table.insert(detail_lines, "")
        table.insert(detail_lines, "Status: ✅ Installeret (v" .. tostring(plugin._installed_version or plugin.version) .. ")")
    else
        table.insert(detail_lines, "")
        table.insert(detail_lines, "Status: 📦 Ikke installeret")
    end

    if plugin._has_backup then
        table.insert(detail_lines, "Sikkerhedskopi: ⏪ Forrige version gemt og klar til rollback.")
    end

    if plugin.install_note and plugin.install_note ~= "" then
        table.insert(detail_lines, "")
        table.insert(detail_lines, "ℹ️ " .. plugin.install_note)
    end

    local action_text = _("📦 Installer")
    if plugin._status == "update_available" then
        action_text = _("🔄 Opdater til v") .. tostring(plugin.version)
    elseif plugin._status == "installed" then
        action_text = _("📦 Geninstaller v") .. tostring(plugin.version)
    end

    local other_buttons = nil
    if plugin._has_backup then
        other_buttons = {
            {
                {
                    text = _("⏪ Gendan forrige (Rollback)"),
                    callback = function()
                        self:confirmRollback(plugin)
                    end,
                }
            }
        }
    end

    UIManager:show(ConfirmBox:new{
        text = table.concat(detail_lines, "\\n"),
        ok_text = action_text,
        cancel_text = _("Tilbage"),
        other_buttons = other_buttons,
        other_buttons_first = true,
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

-- ===================================================================
-- 🛍️ Butik: Sikker Installation, Staging, Validering & Rollback
-- ===================================================================

function Ordpresse:installPlugin(plugin, on_finish_callback)
    if type(plugin) ~= "table" or type(plugin.id) ~= "string" then
        if on_finish_callback then on_finish_callback(false) end
        UIManager:show(InfoMessage:new{ text = _("Ugyldige pakke-data.") })
        return
    end

    local bridge = package.loaded["ordpresse.bridge"]
    local endpoint = config.endpoint_url
    if bridge and bridge.getConfig then
        local bcfg = bridge:getConfig()
        if bcfg and bcfg.endpoint_url and bcfg.endpoint_url ~= "" then
            endpoint = bcfg.endpoint_url
        end
    end

    if type(endpoint) ~= "string" or endpoint == "" then
        if on_finish_callback then on_finish_callback(false) end
        UIManager:show(InfoMessage:new{ text = _("Ordpresse mangler server-adresse i config.lua.") })
        return
    end

    if NetworkMgr and NetworkMgr.willRerunWhenOnline then
        if NetworkMgr:willRerunWhenOnline(function() self:installPlugin(plugin, on_finish_callback) end) then
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
            endpoint,
            url.escape(plugin.id)
        )

        local ok, res, status_code, body = self:httpGet(dl_url, 60)
        closeWidgetSafe(loading)

        if not ok or not res or status_code ~= 200 then
            if on_finish_callback then on_finish_callback(false) end
            UIManager:show(InfoMessage:new{
                text = _("Fejl ved download af pakke (") .. tostring(status_code or "timeout") .. _(")."),
            })
            return
        end

        if not body or #body < 100 then
            if on_finish_callback then on_finish_callback(false) end
            UIManager:show(InfoMessage:new{
                text = _("Modtog tom eller ugyldig pakke-fil."),
            })
            return
        end

        if not ok_ds or not DataStorage or not DataStorage.getDataDir then
            if on_finish_callback then on_finish_callback(false) end
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke tilgå KOReader-datamappen."),
            })
            return
        end

        local data_dir = DataStorage:getDataDir()
        local tmp_dir = data_dir .. "/cache"
        if not ensureDir(tmp_dir) then
            if on_finish_callback then on_finish_callback(false) end
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke oprette cache-mappen."),
            })
            return
        end

        -- Bestem målmappe afhængigt af type (plugins, fonts, data/hyph)
        local target_type = plugin.target_type or (plugin.category == "resources" and "font" or "plugin")
        local safe_koplugin = (plugin.koplugin_name or plugin.id):gsub("[^%w%._%-]", "_")
        local plugins_dir = data_dir .. "/plugins"
        local staging_dir = plugins_dir .. "/." .. safe_koplugin .. ".tmp"
        local target_dir = plugins_dir .. "/" .. safe_koplugin
        local backup_dir = plugins_dir .. "/." .. safe_koplugin .. ".bak"
        local base_dir = plugins_dir

        if target_type == "font" or plugin.target_dir == "fonts" then
            local fonts_dir = data_dir .. "/fonts"
            ensureDir(fonts_dir)
            base_dir = fonts_dir
            target_dir = fonts_dir .. "/" .. safe_koplugin
            staging_dir = fonts_dir .. "/." .. safe_koplugin .. ".tmp"
            backup_dir = fonts_dir .. "/." .. safe_koplugin .. ".bak"
        elseif target_type == "hyph" or plugin.target_dir == "data/hyph" then
            local hyph_dir = data_dir .. "/data/hyph"
            ensureDir(hyph_dir)
            base_dir = hyph_dir
            target_dir = hyph_dir .. "/" .. safe_koplugin
            staging_dir = hyph_dir .. "/." .. safe_koplugin .. ".tmp"
            backup_dir = hyph_dir .. "/." .. safe_koplugin .. ".bak"
        else
            if not ensureDir(plugins_dir) then
                if on_finish_callback then on_finish_callback(false) end
                UIManager:show(InfoMessage:new{
                    text = _("Kunne ikke oprette plugins-mappen."),
                })
                return
            end
        end

        local zip_path = tmp_dir .. "/ordpresse_" .. safe_koplugin .. ".zip"
        local zip_ok, zip_err = writeFileSafe(zip_path, body)
        if not zip_ok then
            if on_finish_callback then on_finish_callback(false) end
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke gemme pakke-filen: ") .. tostring(zip_err),
            })
            return
        end

        removePath(staging_dir)

        local extract_ok = false
        local extracted_files = 0
        local extract_err = nil

        -- Metode 1: ffi/archiver
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

        -- Metode 2: Fallback til system unzip
        if not extract_ok then
            removePath(staging_dir)
            local tmp_extract = tmp_dir .. "/ordpresse_extract_" .. safe_koplugin
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

        -- Validering afhængigt af target_type
        if extract_ok and ok_lfs and lfs and lfs.attributes then
            if target_type == "plugin" then
                local has_main = lfs.attributes(staging_dir .. "/main.lua", "mode") == "file"
                local has_meta = lfs.attributes(staging_dir .. "/_meta.lua", "mode") == "file"
                if not has_main and not has_meta then
                    extract_ok = false
                    extract_err = "manglende main.lua/_meta.lua i plugin-arkiv"
                end
            elseif target_type == "font" then
                local has_font = false
                if lfs.dir then
                    local ok_d, iter, dir_obj = pcall(lfs.dir, staging_dir)
                    if ok_d then
                        for fname in iter, dir_obj do
                            local lower = fname:lower()
                            if lower:match("%.ttf$") or lower:match("%.otf$") or lower:match("%.woff$") or lower:match("%.woff2$") then
                                has_font = true
                                break
                            end
                        end
                    end
                else
                    has_font = true
                end
                if not has_font then
                    extract_ok = false
                    extract_err = "ingen skrifttypefiler (.ttf/.otf) fundet i arkivet"
                end
            elseif target_type == "hyph" then
                local has_pattern = false
                if lfs.dir then
                    local ok_d, iter, dir_obj = pcall(lfs.dir, staging_dir)
                    if ok_d then
                        for fname in iter, dir_obj do
                            local lower = fname:lower()
                            if lower:match("%.pat$") or lower:match("%.pattern$") or lower:match("%.tex$") then
                                has_pattern = true
                                break
                            end
                        end
                    end
                else
                    has_pattern = true
                end
                if not has_pattern then
                    extract_ok = false
                    extract_err = "ingen orddelingsmønstre (.pattern/.pat) fundet i arkivet"
                end
            end
        end

        if not extract_ok then
            removePath(staging_dir)
            local detail = extract_err and (" (" .. tostring(extract_err) .. ")") or ""
            if on_finish_callback then on_finish_callback(false) end
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke udpakke pakken") .. detail .. _(". Prøv evt. at genstarte KOReader og forsøg igen."),
            })
            return
        end

        -- Sikker backup af eksisterende version før overskrivning
        if ok_lfs and lfs and lfs.attributes and lfs.attributes(target_dir, "mode") ~= nil then
            removePath(backup_dir)
            local backup_ok = os.rename(target_dir, backup_dir)
            if not backup_ok then
                local mv_ret = os.execute("mv " .. shellEscapeArg(target_dir) .. " " .. shellEscapeArg(backup_dir))
                backup_ok = (mv_ret == 0 or mv_ret == true)
            end
            if not backup_ok then
                removePath(staging_dir)
                if on_finish_callback then on_finish_callback(false) end
                UIManager:show(InfoMessage:new{ text = _("Kunne ikke oprette sikkerhedskopi af den eksisterende pakke.") })
                return
            end
        end

        -- Flyt staging_dir til target_dir
        local final_ok, rename_err = os.rename(staging_dir, target_dir)
        if not final_ok then
            local mv_ret = os.execute("mv " .. shellEscapeArg(staging_dir) .. " " .. shellEscapeArg(target_dir))
            final_ok = (mv_ret == 0 or mv_ret == true)
        end

        if not final_ok then
            -- Gendan backup øjeblikkeligt ved flyttefejl!
            if ok_lfs and lfs and lfs.attributes and lfs.attributes(backup_dir, "mode") ~= nil then
                os.rename(backup_dir, target_dir)
            end
            removePath(staging_dir)
            if on_finish_callback then on_finish_callback(false) end
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke færdiggøre installationen: ") .. tostring(rename_err or "flytning fejlede"),
            })
            return
        end

        -- Skriv .version fil til versionsinspektion
        writeFileSafe(target_dir .. "/.version", tostring(plugin.version or "1.0"))

        if on_finish_callback then
            on_finish_callback(true)
            return
        end

        -- Enkeltstående installation: vis genstart-dialog
        local restart_msg = (target_type == "plugin")
            and (_("KOReader skal genstartes for at aktivere pluginet."))
            or (_("Genstart KOReader for at opdatere skrifttyper og læseressourcer."))

        UIManager:show(ConfirmBox:new{
            text = _("✅ ") .. tostring(plugin.name or plugin.id) .. _(" er installeret/opdateret!") .. "\\n\\n"
                .. restart_msg .. "\\n"
                .. _("Vil du genstarte KOReader nu?"),
            ok_text = _("Genstart nu"),
            cancel_text = _("Senere"),
            ok_callback = function()
                self:triggerRestart()
            end,
        })
    end)
end

function Ordpresse:confirmRollback(plugin)
    local paths = findPackageDest(plugin)
    if not ok_lfs or not lfs or not lfs.attributes or lfs.attributes(paths.backup_dir, "mode") == nil then
        UIManager:show(InfoMessage:new{ text = _("Ingen sikkerhedskopi fundet for denne pakke.") })
        return
    end

    UIManager:show(ConfirmBox:new{
        text = _("Vil du rulle tilbage til den forrige version af ") .. plugin.name .. _("?") .. "\\n\\n"
            .. _("Dette erstatter den nuværende installation med den tidligere sikkerhedskopi."),
        ok_text = _("Gendan forrige"),
        cancel_text = _("Annuller"),
        ok_callback = function()
            local tmp_broken = paths.base_dir .. "/." .. paths.safe_name .. ".broken"
            removePath(tmp_broken)
            os.rename(paths.dest_dir, tmp_broken)
            local rb_ok = os.rename(paths.backup_dir, paths.dest_dir)
            if not rb_ok then
                local mv_ret = os.execute("mv " .. shellEscapeArg(paths.backup_dir) .. " " .. shellEscapeArg(paths.dest_dir))
                rb_ok = (mv_ret == 0 or mv_ret == true)
            end
            removePath(tmp_broken)

            if rb_ok then
                UIManager:show(ConfirmBox:new{
                    text = _("✅ Forrige version af ") .. plugin.name .. _(" er gendannet!") .. "\\n\\n"
                        .. _("KOReader skal genstartes for at aktivere ændringen.") .. "\\n"
                        .. _("Vil du genstarte KOReader nu?"),
                    ok_text = _("Genstart nu"),
                    cancel_text = _("Senere"),
                    ok_callback = function()
                        self:triggerRestart()
                    end,
                })
            else
                UIManager:show(InfoMessage:new{ text = _("Fejl under gendannelse af forrige version.") })
            end
        end,
    })
end

function Ordpresse:updateAllPlugins(outdated_list)
    if not outdated_list or #outdated_list == 0 then return end

    local total = #outdated_list
    local current_idx = 1
    local updated_names = {}
    local failed_names = {}

    local function installNext()
        if current_idx > total then
            local summary_lines = {}
            if #updated_names > 0 then
                table.insert(summary_lines, _("✅ Følgende pakker blev opdateret:"))
                for _, name in ipairs(updated_names) do
                    table.insert(summary_lines, "  • " .. name)
                end
            end
            if #failed_names > 0 then
                table.insert(summary_lines, "")
                table.insert(summary_lines, _("⚠️ Følgende opdateringer fejlede:"))
                for _, name in ipairs(failed_names) do
                    table.insert(summary_lines, "  • " .. name)
                end
            end
            table.insert(summary_lines, "")
            table.insert(summary_lines, _("KOReader skal genstartes for at aktivere ændringerne."))
            table.insert(summary_lines, _("Vil du genstarte KOReader nu?"))

            UIManager:show(ConfirmBox:new{
                text = table.concat(summary_lines, "\\n"),
                ok_text = _("Genstart nu"),
                cancel_text = _("Senere"),
                ok_callback = function()
                    self:triggerRestart()
                end,
            })
            return
        end

        local p = outdated_list[current_idx]
        current_idx = current_idx + 1

        self:installPlugin(p, function(success)
            if success then
                table.insert(updated_names, p.name)
            else
                table.insert(failed_names, p.name)
            end
            UIManager:scheduleIn(0.1, installNext)
        end)
    end

    UIManager:show(ConfirmBox:new{
        text = _("Der er ") .. tostring(total) .. _(" opdateringer tilgængelige.") .. "\\n\\n"
            .. _("Vil du hente og installere alle opdateringer nu?"),
        ok_text = _("Opdater alle (" .. tostring(total) .. ")"),
        cancel_text = _("Annuller"),
        ok_callback = function()
            UIManager:nextTick(installNext)
        end,
    })
end

return Ordpresse
"""

MAIN_LUA = MAIN_LUA_TEMPLATE.replace("__EMBEDDED_BRIDGE_LUA__", BRIDGE_LUA)


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
        zf.writestr("ordpresse.koplugin/bridge.lua", BRIDGE_LUA)
        zf.writestr("ordpresse.koplugin/main.lua", MAIN_LUA)

    return buf.getvalue()
