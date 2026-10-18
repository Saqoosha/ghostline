#include <metal_stdlib>
using namespace metal;

// The scan's points, drawn inside the ARSCNView's own pass (PointRenderer): white, blended over the camera image with one alpha.
struct Pt { packed_float3 pos; uchar4 color; };            // 16 bytes, as rt_vps.py /points sends them (the colour is not drawn)
struct U { float4x4 mvp; float alpha; float size; };
struct VOut { float4 position [[position]]; float size [[point_size]]; float4 color; };

vertex VOut pointsVertex(uint vid [[vertex_id]], const device Pt* pts [[buffer(0)]], constant U& u [[buffer(1)]]) {
    VOut o;
    o.position = u.mvp * float4(float3(pts[vid].pos), 1);
    o.size = u.size;
    o.color = float4(u.alpha, u.alpha, u.alpha, u.alpha);   // white, premultiplied: stands out against the camera image
    return o;
}

fragment float4 pointsFragment(VOut in [[stage_in]]) { return in.color; }

// The flight (PathRenderer). Metal draws lines 1 px wide, so the path is a triangle strip two vertices per frame, pushed apart
// across the path's direction on the screen by `width` px. n: frames in the path (the neighbours are clamped to it).
// tail < head: a trail that thins (to 30%) and fades towards its old end (frame tail) as the PC pages draw it; else uniform.
struct LU { float4x4 mvp; float4 color; float2 viewport; float width; int n; int tail; int head; };
struct PVOut { float4 position [[position]]; float size [[point_size]]; float4 color; };

vertex PVOut lineVertex(uint vid [[vertex_id]], const device packed_float3* p [[buffer(0)]], constant LU& u [[buffer(1)]]) {
    int i = int(vid / 2); float side = (vid % 2 == 0) ? -1.0 : 1.0;
    int a = max(i - 1, 0), b = min(i + 1, u.n - 1);
    float4 c = u.mvp * float4(float3(p[i]), 1), ca = u.mvp * float4(float3(p[a]), 1), cb = u.mvp * float4(float3(p[b]), 1);
    float2 d = cb.xy / max(cb.w, 1e-3) * u.viewport - ca.xy / max(ca.w, 1e-3) * u.viewport;
    d = length(d) < 1e-4 ? float2(1, 0) : normalize(d);
    float fresh = u.tail < u.head ? clamp(float(i - u.tail) / float(u.head - u.tail), 0.0, 1.0) : 1.0;
    c.xy += float2(-d.y, d.x) * side * u.width * (0.3 + 0.7 * fresh) / u.viewport * c.w;   // half the width each side, in NDC (2 per viewport)
    PVOut o; o.position = c; o.size = 1;
    float alpha = u.color.a * fresh;
    o.color = float4(u.color.rgb * alpha, alpha);           // premultiplied
    return o;
}

vertex PVOut dotVertex(uint vid [[vertex_id]], const device packed_float3* p [[buffer(0)]], constant LU& u [[buffer(1)]]) {
    PVOut o; o.position = u.mvp * float4(float3(p[vid]), 1); o.size = u.width;
    o.color = float4(u.color.rgb * u.color.a, u.color.a);
    return o;
}

fragment float4 pathFragment(PVOut in [[stage_in]]) { return in.color; }

fragment float4 dotFragment(PVOut in [[stage_in]], float2 pc [[point_coord]]) {
    if (length(pc - 0.5) > 0.5) discard_fragment();
    return in.color;
}

// The room's solid splats as discs, into depth only, before the flight: the flight then hides behind pillars and furniture.
// Each disc is pushed `bias` m away from the camera so the path does not sink into the floor it skims.
struct Occ { packed_float3 pos; float r; };                // 16 bytes, as rt_vps.py /occluders sends them
struct OU { float4x4 mv; float4x4 p; float2 viewport; float bias; };

vertex PVOut occVertex(uint vid [[vertex_id]], const device Occ* o [[buffer(0)]], constant OU& u [[buffer(1)]]) {
    float4 v = u.mv * float4(float3(o[vid].pos), 1);
    PVOut out;
    if (v.z > -0.05) { out.position = float4(0, 0, -2, 1); out.size = 1; out.color = 0; return out; }   // behind the camera: clipped
    v.z -= u.bias;                                          // the camera looks down -z
    float4 c = u.p * v;
    out.position = c;
    out.size = clamp(o[vid].r * u.p[1][1] * u.viewport.y / c.w, 1.0, 256.0);   // diameter in px: 2r x (P11 x H / 2) / w
    out.color = 0;
    return out;
}

fragment float4 occFragment(PVOut in [[stage_in]], float2 pc [[point_coord]]) {
    if (length(pc - 0.5) > 0.5) discard_fragment();
    return 0;
}

// The FPV transition (SplatOverlay with a Warp): the splats and the flight are drawn first through a wide pinhole into a texture
// (focal fTex px), then every screen pixel takes its colour from it through the DVR's lens - OpenCV's fisheye model,
// theta_d = theta (1 + k1 theta^2 + k2 theta^4 + k3 theta^6 + k4 theta^8) - with the screen's focal fOut. s blends the lens in:
// 0 is the plain pinhole (the view still matches ARKit's camera), 1 the DVR's own picture. Beyond the DVR's 4:3 picture
// (more than halfW px either side of the centre) the screen fades to black as `bars` grows.
struct WU { float2 size; float fOut; float fTex; float s; float bars; float halfW; float thMax; float4 k; };   // thMax: the lens's monotonic end
struct WOut { float4 position [[position]]; };

vertex WOut warpVertex(uint vid [[vertex_id]]) {           // one triangle over the screen
    float2 p = float2((vid << 1) & 2, vid & 2);
    WOut o; o.position = float4(p * 2 - 1, 0, 1); return o;
}

fragment float4 warpFragment(WOut in [[stage_in]], texture2d<float> tex [[texture(0)]], constant WU& u [[buffer(0)]]) {
    constexpr sampler smp(filter::linear, address::clamp_to_zero);
    float2 c = u.size * 0.5;
    float2 d = (in.position.xy - c) / u.fOut;
    float td = length(d), r = td;
    bool lost = false;
    if (td > 1e-5 && u.s > 0) {
        float th = min(td, u.thMax), f = 0;                 // invert the lens by Newton's method from theta = theta_d
        for (int i = 0; i < 6; i++) {
            float t2 = th * th;
            f = th * (1 + t2 * (u.k.x + t2 * (u.k.y + t2 * (u.k.z + t2 * u.k.w)))) - td;
            float g = 1 + t2 * (3 * u.k.x + t2 * (5 * u.k.y + t2 * (7 * u.k.z + t2 * 9 * u.k.w)));
            th = clamp(th - f / max(g, 1e-3), 0.0, u.thMax);
        }
        lost = abs(f) > 1e-3;                               // past the lens's reach: no ray, black (not the centre's colour)
        r = mix(td, tan(th), u.s);
    }
    float2 p = td > 1e-5 ? d * (r / td) : d;
    float2 uv = (c + p * u.fTex) / u.size;
    float4 col = lost ? float4(0) : tex.sample(smp, uv);
    float black = abs(in.position.x - c.x) > u.halfW ? u.bars : (1 - col.a) * u.bars;   // the sides, and where the render has nothing
    return mix(col, float4(0, 0, 0, 1), black);
}
