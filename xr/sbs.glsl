//!PARAM mpvr_layout
//!TYPE float
//!MINIMUM -1
//!MAXIMUM 2
-1

//!PARAM mpvr_swap
//!TYPE float
//!MINIMUM 0
//!MAXIMUM 1
0

//!PARAM mpvr_par
//!TYPE float
//!MINIMUM 0.01
//!MAXIMUM 100
1

//!HOOK MAIN
//!BIND HOOKED
//!WIDTH OUTPUT.w
//!HEIGHT OUTPUT.h
//!DESC MPVR independent SBS eye fitting
vec4 hook() {
    if (mpvr_layout < 0.0) return vec4(0.0, 0.0, 0.0, 1.0);
    float eye = step(0.5, HOOKED_pos.x);
    vec2 uv = vec2(fract(HOOKED_pos.x * 2.0), HOOKED_pos.y);
    // Each half of the transport is stretched to the entire XR canvas.
    float canvas_ar = target_size.x / target_size.y;
    float source_ar = HOOKED_size.x * mpvr_par / HOOKED_size.y;
    if (mpvr_layout > 0.5 && mpvr_layout < 1.5)
        source_ar *= 0.5; // full SBS: two unsqueezed source eyes
    vec2 fit = vec2(min(1.0, source_ar / canvas_ar),
                    min(1.0, canvas_ar / source_ar));
    uv = (uv - 0.5) / fit + 0.5;
    if (any(lessThan(uv, vec2(0.0))) || any(greaterThan(uv, vec2(1.0))))
        return vec4(0.0, 0.0, 0.0, 1.0);
    if (mpvr_layout > 0.5) {
        eye = abs(eye - mpvr_swap);
        uv.x = (uv.x + eye) * 0.5;
        // Clamp within this eye to avoid sampling the opposite eye's edge.
        uv.x = clamp(uv.x, eye * 0.5 + 0.5 / HOOKED_size.x,
                     (eye + 1.0) * 0.5 - 0.5 / HOOKED_size.x);
    }
    return HOOKED_tex(uv);
}
