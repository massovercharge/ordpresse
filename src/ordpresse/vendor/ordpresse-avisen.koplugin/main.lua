--[[
    Ordpresse Avisen — Web-til-EPUB ('Læs Senere' & Artikel-Henter) (v1.6.0)
    Henter nye vaskede artikel-EPUB'er fra Ordpresse-serveren ned i Artikler/ og åbner den nyeste.
    Nul konfiguration: bruger automatisk ordpresse.koplugin/config.lua.
--]]

local ConfirmBox = require("ui/widget/confirmbox")
local DataStorage = require("datastorage")
local InfoMessage = require("ui/widget/infomessage")
local NetworkMgr = require("ui/network/manager")
local ReaderUI = require("apps/reader/readerui")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local http = require("socket.http")
local json = require("json")
local lfs = require("libs/libkoreader-lfs")
local ltn12 = require("ltn12")
local util = require("util")
local _ = require("gettext")

local ok_https, https = pcall(require, "ssl.https")

local OrdpresseAvisen = WidgetContainer:extend{
    name = "ordpresse_avisen",
    is_doc_only = false,
}

function OrdpresseAvisen:init()
    if self.ui and self.ui.menu then
        self.ui.menu:registerToMainMenu(self)
    end
end

function OrdpresseAvisen:loadConfig()
    local ok_bridge, bridge = pcall(require, "ordpresse_bridge")
    if ok_bridge and type(bridge) == "table" and bridge.getConfig then
        local bcfg = bridge:getConfig()
        if bcfg and bcfg.api_key and (bcfg.server_url or bcfg.endpoint_url) then
            local base = (bcfg.server_url or bcfg.endpoint_url:gsub("/api/v1/koreader/.*$", "")):gsub("/+$", "")
            return { server_url = base, api_key = bcfg.api_key }
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
            return { server_url = base, api_key = cfg.api_key }
        end
    end
    return nil
end

function OrdpresseAvisen:getArticlesDir()
    local home_dir = G_reader_settings and G_reader_settings:readSetting("home_dir") or DataStorage:getDataDir()
    local dir = home_dir:gsub("/+$", "") .. "/Artikler"
    util.makePath(dir)
    return dir
end

function OrdpresseAvisen:httpGet(req_url, api_key, sink)
    local req_fn = http.request
    if req_url:sub(1, 8) == "https://" and ok_https and https then
        https.TIMEOUT = 25
        req_fn = https.request
    else
        http.TIMEOUT = 25
    end
    local ok, res, code = pcall(function()
        return req_fn({
            url = req_url,
            method = "GET",
            headers = {
                ["X-API-Key"] = api_key or "",
                ["Connection"] = "close",
            },
            sink = sink,
        })
    end)
    return ok and res ~= nil, code
end

function OrdpresseAvisen:addToMainMenu(menu_items)
    menu_items.ordpresse_avisen = {
        text = _("[Avis] Hent 'Læs Senere'-artikler"),
        sorting_hint = "tools",
        callback = function()
            NetworkMgr:runWhenConnected(function()
                self:fetchNewArticles()
            end)
        end,
    }
end

function OrdpresseAvisen:fetchNewArticles()
    local cfg = self:loadConfig()
    if not cfg then
        UIManager:show(InfoMessage:new{ text = _("Ordpresse-konfiguration mangler.") })
        return
    end

    local loading = InfoMessage:new{ text = _("[Avis] Henter nye artikler fra Ordpresse...") }
    UIManager:show(loading)
    UIManager:forceRePaint()

    local chunks = {}
    local list_url = cfg.server_url .. "/api/v1/koreader/articles?unread_only=true"
    local ok, code = self:httpGet(list_url, cfg.api_key, ltn12.sink.table(chunks))
    UIManager:close(loading)

    if not ok or code ~= 200 then
        UIManager:show(InfoMessage:new{
            text = _("Kunne ikke hente artikellisten (HTTP ") .. tostring(code or "fejl") .. ").",
        })
        return
    end

    local dec_ok, payload = pcall(json.decode, table.concat(chunks))
    local articles = (dec_ok and type(payload) == "table" and payload.articles) or {}
    if #articles == 0 then
        UIManager:show(InfoMessage:new{
            text = _("[✓] Ingen nye ulæste artikler i din 'Læs Senere'-kø."),
        })
        return
    end

    local target_dir = self:getArticlesDir()
    local downloaded = 0
    local newest_path = nil
    local newest_title = nil

    for _, art in ipairs(articles) do
        local fn = tostring(art.filename or (tostring(art.md5 or "artikel") .. ".epub"))
        fn = fn:gsub("[^%w%._%-]", "_")
        local dest_path = target_dir .. "/" .. fn
        local dl_url = cfg.server_url .. "/opds/articles/download/" .. tostring(art.md5)

        if lfs.attributes(dest_path, "mode") ~= "file" then
            local tmp_path = dest_path .. ".tmp"
            local out_f = io.open(tmp_path, "wb")
            if out_f then
                local dl_ok, dl_code = self:httpGet(dl_url, cfg.api_key, ltn12.sink.file(out_f))
                pcall(function() out_f:close() end)
                local sz = lfs.attributes(tmp_path, "size") or 0
                if dl_ok and dl_code == 200 and sz > 200 then
                    os.remove(dest_path)
                    os.rename(tmp_path, dest_path)
                    downloaded = downloaded + 1
                    if not newest_path then
                        newest_path = dest_path
                        newest_title = art.title or fn
                    end
                else
                    os.remove(tmp_path)
                end
            end
        elseif not newest_path then
            newest_path = dest_path
            newest_title = art.title or fn
        end
    end

    if newest_path then
        UIManager:show(ConfirmBox:new{
            text = string.format(
                _("[✓] Hentede %d nye artikler til Artikler/.\n\nVil du åbne '%s' nu?"),
                downloaded,
                tostring(newest_title or "nyeste artikel")
            ),
            ok_text = _("[>] Åbn artikel"),
            cancel_text = _("Senere"),
            ok_callback = function()
                ReaderUI:showReader(newest_path)
            end,
        })
    else
        UIManager:show(InfoMessage:new{
            text = _("[!] Kunne ikke downloade artiklerne. Tjek Wi-Fi-forbindelsen."),
        })
    end
end

return OrdpresseAvisen
