// From the MGP page's racekit.ts (branch reconstruct-mgp-qualifier-tracks), the one piece the replay needs.
// PlayCanvas's CPU splat sort drops a request made while the previous one is still running, yet records the view as
// sorted; a camera that then stays still (top, paused) keeps the order of an earlier pose, which smears dark haze over
// the ground. So once the camera settles, one more sort is asked for directly, on a frame when the sorter is idle (so it
// is not dropped too). Reads PlayCanvas internals: check it after upgrading PlayCanvas.
import type * as pc from 'playcanvas'
export function resortWhenIdle(app: pc.AppBase) {
  let sent = false
  ;(app.renderer as any).gsplatDirector?.camerasMap.forEach((cd: any) => cd.layersMap.forEach((ld: any) => {
    const m = ld.gsplatManager; if (m && (!m.cpuSorter || m.cpuSorter.jobsInFlight === 0)) { m.sortNeeded = true; sent = true }
  }))
  return sent
}
