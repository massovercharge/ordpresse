--[[
    Ordpresse Bogdoktor (v2.0)
    EPUB-restaurering & Bogvaskeri til KOReader
    Fuld E-Ink sanering, OCR-heling, typografi-opgradering og offline lexcache.
--]]

local DataStorage = require("datastorage")
local DocSettings = require("docsettings")
local InfoMessage = require("ui/widget/infomessage")
local MultiConfirmBox = require("ui/widget/multiconfirmbox")
local NetworkMgr = require("ui/network/manager")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local ffiUtil = require("ffi/util")
local http = require("socket.http")
local lfs = require("libs/libkoreader-lfs")
local ltn12 = require("ltn12")
local util = require("util")
local _ = require("gettext")

local Bogdoktor = WidgetContainer:extend{
    name = "bogdoktor",
    is_doc_only = true,
}

function Bogdoktor:init()
    self.ui.menu:registerToMainMenu(self)
end

function Bogdoktor:loadOrdpresseConfig()
    -- 1. Tjek om ordpresse_bridge er indlæst
    local ok_bridge, bridge = pcall(require, "ordpresse_bridge")
    if ok_bridge and type(bridge) == "table" and bridge.getConfig then
        local bcfg = bridge:getConfig()
        if bcfg and bcfg.api_key and (bcfg.endpoint_url or bcfg.server_url) then
            local s_url = bcfg.server_url or bcfg.endpoint_url:gsub("/api/v1/koreader/.*$", "")
            return { server_url = s_url, api_key = bcfg.api_key }
        end
    end

    -- 2. Tjek ordpresse.koplugin config.lua
    local cfg_paths = {
        DataStorage:getDataDir() .. "/plugins/ordpresse.koplugin/config.lua",
        "plugins/ordpresse.koplugin/config.lua",
    }
    for _, cfg_path in ipairs(cfg_paths) do
        local ok, cfg = pcall(dofile, cfg_path)
        if ok and type(cfg) == "table" and cfg.api_key and (cfg.server_url or cfg.endpoint_url) then
            local s_url = cfg.server_url or cfg.endpoint_url:gsub("/api/v1/koreader/.*$", "")
            return { server_url = s_url, api_key = cfg.api_key }
        end
    end
    return nil
end

function Bogdoktor:getBackupPath(doc_path)
    local sdr_dir = DocSettings:getSidecarDir(doc_path)
    util.makePath(sdr_dir)
    return sdr_dir .. "/original_pre_bogdoktor.epub"
end

function Bogdoktor:hasBackup(doc_path)
    local bkp = self:getBackupPath(doc_path)
    return lfs.attributes(bkp, "mode") == "file"
end

function Bogdoktor:addToMainMenu(menu_items)
    menu_items.ordpresse_bogdoktor = {
        text = _("[Vask] Ordpresse Bogdoktor"),
        sorting_hint = "tools",
        sub_item_table = {
            {
                text = _("[*] Dybdevask (Fuld OCR, TOC & EPUB 3)"),
                callback = function()
                    self:confirmAndRun(true)
                end,
            },
            {
                text = _("[>] Hurtig Vask (Kun Sprog, Typografi & CSS)"),
                callback = function()
                    self:confirmAndRun(false)
                end,
            },
            {
                text = _("[<<] Gendan original før Bogdoktor"),
                enabled_func = function()
                    return self.ui.document and self:hasBackup(self.ui.document.file)
                end,
                callback = function()
                    self:restoreOriginalBackup()
                end,
            },
        },
    }
end

function Bogdoktor:confirmAndRun(full_deep_clean)
    local cfg = self:loadOrdpresseConfig()
    if not cfg then
        UIManager:show(InfoMessage:new{
            text = _("Kunne ikke finde Ordpresse-konfiguration. Tjek at ordpresse.koplugin er installeret."),
        })
        return
    end

    local doc_path = self.ui.document.file
    if not doc_path:lower():match("%.epub$") then
        UIManager:show(InfoMessage:new{
            text = _("Bogdoktor understøtter kun EPUB-filer."),
        })
        return
    end

    NetworkMgr:runWhenConnected(function()
        self:runRemoteLaundry(cfg, doc_path, full_deep_clean)
    end)
end

function Bogdoktor:verifyValidZipFile(filepath)
    local f = io.open(filepath, "rb")
    if not f then return false end
    local magic = f:read(4)
    local size = f:seek("end")
    f:close()
    return magic == "PK\003\004" and size and size > 512
end

function Bogdoktor:executeRequest(url, headers, source_fn, tmp_out_path)
    local out_file = io.open(tmp_out_path, "wb")
    if not out_file then return nil, nil end

    http.TIMEOUT = 60
    local _res, code, resp_headers = http.request{
        url = url,
        method = "POST",
        headers = headers,
        source = source_fn,
        sink = ltn12.sink.file(out_file), -- ltn12.sink.file lukker automatisk filen ved EOF
    }
    -- Sikr at fil-handle er lukket, hvis forbindelsen blev afbrudt midtvejs
    pcall(function() out_file:close() end)
    return code, resp_headers
end

function Bogdoktor:runRemoteLaundry(cfg, doc_path, full_deep_clean)
    local wait_msg = InfoMessage:new{
        text = _("[↻] Bogdoktor restaurerer bogen på serveren...\nVent venligst (5-15 sek.)"),
    }
    UIManager:show(wait_msg)
    UIManager:forceRePaint()

    local url = cfg.server_url:gsub("/+$", "") .. "/api/v1/koreader/laundry"
    local tmp_out_path = doc_path .. ".laundry.tmp"
    local filename = ffiUtil.basename(doc_path)
    local partial_md5 = util.partialMD5(doc_path)

    -- Trin A: Forsøg 0-byte Fast-Path hvis bogen allerede ligger på serveren
    local base_headers = {
        ["X-API-Key"] = cfg.api_key,
        ["X-Book-Filename"] = filename,
        ["X-Book-MD5"] = partial_md5 or "",
        ["X-Laundry-Heal-OCR"] = full_deep_clean and "true" or "false",
        ["Content-Length"] = "0",
    }

    local code, resp_headers = self:executeRequest(url, base_headers, nil, tmp_out_path)

    -- Trin B: Hvis bogen ikke lå på serveren (HTTP 404), streames den i 4 KB chunks (0 MB RAM!)
    if code == 404 then
        local file_size = lfs.attributes(doc_path, "size")
        local src_file = io.open(doc_path, "rb")
        if src_file and file_size then
            base_headers["Content-Type"] = "application/epub+zip"
            base_headers["Content-Length"] = tostring(file_size)
            code, resp_headers = self:executeRequest(
                url,
                base_headers,
                ltn12.source.file(src_file),
                tmp_out_path
            )
        end
    end

    UIManager:close(wait_msg)

    if code == 200 and resp_headers and self:verifyValidZipFile(tmp_out_path) then
        -- 1. Gem nuværende læseprocent før vi udskifter filen
        local current_percent = self.ui.rolling and self.ui.rolling:getLastPercent() or nil

        -- 2. Tag sikkerhedskopi i bogens egen .sdr-mappe (samme partition!) hvis der ikke allerede findes én
        local backup_path = self:getBackupPath(doc_path)
        if not self:hasBackup(doc_path) then
            os.rename(doc_path, backup_path)
        else
            os.remove(doc_path)
        end

        -- 3. Atomisk skift af den nyvaskede EPUB på plads
        os.rename(tmp_out_path, doc_path)

        -- 4. Opdater KOReaders partial_md5_checksum så DocSettings ikke afviser bogen
        local new_partial_md5 = util.partialMD5(doc_path)
        if self.ui.doc_settings and new_partial_md5 then
            self.ui.doc_settings:saveSetting("partial_md5_checksum", new_partial_md5)
            self.ui.doc_settings:flush()
        end

        -- 5. Genindlæs dokumentet og hop til den gemte læseprocent
        self.ui:reloadDocument()
        if current_percent and self.ui.rolling then
            self.ui.rolling:onGotoPercent(current_percent * 100)
        end

        local lang = resp_headers["x-ordpresse-lang"] or "da"
        local before_kb = resp_headers["x-ordpresse-size-before-kb"] or "?"
        local after_kb = resp_headers["x-ordpresse-size-after-kb"] or "?"
        local healed = resp_headers["x-ordpresse-paragraphs-healed"] or "0"
        local prewarmed = resp_headers["x-ordpresse-prewarmed-words"] or "0"
        local ms = resp_headers["x-ordpresse-time-ms"] or "0"

        UIManager:show(InfoMessage:new{
            text = string.format(
                _("[✓] Bogdoktor Færdig (%s ms)\n\n"
                .. "• Sprog & orddeling: %s\n"
                .. "• Størrelse: %s KB -> %s KB\n"
                .. "• Knækkede OCR-afsnit helet: %s\n"
                .. "• Offline gloser indlejret: %s ord\n\n"
                .. "(Du kan altid fortryde under [Vask] Ordpresse Bogdoktor -> Gendan original)"),
                ms, lang:upper(), before_kb, after_kb, healed, prewarmed
            ),
        })
    else
        os.remove(tmp_out_path)
        UIManager:show(InfoMessage:new{
            text = _("Fejl under bogvask (HTTP " .. tostring(code) .. "). Originalfilen er urørt."),
        })
    end
end

function Bogdoktor:restoreOriginalBackup()
    local doc_path = self.ui.document.file
    local backup_path = self:getBackupPath(doc_path)
    if not self:hasBackup(doc_path) then return end

    local current_percent = self.ui.rolling and self.ui.rolling:getLastPercent() or nil
    os.remove(doc_path)
    os.rename(backup_path, doc_path)

    local orig_md5 = util.partialMD5(doc_path)
    if self.ui.doc_settings and orig_md5 then
        self.ui.doc_settings:saveSetting("partial_md5_checksum", orig_md5)
        self.ui.doc_settings:flush()
    end

    self.ui:reloadDocument()
    if current_percent and self.ui.rolling then
        self.ui.rolling:onGotoPercent(current_percent * 100)
    end

    UIManager:show(InfoMessage:new{
        text = _("[<<] Original EPUB-fil er gendannet."),
    })
end

return Bogdoktor

