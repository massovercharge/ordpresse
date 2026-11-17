--[[
    Ordpresse Højdepunkter & Glosebog-Synk (v1.6.0)
    Samler bogens markeringer/noter samt KOReaders Vocabulary Builder og sender dem til Ordpresse Web UI.
    Nul konfiguration: bruger automatisk ordpresse.koplugin/config.lua.
--]]

local DataStorage = require("datastorage")
local InfoMessage = require("ui/widget/infomessage")
local NetworkMgr = require("ui/network/manager")
local SQ3 = require("lua-ljsqlite3/init")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local http = require("socket.http")
local json = require("json")
local lfs = require("libs/libkoreader-lfs")
local ltn12 = require("ltn12")
local _ = require("gettext")

local ok_https, https = pcall(require, "ssl.https")

local OrdpresseUdklip = WidgetContainer:extend{
    name = "ordpresse_udklip",
    is_doc_only = false,
}

function OrdpresseUdklip:init()
    if self.ui and self.ui.menu then
        self.ui.menu:registerToMainMenu(self)
    end
end

function OrdpresseUdklip:loadConfig()
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

function OrdpresseUdklip:addToMainMenu(menu_items)
    menu_items.ordpresse_udklip = {
        text = _("[Noter] Synkroniser Højdepunkter & Glosebog"),
        sorting_hint = "tools",
        callback = function()
            NetworkMgr:runWhenConnected(function()
                self:syncAllToServer(true)
            end)
        end,
    }
end

function OrdpresseUdklip:onCloseDocument()
    -- Lydløs baggrunds-synk af bogens markeringer hvis Wi-Fi allerede er tændt
    if NetworkMgr and NetworkMgr:isConnected() then
        pcall(function() self:syncAllToServer(false) end)
    end
end

function OrdpresseUdklip:readVocabBuilderWords()
    local db_path = DataStorage:getSettingsDir() .. "/vocabulary_builder.sqlite3"
    if lfs.attributes(db_path, "mode") ~= "file" then return {} end

    local words = {}
    local ok, conn = pcall(SQ3.open, db_path, "ro")
    if not ok or not conn then return {} end

    local ok_q, stmt = pcall(function()
        return conn:prepare([[
            SELECT v.word, v.prev_context, v.next_context, v.create_time, b.title
            FROM vocabulary v
            LEFT JOIN book_title b ON v.title_id = b.id
            ORDER BY v.create_time DESC LIMIT 300
        ]])
    end)
    if ok_q and stmt then
        for row in stmt:rows() do
            table.insert(words, {
                word = row[1],
                context = (row[2] or "") .. " [" .. (row[1] or "") .. "] " .. (row[3] or ""),
                created_at = tonumber(row[4]) or 0,
                book_title = row[5] or "",
            })
        end
        stmt:close()
    end
    conn:close()
    return words
end

function OrdpresseUdklip:syncAllToServer(interactive)
    local cfg = self:loadConfig()
    if not cfg then
        if interactive then
            UIManager:show(InfoMessage:new{ text = _("Ordpresse-konfiguration mangler.") })
        end
        return
    end

    local annotations = {}
    if self.ui and self.ui.doc_settings and self.ui.doc_settings.data then
        annotations = self.ui.doc_settings.data.annotations or {}
    end

    local payload = {
        book_title = self.ui and self.ui.doc_props and (self.ui.doc_props.display_title or self.ui.doc_props.title) or nil,
        book_author = self.ui and self.ui.doc_props and self.ui.doc_props.authors or nil,
        annotations = annotations,
        vocab_words = self:readVocabBuilderWords(),
    }

    local body = json.encode(payload)
    local req_url = cfg.server_url .. "/api/v1/koreader/udklip/sync"
    local req_fn = http.request
    if req_url:sub(1, 8) == "https://" and ok_https and https then
        https.TIMEOUT = 20
        req_fn = https.request
    else
        http.TIMEOUT = 20
    end

    local resp_chunks = {}
    local ok_req, _res, code = pcall(function()
        return req_fn({
            url = req_url,
            method = "POST",
            headers = {
                ["X-API-Key"] = cfg.api_key,
                ["Content-Type"] = "application/json",
                ["Content-Length"] = tostring(#body),
                ["Connection"] = "close",
            },
            source = ltn12.source.string(body),
            sink = ltn12.sink.table(resp_chunks),
        })
    end)

    if interactive then
        if ok_req and code == 200 then
            UIManager:show(InfoMessage:new{
                text = string.format(
                    _("[✓] Synkroniseret til Ordpresse Web UI!\n\n• Bogens citater: %d\n• Ord i glosebog: %d"),
                    #(payload.annotations or {}),
                    #(payload.vocab_words or {})
                ),
            })
        else
            UIManager:show(InfoMessage:new{ text = _("Synkronisering fejlede (HTTP ") .. tostring(code or "fejl") .. ")." })
        end
    end
end

return OrdpresseUdklip
