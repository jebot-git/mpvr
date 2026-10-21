-- Source metadata is read before the metadata-only format filter tags output SBS.
local mp = require 'mp'
local options = require 'mp.options'
local utils = require 'mp.utils'
local o = {layout = 'auto', swap = false, stereo_only = false}
options.read_options(o, 'mpvr')
local function token(stem, name)
    return ('_' .. stem:upper():gsub('[^%w]', '_') .. '_'):find('_' .. name .. '_', 1, true) ~= nil
end
local function configure()
    local p = mp.get_property_native('video-params')
    if not p or not p.w or not p.h or not p.pixelformat or not p.par then return end
    local layout, swap = o.layout, o.swap
    local name = mp.get_property('filename/no-ext', '')
    local stereo = p['stereo-in'] or 'mono'
    local metadata_par = 1
    -- yt-dlp's original metadata survives mpv's separate audio/video streams.
    local result = mp.get_property_native('user-data/mpv/ytdl/json-subprocess-result')
    if result and result.status == 0 and result.stdout then
        local data = utils.parse_json(result.stdout)
        if data then
            name = data.title or name
            local video = data
            for _, format in ipairs(data.requested_formats or {}) do
                if format.vcodec and format.vcodec ~= 'none' then video = format; break end
            end
            local modes = {left_right='sbs2l', right_left='sbs2r', top_bottom='ab2l', bottom_top='ab2r'}
            local mode = video.stereo_mode or data.stereo_mode
            if mode and modes[mode] and (stereo == 'mono' or stereo == 'no') then
                stereo = modes[mode]
                metadata_par = (stereo == 'sbs2l' or stereo == 'sbs2r') and 0.5 or 2
            end
        end
    end
    if layout == 'auto' then
        if stereo == 'sbs2l' or stereo == 'sbs2r' then
            -- Container display dimensions describe one eye. Preserve that
            -- aspect, whether the stored pair is full or half width.
            layout = 'metadata-sbs'
        elseif stereo == 'ab2l' or stereo == 'ab2r' then
            layout = 'metadata-tb'
        elseif stereo ~= 'mono' and stereo ~= 'no' then
            if o.stereo_only then
                layout = 'mono' -- hand unsupported/2D content back to the desktop
            else
                mp.msg.error('Unsupported stereo layout: ' .. stereo .. '; use --sbs-layout to override')
                mp.commandv('quit', 2)
                return
            end
        elseif token(name, 'HTB') or token(name, 'HALF_TB') then
            layout = 'half-tb'
        elseif token(name, 'TB') or token(name, 'TAB') or token(name, 'OVER_UNDER') or token(name, 'OVERUNDER') or token(name, 'FTB') or token(name, 'FULL_TB') then
            layout = 'full-tb'
        elseif token(name, 'HSBS') or token(name, 'HALF_SBS') or token(name, 'H_SBS') then
            layout = 'half-sbs'
        elseif token(name, 'SBS') or token(name, 'FSBS') or token(name, 'FULL_SBS') or token(name, 'F_SBS') then
            layout = 'full-sbs'
        else
            layout = 'mono'
        end
    end
    if stereo == 'sbs2r' or stereo == 'ab2r' then swap = not swap end
    if stereo == 'mono' or stereo == 'no' then
        if token(name, 'RL') or token(name, 'CROSS') or token(name, 'CROSS_EYED') then swap = not swap end
    end
    local par = (p.par or 1) * metadata_par
    if layout == 'full-sbs' and (stereo == 'sbs2l' or stereo == 'sbs2r') then
        -- SBS container display dimensions already describe one eye; undo
        -- that factor before the full-width shader divides the pair again.
        par = par * 2
    end
    if layout == 'full-tb' and (stereo == 'ab2l' or stereo == 'ab2r') then par = par / 2 end
    local mode = ({mono = 0, ['full-sbs'] = 1, ['half-sbs'] = 2, ['metadata-sbs'] = 2, ['full-tb'] = 3, ['half-tb'] = 4, ['metadata-tb'] = 4})[layout]
    if not mode then return end
    if mode == 0 and o.stereo_only then
        mp.set_property('glsl-shader-opts', 'mpvr_layout=-1')
        mp.set_property_native('pause', true)
        mp.set_property_native('user-data/mpvr', {layout = 'mono', desktop = true,
            path = mp.get_property('path'), start = mp.get_property_number('time-pos', 0),
            volume = mp.get_property_number('volume', 100), mute = mp.get_property_native('mute'),
            speed = mp.get_property_number('speed', 1)})
        return
    end
    mp.set_property('glsl-shader-opts', string.format('mpvr_layout=%d,mpvr_swap=%d,mpvr_par=%.9f',
        mode, swap and 1 or 0, par))
    mp.set_property_native('user-data/mpvr', {layout = layout, swap = swap, par = par})
    mp.msg.info('XR input: ' .. layout .. (swap and ', swapped eyes' or ''))
end
mp.observe_property('video-params', 'native', configure)
mp.register_event('file-loaded', configure)

mp.register_event('start-file', function()
    mp.set_property('glsl-shader-opts', 'mpvr_layout=-1')
end)
