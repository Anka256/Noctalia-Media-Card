--!nonstrict
-- Lock-screen media card: cover, title, artist, controls, full-width seek bar.
-- All "what is really playing" logic lives in probe.py (streams JSON lines);
-- this file only renders and sends playerctl commands.
local GOLD = "#e9c896"
local TEXT = "#ece4da"
local DIM = "#bdb3a8"
local FONT = "Cinzel"
local CARDW, CARDH, PAD = 400, 182, 14
local INNERW = CARDW - 2 * PAD
local TEXTW = INNERW - 92 - 14
local DEBUG = false

-- last state from probe.py: { mode, player, status, title, artist, art, pos, len, control, seek }
local st = { mode = "none" }
local lastSet = -1      -- slider value we rendered (to ignore our own echo in onChange)
local drag = nil        -- value while the user drags the slider
local seekTarget = nil  -- optimistic position after a seek, until the player confirms it
local seekUntil = 0
local toggledUntil = 0  -- optimistic play/pause until the player confirms it
local toggledStatus = nil

local function fmt(s)
  s = math.max(0, math.floor(s))
  return string.format("%d:%02d", s // 60, s % 60)
end

local FOCUS = noctalia.expandPath("~/.cache/noctalia-seekbar/focus")

local function cmd(...)
  if st.player == nil or st.player == "" or st.control ~= true then return false end
  noctalia.writeFile(FOCUS, st.player) -- probe.py keeps this player on the card while paused
  noctalia.runAsync({ "playerctl", "-p", st.player, ... })
  return true
end

local function render()
  if st.mode == "none" or st.mode == nil then
    desktopWidget.render(ui.column({}, {}))
    return
  end

  local len = tonumber(st.len) or 0
  local pos = tonumber(st.pos) or -1
  if seekTarget ~= nil then pos = seekTarget end
  if drag ~= nil then pos = drag end
  local status = st.status
  if toggledStatus ~= nil then status = toggledStatus end
  local control = st.control == true
  local canSeek = st.seek == true and control and len > 0

  local maxv = len > 0 and len or 1
  lastSet = math.floor(math.clamp(pos, 0, maxv) + 0.5)
  if DEBUG then noctalia.log(string.format("seekbar R mode=%s pos=%s len=%s st=%s t=%s", st.mode, tostring(pos), tostring(len), tostring(status), tostring(st.title))) end

  local cover
  if st.art ~= nil and st.art ~= "" and noctalia.fileExists(st.art) then
    cover = ui.image({ key = "cover", path = st.art, width = 92, height = 92, radius = 10, fit = "cover" })
  else
    cover = ui.box({ key = "cover", width = 92, height = 92, radius = 10, fill = "#2a2224" })
  end

  local top = {
    cover,
    ui.column({ gap = 4, width = TEXTW, justify = "center" }, {
      ui.label({ text = st.title or "", fontSize = 19, fontWeight = "bold", color = GOLD, fontFamily = FONT, maxLines = 1, maxWidth = TEXTW }),
      ui.label({ text = st.artist or "", fontSize = 15, color = TEXT, fontFamily = FONT, maxLines = 1, maxWidth = TEXTW }),
      ui.row({ gap = 6, align = "center", opacity = control and 1.0 or 0.4 }, {
        ui.button({ glyph = "media-prev", variant = "ghost", controlSize = "md", glyphSize = 22, onClick = "onPrev", enabled = control }),
        ui.button({ glyph = status == "Playing" and "media-pause" or "media-play", variant = "ghost", controlSize = "md", glyphSize = 22, onClick = "onToggle", enabled = control }),
        ui.button({ glyph = "media-next", variant = "ghost", controlSize = "md", glyphSize = 22, onClick = "onNext", enabled = control }),
      }),
    }),
  }

  desktopWidget.render(ui.column({ gap = 4, padding = PAD, radius = 14, fill = "#120d0eb3", width = CARDW, height = CARDH }, {
    ui.row({ gap = 14, align = "center", width = INNERW, height = 92 }, top),
    ui.slider({ key = "seek", width = INNERW, min = 0, max = maxv, step = 1, value = lastSet, enabled = canSeek,
                controlSize = "sm", onChange = "onSeekChange", onDragEnd = "onSeekEnd" }),
    ui.row({ align = "center", width = INNERW }, {
      ui.label({ text = pos >= 0 and fmt(pos) or "", width = 60, fontSize = 16, fontWeight = "bold", color = GOLD, fontFamily = FONT }),
      ui.spacer({ flexGrow = 1 }),
      ui.label({ text = len > 0 and fmt(len) or "--:--", width = 60, textAlign = "end", fontSize = 16, color = DIM, fontFamily = FONT }),
    }),
  }))
end

local function onState(line)
  local data = noctalia.json.decode(line)
  if type(data) ~= "table" then return end
  local now = os.time()
  -- a new track clears every optimistic override
  if data.title ~= st.title or data.player ~= st.player then
    seekTarget, toggledStatus = nil, nil
  end
  -- keep the seek target until the player reports a nearby position (or 3 s pass)
  if seekTarget ~= nil and (now > seekUntil or math.abs((tonumber(data.pos) or 0) - seekTarget) < 2.5) then
    seekTarget = nil
  end
  if toggledStatus ~= nil and (now > toggledUntil or data.status == toggledStatus) then
    toggledStatus = nil
  end
  st = data
  render()
end

local probe = noctalia.pluginDir() .. "/probe.py"
if not noctalia.runStream("exec python3 -I -u '" .. probe .. "'", onState) then
  noctalia.log("seekbar: could not start probe.py")
end

function onSeekChange(value)
  local n = tonumber(value)
  if n == nil or math.abs(n - lastSet) < 0.5 then return end -- echo of our own value, not a drag
  drag = n
  render()
end

function onSeekEnd()
  if drag == nil or st.seek ~= true then drag = nil; return end
  seekTarget = math.max(drag, 0.1) -- position 0 makes Zen/Firefox ignore the seek
  seekUntil = os.time() + 3
  drag = nil
  cmd("position", string.format("%.1f", seekTarget))
  render()
end

function onPrev() cmd("previous") end
function onNext() cmd("next") end
function onToggle()
  if st.control ~= true then return end
  toggledStatus = (st.status == "Playing") and "Paused" or "Playing"
  toggledUntil = os.time() + 2
  cmd("play-pause")
  render()
end

render()

-- test hooks: noctalia msg plugin onur/seekbar:seek all <event> <payload>
function onIpc(event, payload)
  if event == "debug" then DEBUG = payload == "on"
  elseif event == "drag" then onSeekChange(payload)
  elseif event == "end" then onSeekEnd()
  elseif event == "next" then onNext()
  elseif event == "prev" then onPrev()
  elseif event == "toggle" then onToggle()
  end
end
