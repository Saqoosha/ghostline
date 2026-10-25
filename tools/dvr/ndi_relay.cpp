// ndi_relay: an NDI|HX source (ZowieBox at JDL R7) received with the NDI SDK and sent again on this box as plain NDI, for the
// tracker and the recorder, which receive through cyndilib: cyndilib segfaults on HX video (SDK 6.1.1 and 6.3.2 alike), while
// the SDK's own receive takes the same source cleanly (1920x1080 BGRX 29.97, no drops). Video only; every frame is passed on
// with its timecode and timestamp. Reconnects on its own (the SDK's receiver does).
//   ndi_relay <source name substring> <name to send as>        e.g. ndi_relay ZowieBox_4_FPV FPV-RELAY
// build (rt4090): g++ -O2 -o ndi_relay ndi_relay.cpp -I"$SDK/include" -L"$SDK/lib/x86_64-linux-gnu" -lndi -Wl,-rpath,"$SDK/lib/x86_64-linux-gnu"
#include <cstddef>
#include <cstdlib>
#include <Processing.NDI.Lib.h>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <string>
static volatile std::sig_atomic_t stop = 0;
int main(int argc, char** argv) {
    if (argc < 3) { std::fprintf(stderr, "usage: ndi_relay <source substring> <send name>\n"); return 2; }
    if (!NDIlib_initialize()) { std::fprintf(stderr, "NDI init failed\n"); return 1; }
    std::signal(SIGTERM, [](int) { stop = 1; }); std::signal(SIGINT, [](int) { stop = 1; });
    NDIlib_find_instance_t f = NDIlib_find_create_v2(); NDIlib_source_t src{}; std::string name; uint32_t n = 0;
    while (!stop && name.empty()) {                           // wait for the source, however long
        NDIlib_find_wait_for_sources(f, 1000); const NDIlib_source_t* s = NDIlib_find_get_current_sources(f, &n);
        for (uint32_t i = 0; i < n; i++) if (std::strstr(s[i].p_ndi_name, argv[1])) { name = s[i].p_ndi_name; src.p_ndi_name = name.c_str(); break; }
    }
    if (stop) return 0;
    std::printf("relay %s -> %s\n", name.c_str(), argv[2]); std::fflush(stdout);
    NDIlib_recv_create_v3_t rc; rc.source_to_connect_to = src; rc.bandwidth = NDIlib_recv_bandwidth_highest; rc.allow_video_fields = false;
    rc.color_format = NDIlib_recv_color_format_UYVY_BGRA; rc.p_ndi_recv_name = "ghostline-relay";   // UYVY: what the sender encodes from, no conversion
    NDIlib_recv_instance_t r = NDIlib_recv_create_v3(&rc);
    NDIlib_send_create_t sc; sc.p_ndi_name = argv[2]; sc.clock_video = false; sc.clock_audio = false;   // paced by the source, not the clock
    NDIlib_send_instance_t snd = NDIlib_send_create(&sc);
    if (!r || !snd) { std::fprintf(stderr, "recv/send create failed\n"); return 1; }
    long frames = 0; auto t0 = std::chrono::steady_clock::now();
    while (!stop) {
        NDIlib_video_frame_v2_t v;
        if (NDIlib_recv_capture_v3(r, &v, nullptr, nullptr, 500) == NDIlib_frame_type_video) {
            NDIlib_send_send_video_v2(snd, &v); NDIlib_recv_free_video_v2(r, &v); frames++;
        }
        double s = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        if (s >= 10) { std::printf("%.1f fps, %d receivers\n", frames / s, NDIlib_send_get_no_connections(snd, 0)); std::fflush(stdout); frames = 0; t0 = std::chrono::steady_clock::now(); }
    }
    NDIlib_send_destroy(snd); NDIlib_recv_destroy(r); NDIlib_find_destroy(f); NDIlib_destroy(); return 0;
}
