--[[
    Ordpresse Læse-Synk (v1.6.0)
    1-kliks læseposition-synkronisering mod Ordpresse-serveren (Kindle, Kobo, CrossPoint & mobil).
    Nul konfiguration: bruger automatisk ordpresse.koplugin/config.lua.
--]]

local ConfirmBox = require("ui/widget/confirmbox")
local DataStorage = require("datastorage")
local InfoMessage = require("ui/widget/infomessage")
local NetworkMgr = require("ui/network/manager")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local http = require("socket.http")
local json = require("json")
local ltn12 = require("ltn12")
local url = require("socket.url")
local util = require("util")
local _ = require("gettext")

local ok_https, https = pcall(require, "ssl.https")

local OrdpresseKosync = WidgetContainer:extend{
    name = "ordpresse_kosync",
    is_doc_only = true,
}

function OrdpresseKosync:init()
    if self.ui and self.ui.menu then
        self.ui.menu:registerToMainMenu(self)
    end
end

function OrdpresseKosync:loadConfig()
    local ok_bridge, bridge = pcall(require, "ordpresse_bridge")
    if ok_bridge and type(bridge) == "table" and bridge.getConfig then
        local bcfg = bridge:getConfig()
        if bcfg and bcfg.api_key and (bcfg.server_url or bcfg.endpoint_url) then
            local base = (bcfg.server_url or bcfg.endpoint_url:gsub("/api/v1/koreader/.*$", "")):gsub("/+$", "")
            return {
                server_url = base,
                api_key = bcfg.api_key,
                device_name = bcfg.device_name or "KOReader",
            }
        end
    end

    local paths = {
        DataStorage:getDataDir() .. "/plugins/ordpresse.koplugin/config.lua",
        "plugins/ordpresse.koplugin/config.lua",
    }
    for _, p in ipairs(paths) do
        local ok, cfg = pcall(dofile, p)
        if ok and type(cfg) == "table" and cfg.api_key and (cfg.server_url or cfg.endpoint_url) then
            local base = (cfg.server_url or cfg.endpoint_url:gsub("/api/v1/koreader/.*$", "")):gsub("/+$", "")
            return {
                server_url = base,
                api_key = cfg.api_key,
                device_name = cfg.device_name or "KOReader",
            }
        end
    end
    return nil
end

function OrdpresseKosync:httpRequest(req_url, method, api_key, body_str)
    local req_fn = http.request
    if req_url:sub(1, 8) == "https://" and ok_https and https then
        https.TIMEOUT = 10
        req_fn = https.request
    else
        http.TIMEOUT = 10
    end

    local headers = {
        ["X-API-Key"] = api_key or "",
        ["Accept"] = "application/json",
        ["Connection"] = "close",
    }
    local source = nil
    if body_str then
        source = ltn12.source.string(body_str)
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = tostring(#body_str)
    end

    local resp_chunks = {}
    local ok, res, code = pcall(function()
        return req_fn({
            url = req_url,
            method = method,
            headers = headers,
            source = source,
            sink = ltn12.sink.table(resp_chunks),
        })
    end)
    if not ok or not res then
        return false, nil, ""
    end
    return true, code, table.concat(resp_chunks)
end

function OrdpresseKosync:getBookProgressState()
    if not self.ui or not self.ui.document then return nil end
    local doc_path = self.ui.document.file
    local props = self.ui.doc_props or {}
    local title = props.display_title or props.title or ""
    local author = props.authors or ""
    local partial_md5 = doc_path and util.partialMD5(doc_path) or ""

    local percentage = 0.0
    local xpointer = ""
    if self.ui.rolling then
        percentage = (self.ui.rolling:getLastPercent() or 0) * 100.0
        if self.ui.document.getXPointer then
            local ok_xp, xp = pcall(function() return self.ui.document:getXPointer() end)
            if ok_xp and type(xp) == "string" then
                xpointer = xp
            end
        end
    elseif self.ui.paging and self.ui.document.getPageCount then
        local cur_p = self.ui.paging.current_page or 1
        local total_p = self.ui.document:getPageCount() or 1
        if total_p > 0 then
            percentage = (cur_p / total_p) * 100.0
        end
    end

    return {
        partial_md5 = partial_md5,
        title = title,
        author = author,
        percentage = percentage,
        xpointer = xpointer,
    }
end

function OrdpresseKosync:pushProgress(interactive)
    local cfg = self:loadConfig()
    if not cfg then
        if interactive then
            UIManager:show(InfoMessage:new{ text = _("Ordpresse-konfiguration mangler.") })
        end
        return
    end
    local state = self:getBookProgressState()
    if not state then return end

    local body = json.encode(state)
    local req_url = cfg.server_url .. "/api/v1/koreader/sync/progress"
    local ok, code = self:httpRequest(req_url, "PUT", cfg.api_key, body)

    if interactive then
        if ok and code == 200 then
            UIManager:show(InfoMessage:new{
                text = string.format(_("[✓] Læseposition gemt (%.1f %%)."), state.percentage),
            })
        else
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke gemme læseposition (HTTP ") .. tostring(code or "fejl") .. ").",
            })
        end
    end
end

function OrdpresseKosync:pullProgress(interactive)
    local cfg = self:loadConfig()
    if not cfg then
        if interactive then
            UIManager:show(InfoMessage:new{ text = _("Ordpresse-konfiguration mangler.") })
        end
        return
    end
    local state = self:getBookProgressState()
    if not state then return end

    local req_url = string.format(
        "%s/api/v1/koreader/sync/progress?md5=%s&title=%s&author=%s",
        cfg.server_url,
        url.escape(state.partial_md5 or ""),
        url.escape(state.title or ""),
        url.escape(state.author or "")
    )
    local ok, code, raw = self:httpRequest(req_url, "GET", cfg.api_key, nil)
    if not ok or code ~= 200 then
        if interactive then
            UIManager:show(InfoMessage:new{
                text = _("Kunne ikke hente læseposition (HTTP ") .. tostring(code or "fejl") .. ").",
            })
        end
        return
    end

    local dec_ok, data = pcall(json.decode, raw)
    if not dec_ok or type(data) ~= "table" or not data.found then
        if interactive then
            UIManager:show(InfoMessage:new{ text = _("Ingen gemt læseposition fundet for denne bog endnu.") })
        end
        return
    end

    local remote_pct = tonumber(data.percentage) or 0.0
    local diff = remote_pct - (state.percentage or 0.0)
    local remote_device = tostring(data.device_name or "anden enhed")

    if not interactive and (diff <= 0.3 or remote_device == cfg.device_name) then
        return
    end

    if interactive and math.abs(diff) <= 0.3 then
        UIManager:show(InfoMessage:new{
            text = string.format(_("[✓] Du er allerede opdateret (%.1f %%)."), state.percentage),
        })
        return
    end

    UIManager:show(ConfirmBox:new{
        text = string.format(
            _("[↻] Du læste videre på '%s' (%.1f %%). Vil du hoppe dertil?"),
            remote_device,
            remote_pct
        ),
        ok_text = _("[>] Hop dertil"),
        cancel_text = _("Bliv her"),
        ok_callback = function()
            if data.xpointer and data.xpointer ~= "" and self.ui.rolling and self.ui.rolling.onGotoXPointer then
                pcall(function() self.ui.rolling:onGotoXPointer(data.xpointer) end)
            elseif self.ui.rolling and self.ui.rolling.onGotoPercent then
                pcall(function() self.ui.rolling:onGotoPercent(remote_pct) end)
            elseif self.ui.paging and self.ui.document.getPageCount then
                local total = self.ui.document:getPageCount() or 1
                local target_p = math.max(1, math.floor((remote_pct / 100.0) * total + 0.5))
                pcall(function() self.ui.paging:onGotoPage(target_p) end)
            end
        end,
    })
end

function OrdpresseKosync:onCloseDocument()
    if NetworkMgr and NetworkMgr:isConnected() then
        pcall(function() self:pushProgress(false) end)
    end
end

function OrdpresseKosync:onSuspend()
    if NetworkMgr and NetworkMgr:isConnected() then
        pcall(function() self:pushProgress(false) end)
    end
end

function OrdpresseKosync:onReaderReady()
    if NetworkMgr and NetworkMgr:isConnected() then
        UIManager:scheduleIn(0.8, function()
            pcall(function() self:pullProgress(false) end)
        end)
    end
end

function OrdpresseKosync:onResume()
    if NetworkMgr and NetworkMgr:isConnected() then
        UIManager:scheduleIn(1.0, function()
            pcall(function() self:pullProgress(false) end)
        end)
    end
end

function OrdpresseKosync:addToMainMenu(menu_items)
    menu_items.ordpresse_kosync = {
        text = _("[Synk] Ordpresse Læse-Synk"),
        sorting_hint = "tools",
        sub_item_table = {
            {
                text = _("[↻] Hent seneste læseposition"),
                callback = function()
                    NetworkMgr:runWhenConnected(function()
                        self:pullProgress(true)
                    end)
                end,
            },
            {
                text = _("[>] Send nuværende læseposition"),
                callback = function()
                    NetworkMgr:runWhenConnected(function()
                        self:pushProgress(true)
                    end)
                end,
            },
        },
    }
end

return OrdpresseKosync
