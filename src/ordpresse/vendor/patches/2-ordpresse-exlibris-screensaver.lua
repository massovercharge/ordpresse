-- @name Ordpresse Ex Libris Pausedisplay
-- @version 1.6.0
-- @description Viser et typografisk Ex Libris-kort med bogtitel og læseprocent på pauseskærmen.

local Blitbuffer = require("ffi/blitbuffer")
local CenterContainer = require("ui/widget/container/centercontainer")
local Font = require("ui/font")
local FrameContainer = require("ui/widget/container/framecontainer")
local OverlapGroup = require("ui/widget/overlapgroup")
local Screensaver = require("ui/screensaver")
local Screen = require("device").screen
local TextWidget = require("ui/widget/textwidget")
local VerticalGroup = require("ui/widget/verticalgroup")
local VerticalSpan = require("ui/widget/verticalspan")

local orig_show = Screensaver.show
Screensaver.show = function(self, ...)
    orig_show(self, ...)
    if not self.ui or not self.ui.document or not self.screensaver_widget then
        return
    end

    local props = self.ui.doc_props or {}
    local title = props.display_title or props.title or "Ukendt bog"
    local author = props.authors or ""
    local pct = self.ui.rolling and math.floor((self.ui.rolling:getLastPercent() or 0) * 100) or nil
    local pct_str = pct and string.format("Læst: %d %%", pct) or "I gang"

    local card = FrameContainer:new{
        background = Blitbuffer.COLOR_WHITE,
        bordersize = Screen:scaleBySize(2),
        color = Blitbuffer.COLOR_BLACK,
        padding = Screen:scaleBySize(14),
        margin = Screen:scaleBySize(20),
        VerticalGroup:new{
            align = "center",
            TextWidget:new{
                text = "— EX LIBRIS · ORDPRESSE —",
                face = Font:getFace("scfont", 14),
            },
            VerticalSpan:new{ width = Screen:scaleBySize(6) },
            TextWidget:new{
                text = title,
                face = Font:getFace("cfont", 20),
                max_width = math.floor(Screen:getWidth() * 0.78),
            },
            TextWidget:new{
                text = author ~= "" and (author .. "  ·  " .. pct_str) or pct_str,
                face = Font:getFace("cfont", 16),
            },
        },
    }

    self.screensaver_widget = OverlapGroup:new{
        dimen = Screen:getSize(),
        self.screensaver_widget,
        CenterContainer:new{
            dimen = Screen:getSize(),
            card,
        },
    }
end
