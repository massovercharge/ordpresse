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

function Scraper.getOrdpresseEndpoint()
    local ok, cfg = pcall(dofile, "plugins/ordpresse.koplugin/config.lua")
    if ok and type(cfg) == "table" and cfg.endpoint_url and cfg.endpoint_url ~= "" then
        return cfg.endpoint_url
    end
    return nil
end

function Scraper.execCurl(url, max_time)
    local timeout = max_time or 20
    local cmd = string.format(
        "curl -L -s --max-time %d -A %s -c %s -b %s %s 2>&1",
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

    local endpoint = Scraper.getOrdpresseEndpoint()
    if endpoint then
        local sep = string.find(endpoint, "?", 1, true) and "&" or "?"
        local api_url = endpoint .. sep .. "action=annas_mirrors"
        local raw = Scraper.execCurl(api_url, 3)
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
            local title = string.match(row, "<a[^>]-title=[^>]->(.-)</a>")
                or string.match(row, "edition%.php[^>]->(.-)</a>")
                or string.match(row, "<a[^>]-href=[\"'][^\"']*edition%.php[^\"']*[\"'][^>]*>(.-)</a>")
                or "Unknown Title"
            title = title:gsub("<[^>]+>", ""):gsub("%s+", " "):match("^%s*(.-)%s*$")

            local author = "Unknown Author"
            local cells = {}
            for cell in string.gmatch(row, "<td[^>]*>(.-)</td>") do
                table.insert(cells, cell:gsub("<[^>]+>", ""):gsub("%s+", " "):match("^%s*(.-)%s*$"))
            end
            if #cells >= 5 and cells[5] and cells[5] ~= "" then
                author = cells[5]
            end

            local year = (#cells >= 7 and cells[7] and cells[7] ~= "") and cells[7] or nil
            local size = (#cells >= 8 and cells[8] and cells[8] ~= "") and cells[8] or nil
            local ext = (#cells >= 9 and cells[9] and cells[9] ~= "") and cells[9]:lower() or "epub"

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
    local mirrors = Scraper.getActiveMirrors()
    local encoded_q = url_encode(query)

    for _, base_url in ipairs(mirrors.libgen or DEFAULT_MIRRORS.libgen) do
        local search_url = string.format("%s/index.php?req=%s&res=50", base_url, encoded_q)
        local html = Scraper.execCurl(search_url, 15)
        local hits = Scraper.parseLibgenHtml(html, base_url)
        if #hits > 0 then
            logger.info("AnnasFetch: Found " .. #hits .. " hits via Libgen mirror " .. base_url)
            return hits
        end
    end

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
                "curl -L -s --max-time 180 -A %s -c %s -b %s -o %s %s 2>&1",
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
