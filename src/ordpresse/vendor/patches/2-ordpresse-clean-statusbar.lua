-- @name Ordpresse Typografisk Bundlinje
-- @version 1.6.0
-- @description Roligere typografisk separator ( · ) og renere layout i KOReaders statuslinje.

local ok, ReaderFooter = pcall(require, "apps/reader/modules/readerfooter")
if ok and ReaderFooter then
    local orig_init = ReaderFooter.init
    ReaderFooter.init = function(self, ...)
        orig_init(self, ...)
        if self.settings then
            -- Brug luftig bogtrykker-prik mellem kapitel, sidetal og klokkeslæt
            self.settings.items_separator = "   ·   "
            self.settings.progress_style_thin = true
        end
    end
end
