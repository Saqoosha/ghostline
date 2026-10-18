#include <metal_stdlib>
using namespace metal;

// The scan's points, drawn inside the ARSCNView's own pass (PointRenderer): blended over the camera image with their own alpha.
struct Pt { packed_float3 pos; uchar4 color; };            // 16 bytes, as rt_vps.py /points sends them
struct U { float4x4 mvp; float alpha; float size; float linearize; };
struct VOut { float4 position [[position]]; float size [[point_size]]; float4 color; };

vertex VOut pointsVertex(uint vid [[vertex_id]], const device Pt* pts [[buffer(0)]], constant U& u [[buffer(1)]]) {
    VOut o;
    o.position = u.mvp * float4(float3(pts[vid].pos), 1);
    o.size = u.size;
    o.color = float4(u.alpha, u.alpha, u.alpha, u.alpha);   // white, premultiplied: stands out against the camera image (the scan's own colours blended in)
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
    out.size = clamp(o[vid].r * u.p[1][1] * u.viewport.y / c.w, 1.0, 256.0);   // diameter in px
    out.color = 0;
    return out;
}

fragment float4 occFragment(PVOut in [[stage_in]], float2 pc [[point_coord]]) {
    if (length(pc - 0.5) > 0.5) discard_fragment();
    return 0;
}
