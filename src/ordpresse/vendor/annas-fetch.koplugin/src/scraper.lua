local json = require("json")
local logger = require("logger")
local util = require("util")

local Scraper = {}

local COOKIE_JAR = "/tmp/annas_cookies.txt"
local CACHE_FILE = "plugins/annas-fetch.koplugin/mirrors_cache.json"
local CACHE_TTL = 3600

local USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

local DEFAULT_MIRRORS = {
    libgen = {
        "https://libgen.li",
        "https://libgen.is",
        "https://libgen.rs",
    },
    annas = {
        "https://annas-archive.pk",
        "https://annas-archive.gl",
        "https://annas-archive.gd",
    },
}

local function shell_quote(str)
    return "'" .. tostring(str or ""):gsub("'", "'\\''") .. "'"
end

local function url_encode(str)
    if not str then return "" end
    str = string.gsub(str, "\n", "\r\n")
    str = string.gsub(str, "([^%w %-%_%.%~])", function(c)
        return string.format("%%%02X", string.byte(c))
    end)
    return string.gsub(str, " ", "+")
end

function Scraper.sanitize_name(name)
    local sanitized = tostring(name or "unknown"):gsub("[^%w._-]", "_"):gsub("%s+", "_"):gsub("_+", "_")
    return sanitized:gsub("^_+", ""):gsub("_+$", "")
end

function Scraper.getOrdpresseConfig()
    local ok, cfg = pcall(dofile, "plugins/ordpresse.koplugin/config.lua")
    if ok and type(cfg) == "table" and cfg.endpoint_url and cfg.endpoint_url ~= "" then
        return cfg
    end
    return nil
end

function Scraper.getOrdpresseEndpoint()
    local cfg = Scraper.getOrdpresseConfig()
    return cfg and cfg.endpoint_url or nil
end

function Scraper.execCurl(url, max_time)
    local timeout = max_time or 20
    local cmd = string.format(
        "curl -k -L -sS --max-time %d -A %s -c %s -b %s %s 2>&1",
        timeout,
        shell_quote(USER_AGENT),
        shell_quote(COOKIE_JAR),
        shell_quote(COOKIE_JAR),
        shell_quote(url)
    )
    local handle = io.popen(cmd, "r")
    if not handle then
        return nil
    end
    local output = handle:read("*a")
    handle:close()
    if not output or #output == 0 then
        return nil
    end
    return output
end

local function readLocalMirrorCache()
    local f = io.open(CACHE_FILE, "r")
    if not f then return nil end
    local content = f:read("*a")
    f:close()
    local ok, data = pcall(json.decode, content)
    if ok and type(data) == "table" and data.flat_mirrors then
        return data
    end
    return nil
end

local function writeLocalMirrorCache(data)
    local f = io.open(CACHE_FILE, "w")
    if not f then return end
    local ok, encoded = pcall(json.encode, data)
    if ok then
        f:write(encoded)
    end
    f:close()
end

function Scraper.getActiveMirrors()
    local cached = readLocalMirrorCache()
    local now = os.time()
    if cached and cached.local_saved_at and (now - cached.local_saved_at) < CACHE_TTL then
        return cached.flat_mirrors
    end

    local cfg = Scraper.getOrdpresseConfig()
    if cfg and cfg.endpoint_url then
        local sep = string.find(cfg.endpoint_url, "?", 1, true) and "&" or "?"
        local api_url = cfg.endpoint_url .. sep .. "action=annas_mirrors"
        if cfg.api_key and cfg.api_key ~= "" then
            api_url = api_url .. "&api_key=" .. url_encode(cfg.api_key)
        end
        local extra_header = (cfg.api_key and cfg.api_key ~= "") and string.format("-H %s", shell_quote("X-API-Key: " .. cfg.api_key)) or ""
        local cmd = string.format(
            "curl -k -L -sS --max-time 3 %s -A %s -c %s -b %s %s 2>&1",
            extra_header,
            shell_quote(USER_AGENT),
            shell_quote(COOKIE_JAR),
            shell_quote(COOKIE_JAR),
            shell_quote(api_url)
        )
        local handle = io.popen(cmd, "r")
        local raw = nil
        if handle then
            raw = handle:read("*a")
            handle:close()
        end
        if raw then
            local ok, payload = pcall(json.decode, raw)
            if ok and type(payload) == "table" and payload.flat_mirrors then
                payload.local_saved_at = now
                writeLocalMirrorCache(payload)
                logger.info("AnnasFetch: Updated active mirrors from Ordpresse server")
                return payload.flat_mirrors
            end
        end
    end

    if cached and cached.flat_mirrors then
        logger.info("AnnasFetch: Ordpresse unreachable, using offline mirror cache")
        return cached.flat_mirrors
    end

    logger.warn("AnnasFetch: Using hardcoded fallback mirrors")
    return DEFAULT_MIRRORS
end

function Scraper.parseLibgenHtml(html, base_url)
    local results = {}
    if not html or string.find(html:lower(), "this domain may be for sale", 1, true) then
        return results
    end

    for row in string.gmatch(html, "<tr[^>]*>(.-)</tr>") do
        local md5 = string.match(row, "md5=([0-9a-fA-F]+)")
        if md5 and #md5 == 32 then
            local cells = {}
            for cell in string.gmatch(row, "<td[^>]*>(.-)</td>") do
                table.insert(cells, cell)
            end

            local title = "Unknown Title"
            if cells[1] then
                title = string.match(cells[1], "<a[^>]-href=[\"'][^\"']*edition%.php[^\"']*[\"'][^>]*>(.-)</a>")
                    or string.match(cells[1], "<a[^>]-title=[^>]->(.-)</a>")
                    or cells[1]
                title = title:gsub("<[^>]+>", ""):gsub("%s+", " "):match("^%s*(.-)%s*$")
            end

            local author = "Unknown Author"
            if cells[2] and cells[2] ~= "" then
                author = cells[2]:gsub("<[^>]+>", ""):gsub("%s+", " "):match("^%s*(.-)%s*$")
                if author == "" then author = "Unknown Author" end
            end

            local year = nil
            if cells[4] and cells[4] ~= "" then
                local y_clean = cells[4]:gsub("<[^>]+>", ""):gsub("%s+", " "):match("^%s*(.-)%s*$")
                if y_clean ~= "" then year = y_clean end
            end

            local size = nil
            if cells[7] and cells[7] ~= "" then
                local s_clean = cells[7]:gsub("<[^>]+>", ""):gsub("%s+", " "):match("^%s*(.-)%s*$")
                if s_clean ~= "" then size = s_clean end
            end

            local ext = "epub"
            if cells[8] and cells[8] ~= "" then
                local e_clean = cells[8]:gsub("<[^>]+>", ""):gsub("%s+", " "):match("^%s*(.-)%s*$"):lower()
                if e_clean ~= "" then ext = e_clean end
            end

            local dl_path = string.match(row, 'href="([^"]*get%.php%?md5=[0-9a-fA-F]+[^"]*)"')
                or string.match(row, 'href="([^"]*ads%.php%?md5=[0-9a-fA-F]+[^"]*)"')

            local dl_url = dl_path and (base_url .. "/" .. dl_path:gsub("^/", ""))
                or (base_url .. "/ads.php?md5=" .. md5)

            if #title > 0 then
                table.insert(results, {
                    title = title,
                    author = author,
                    year = year,
                    size = size,
                    format = ext,
                    extension = ext,
                    md5 = md5:lower(),
                    source = "libgen",
                    mirror = base_url,
                    download = "lgli",
                    download_page = dl_url,
                })
            end
        end
    end
    return results
end

function Scraper.parseAnnasHtml(html, base_url)
    local results = {}
    if not html then return results end
    local lower = html:lower()
    if string.find(lower, "this domain may be for sale", 1, true)
        or string.find(lower, "ddos-guard/js-challenge", 1, true) then
        return results
    end

    local split_pattern = "pt%-3 pb%-3 border%-b last:border%-b%-0 border%-gray%-100"
    for block in string.gmatch(html, split_pattern .. "(.-)<div class=\"pt%-3") do
        local md5 = string.match(block, "/md5/([0-9a-fA-F]+)")
        local title = string.match(block, "<h3[^>]*>(.-)</h3>")
        if md5 and title then
            title = title:gsub("<[^>]+>", ""):gsub("%s+", " "):match("^%s*(.-)%s*$")
            local author = string.match(block, '<div[^>]*class="[^"]*italic[^"]*"[^>]*>(.-)</div>') or "Unknown Author"
            author = author:gsub("<[^>]+>", ""):gsub("%s+", " "):match("^%s*(.-)%s*$")
            local ext = string.match(block, "%.(epub)") or string.match(block, "%.(pdf)") or "epub"
            table.insert(results, {
                title = title,
                author = author,
                format = ext:lower(),
                extension = ext:lower(),
                md5 = md5:lower(),
                source = "annas",
                mirror = base_url,
                download = "annas",
                download_page = base_url .. "/md5/" .. md5:lower(),
            })
        end
    end
    return results
end

function Scraper.search(query)
    -- Step 1: Zero-config server-assisted search via Ordpresse if reachable
    local cfg = Scraper.getOrdpresseConfig()
    if cfg and cfg.endpoint_url then
        local sep = string.find(cfg.endpoint_url, "?", 1, true) and "&" or "?"
        local api_url = cfg.endpoint_url .. sep .. "action=annas_search&q=" .. url_encode(query)
        if cfg.api_key and cfg.api_key ~= "" then
            api_url = api_url .. "&api_key=" .. url_encode(cfg.api_key)
        end
        local extra_header = (cfg.api_key and cfg.api_key ~= "") and string.format("-H %s", shell_quote("X-API-Key: " .. cfg.api_key)) or ""
        local cmd = string.format(
            "curl -k -L -sS --max-time 8 %s -A %s %s 2>&1",
            extra_header,
            shell_quote(USER_AGENT),
            shell_quote(api_url)
        )
        local handle = io.popen(cmd, "r")
        local raw = nil
        if handle then
            raw = handle:read("*a")
            handle:close()
        end
        if raw and #raw > 0 then
            local ok, payload = pcall(json.decode, raw)
            if ok and type(payload) == "table" and payload.results and #payload.results > 0 then
                logger.info(string.format("AnnasFetch: Server search found %d hits for '%s'", #payload.results, query))
                return payload.results
            end
        end
    end

    -- Step 2: Client-side Libgen scraping (with fiction tab prioritized)
    local mirrors = Scraper.getActiveMirrors()
    local encoded_q = url_encode(query)

    for _, base_url in ipairs(mirrors.libgen or DEFAULT_MIRRORS.libgen) do
        local search_url = string.format("%s/index.php?req=%s&columns%%5B%%5D=t&columns%%5B%%5D=a&curtab=f&res=50", base_url, encoded_q)
        local html = Scraper.execCurl(search_url, 15)
        local hits = Scraper.parseLibgenHtml(html, base_url)
        if #hits == 0 then
            search_url = string.format("%s/index.php?req=%s&res=50", base_url, encoded_q)
            html = Scraper.execCurl(search_url, 15)
            hits = Scraper.parseLibgenHtml(html, base_url)
        end
        if #hits > 0 then
            logger.info("AnnasFetch: Found " .. #hits .. " hits via Libgen mirror " .. base_url)
            return hits
        end
    end

    -- Step 3: Client-side Anna's Archive scraping fallback
    for _, base_url in ipairs(mirrors.annas or DEFAULT_MIRRORS.annas) do
        local search_url = string.format("%s/search?q=%s", base_url, encoded_q)
        local html = Scraper.execCurl(search_url, 20)
        local hits = Scraper.parseAnnasHtml(html, base_url)
        if #hits > 0 then
            logger.info("AnnasFetch: Found " .. #hits .. " hits via Anna mirror " .. base_url)
            return hits
        end
    end

    return {}
end

function Scraper.download_book(book, path, progress_callback, is_cancelled_func)
    if not book or not book.md5 then
        return "Failed, invalid book metadata"
    end

    local clean_title = Scraper.sanitize_name(book.title or "Book")
    local clean_author = Scraper.sanitize_name(book.author or "Author")
    local ext = tostring(book.format or book.extension or "epub"):lower()
    local filename = string.format("%s/%s_%s.%s", path:gsub("/+$", ""), clean_title, clean_author, ext)
    local temp_file = filename .. ".part"

    -- 1. Try server-assisted download ticket resolution if Ordpresse available
    local cfg = Scraper.getOrdpresseConfig()
    local direct_ticket_url = nil
    if cfg and cfg.endpoint_url then
        local sep = string.find(cfg.endpoint_url, "?", 1, true) and "&" or "?"
        local api_url = cfg.endpoint_url .. sep .. "action=annas_download_ticket&md5=" .. url_encode(book.md5)
        if cfg.api_key and cfg.api_key ~= "" then
            api_url = api_url .. "&api_key=" .. url_encode(cfg.api_key)
        end
        local extra_header = (cfg.api_key and cfg.api_key ~= "") and string.format("-H %s", shell_quote("X-API-Key: " .. cfg.api_key)) or ""
        local cmd = string.format(
            "curl -k -L -sS --max-time 8 %s -A %s %s 2>&1",
            extra_header,
            shell_quote(USER_AGENT),
            shell_quote(api_url)
        )
        local handle = io.popen(cmd, "r")
        local raw = nil
        if handle then
            raw = handle:read("*a")
            handle:close()
        end
        if raw and #raw > 0 then
            local ok, payload = pcall(json.decode, raw)
            if ok and type(payload) == "table" and payload.download_url then
                direct_ticket_url = payload.download_url
            end
        end
    end

    if direct_ticket_url then
        pcall(os.remove, temp_file)
        logger.info("AnnasFetch: Downloading from server-resolved ticket: " .. direct_ticket_url)
        local dl_cmd = string.format(
            "curl -k -L -sS --max-time 180 -A %s -c %s -b %s -o %s %s 2>&1",
            shell_quote(USER_AGENT),
            shell_quote(COOKIE_JAR),
            shell_quote(COOKIE_JAR),
            shell_quote(temp_file),
            shell_quote(direct_ticket_url)
        )
        os.execute(dl_cmd)
        local f = io.open(temp_file, "rb")
        if f then
            local head = f:read(512) or ""
            local sz = f:seek("end")
            f:close()
            local is_html = head:find("^%s*<!doctype html") or head:find("^%s*<html")
            if sz and sz > 1000 and not is_html then
                os.rename(temp_file, filename)
                logger.info("AnnasFetch: Download complete: " .. filename .. " (" .. sz .. " bytes)")
                return filename
            end
        end
    end

    -- 2. Direct mirror fallback
    local mirrors = Scraper.getActiveMirrors()
    local libgen_mirrors = mirrors.libgen or DEFAULT_MIRRORS.libgen

    for _, base_url in ipairs(libgen_mirrors) do
        if is_cancelled_func and is_cancelled_func() then
            pcall(os.remove, temp_file)
            return "Download cancelled"
        end

        local ads_url = string.format("%s/ads.php?md5=%s", base_url, book.md5)
        logger.info("AnnasFetch: Fetching download ticket from " .. ads_url)
        local page_html = Scraper.execCurl(ads_url, 15)

        if page_html and #page_html > 100 then
            local get_link = page_html:match('href="([^"]*get%.php[^"]*)"')
                or page_html:match("href='([^']*get%.php[^']*)'")

            local direct_download_url
            if get_link then
                direct_download_url = base_url .. "/" .. get_link:gsub("^/", "")
            else
                direct_download_url = string.format("%s/get.php?md5=%s", base_url, book.md5)
            end

            logger.info("AnnasFetch: Downloading from " .. direct_download_url)
            pcall(os.remove, temp_file)

            local dl_cmd = string.format(
                "curl -k -L -sS --max-time 180 -A %s -c %s -b %s -o %s %s 2>&1",
                shell_quote(USER_AGENT),
                shell_quote(COOKIE_JAR),
                shell_quote(COOKIE_JAR),
                shell_quote(temp_file),
                shell_quote(direct_download_url)
            )

            local dl_ok = os.execute(dl_cmd)
            local f = io.open(temp_file, "rb")
            if f then
                local head = f:read(512) or ""
                local sz = f:seek("end")
                f:close()

                local is_html = head:find("^%s*<!doctype html") or head:find("^%s*<html")
                if sz and sz > 1000 and not is_html then
                    os.rename(temp_file, filename)
                    logger.info("AnnasFetch: Download complete: " .. filename .. " (" .. sz .. " bytes)")
                    return filename
                else
                    logger.warn("AnnasFetch: Download from " .. base_url .. " returned HTML or invalid file.")
                    pcall(os.remove, temp_file)
                end
            end
        end
    end

    pcall(os.remove, temp_file)
    return "Failed, could not download book from any active mirrors. Please check Wi-Fi connection."
end

-- Backward compatibility for callers in main.lua & ui.lua
function scraper(query)
    return Scraper.search(query)
end

function download_book(...)
    return Scraper.download_book(...)
end

function sanitize_name(name)
    return Scraper.sanitize_name(name)
end

function check_url(url, timeout)
    local out = Scraper.execCurl(url, timeout)
    if out and #out > 0 then
        return "success", out
    end
    return "network_error", nil
end

function force_refresh_domains()
    return Scraper.getActiveMirrors()
end

return Scraper
