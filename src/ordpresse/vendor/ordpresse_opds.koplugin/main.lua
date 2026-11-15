--[[
    Ordpresse OPDS Companion Plugin (v1.0.0)
    Sikrer automatisk registrering og synkronisering af Ordpresse OPDS-kataloget i KOReader.
--]]

local DataStorage = require("datastorage")
local InfoMessage = require("ui/widget/infomessage")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local _ = require("gettext")

local OrdpresseOpds = WidgetContainer:extend{
    name = "ordpresse_opds",
}

function OrdpresseOpds:init()
    self:syncOpds(false)
    if self.ui and self.ui.menu then
        self.ui.menu:registerToMainMenu(self)
    end
end

function OrdpresseOpds:addToMainMenu(menu_items)
    menu_items.ordpresse_opds_sync = {
        text = _("[📡] Ordpresse: Forbind OPDS Bogkatalog"),
        sorting_hint = "more_tools",
        callback = function()
            local ok, msg = self:syncOpds(true)
            UIManager:show(InfoMessage:new{ text = msg })
        end,
    }
end

function OrdpresseOpds:loadConfig()
    -- 1. Tjek ordpresse_bridge
    local ok_bridge, bridge = pcall(require, "ordpresse_bridge")
    if ok_bridge and type(bridge) == "table" and bridge.getConfig then
        local bcfg = bridge:getConfig()
        if bcfg and bcfg.api_key and (bcfg.endpoint_url or bcfg.server_url) then
            return bcfg
        end
    end

    -- 2. Tjek ordpresse.koplugin/config.lua
    local data_dir = (DataStorage and DataStorage.getDataDir) and DataStorage:getDataDir() or "./"
    local cfg_paths = {
        data_dir .. "/plugins/ordpresse.koplugin/config.lua",
        "plugins/ordpresse.koplugin/config.lua",
    }
    for _, path in ipairs(cfg_paths) do
        local ok, cfg = pcall(dofile, path)
        if ok and type(cfg) == "table" and cfg.api_key and (cfg.endpoint_url or cfg.server_url) then
            return cfg
        end
    end
    return nil
end

function OrdpresseOpds:syncOpds(is_manual)
    local cfg = self:loadConfig()
    if not cfg then
        local msg = _("Kunne ikke finde Ordpresse API-nøgle i config.lua.")
        return false, msg
    end

    local endpoint = cfg.endpoint_url or cfg.server_url or ""
    local api_key = cfg.api_key or ""
    if endpoint == "" then
        return false, _("Ordpresse server-adresse mangler.")
    end

    local base_url = endpoint:match("^(https?://[^/]+)")
    if not base_url then
        base_url = endpoint:gsub("/api/.*", ""):gsub("/lookup.*", "")
    end
    base_url = base_url:gsub("/+$", "")

    local opds_url = base_url .. "/opds"
    if api_key ~= "" then
        opds_url = opds_url .. "?api_key=" .. api_key
    end

    local entry = {
        title = "Den Danske Ordpresse",
        name = "Den Danske Ordpresse",
        url = opds_url,
        username = "",
        password = "",
        sync = false,
    }

    local settings_dir = (DataStorage and DataStorage.getSettingsDir) and DataStorage:getSettingsDir() or "./settings"

    -- 1. opds.lua via LuaSettings
    local ok_ls, LuaSettings = pcall(require, "luasettings")
    if ok_ls and LuaSettings then
        local opds_file = settings_dir .. "/opds.lua"
        local ok_open, opds_settings = pcall(LuaSettings.open, LuaSettings, opds_file)
        if ok_open and opds_settings then
            -- Servers table
            local servers = opds_settings:readSetting("servers") or {}
            local s_found = false
            for idx, s in ipairs(servers) do
                if (s.title and s.title:find("Ordpresse")) or (s.name and s.name:find("Ordpresse")) or (s.url and s.url:find("/opds")) then
                    servers[idx] = entry
                    s_found = true
                    break
                end
            end
            if not s_found then
                table.insert(servers, entry)
            end
            opds_settings:saveSetting("servers", servers)

            -- Catalogs table
            local catalogs = opds_settings:readSetting("catalogs") or {}
            local c_found = false
            for idx, s in ipairs(catalogs) do
                if (s.title and s.title:find("Ordpresse")) or (s.name and s.name:find("Ordpresse")) or (s.url and s.url:find("/opds")) then
                    catalogs[idx] = entry
                    c_found = true
                    break
                end
            end
            if not c_found then
                table.insert(catalogs, entry)
            end
            opds_settings:saveSetting("catalogs", catalogs)

            pcall(function() opds_settings:flush() end)
        end
    end

    -- 2. G_reader_settings
    if G_reader_settings then
        local gs = G_reader_settings:readSetting("opds_servers") or {}
        local g_found = false
        for idx, s in ipairs(gs) do
            if (s.title and s.title:find("Ordpresse")) or (s.name and s.name:find("Ordpresse")) or (s.url and s.url:find("/opds")) then
                gs[idx] = entry
                g_found = true
                break
            end
        end
        if not g_found then
            table.insert(gs, entry)
        end
        G_reader_settings:saveSetting("opds_servers", gs)
        if G_reader_settings.flush then
            pcall(function() G_reader_settings:flush() end)
        end
    end

    return true, _("Ordpresse OPDS-katalog er nu forbundet!\n\nFind det i KOReaders topmenu under:\n'Søg / Netværk' → 'OPDS-kataloger' → 'Den Danske Ordpresse'.")
end

return OrdpresseOpds
