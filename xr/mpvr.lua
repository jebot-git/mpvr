-- Source metadata is read before the metadata-only format filter tags output SBS.
local mp = require 'mp'
local options = require 'mp.options'
local o = {layout = 'auto', swap = false}
options.read_options(o, 'mpvr')
local function token(stem, name)
    return ('_' .. stem:upper():gsub('[^%w]', '_') .. '_'):find('_' .. name .. '_', 1, true) ~= nil
end
local function configure()
    local p = mp.get_property_native('video-params')
    if not p or not p.w or not p.h then return end
    local layout, swap = o.layout, o.swap
    local name = mp.get_property('filename/no-ext', '')
    local stereo = p['stereo-in'] or 'mono'
    if layout == 'auto' then
        if stereo == 'sbs2l' or stereo == 'sbs2r' then
            -- Container display dimensions describe one eye. Preserve that
            -- aspect, whether the stored pair is full or half width.
            layout = 'metadata-sbs'
        elseif stereo ~= 'mono' and stereo ~= 'no' then
            mp.msg.error('Unsupported stereo layout: ' .. stereo .. '; use --sbs-layout to override')
            mp.commandv('quit', 2)
            return
        elseif token(name, 'HSBS') or token(name, 'HALF_SBS') or token(name, 'H_SBS') then
            layout = 'half-sbs'
        elseif token(name, 'SBS') or token(name, 'FSBS') or token(name, 'FULL_SBS') or token(name, 'F_SBS') then
            layout = 'full-sbs'
        else
            layout = 'mono'
        end
    end
    if stereo == 'sbs2r' then swap = not swap end
    if stereo == 'mono' or stereo == 'no' then
        if token(name, 'RL') or token(name, 'CROSS') or token(name, 'CROSS_EYED') then swap = not swap end
    end
    local par = p.par or 1
    if layout == 'full-sbs' and (stereo == 'sbs2l' or stereo == 'sbs2r') then
        -- SBS container display dimensions already describe one eye; undo
        -- that factor before the full-width shader divides the pair again.
        par = par * 2
    end
    local mode = ({mono = 0, ['full-sbs'] = 1, ['half-sbs'] = 2, ['metadata-sbs'] = 2})[layout]
    if not mode then return end
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
