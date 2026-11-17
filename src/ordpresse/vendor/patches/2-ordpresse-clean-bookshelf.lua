-- @name Ordpresse Clean Bookshelf & Badges
-- @version 1.6.0
-- @description Pænere mosaik-bogreol med skarpe læseprocent-badges og skjulte filendelser.

local userpatch = require("userpatch")
local logger = require("logger")

userpatch.registerPatchPluginFunc("coverbrowser", function(CoverBrowser)
    local ok, MosaicMenu = pcall(require, "mosaicmenu")
    if not ok or not MosaicMenu then return end

    -- Skjul ".epub" / ".pdf" / ".mobi" filendelser i bogreolens tekstvisning
    local orig_updateItems = MosaicMenu.updateItems
    if orig_updateItems then
        MosaicMenu.updateItems = function(self, select_number)
            for _, item in ipairs(self.item_table or {}) do
                if item.text and not item.is_directory then
                    item.text = item.text:gsub("%.[eE][pP][uU][bB]$", "")
                    item.text = item.text:gsub("%.[pP][dD][fF]$", "")
                    item.text = item.text:gsub("%.[mM][oO][bB][iI]$", "")
                end
            end
            return orig_updateItems(self, select_number)
        end
    end
    logger.info("Ordpresse Patch: Clean Bookshelf v1.6.0 indlæst.")
end)
