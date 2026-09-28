// The capture's own sky: one equirectangular image baked into a cubemap on the CPU, once (the viewer and the race page).
import * as pc from 'playcanvas'

// +x -x +y -y +z -z, in the GL cubemap convention (v runs down each face).
const FACE: ((u: number, v: number) => number[])[] = [
  (u, v) => [1, -v, -u], (u, v) => [-1, -v, u],
  (u, v) => [u, 1, v], (u, v) => [u, -1, -v],
  (u, v) => [u, -v, 1], (u, v) => [-u, -v, -1],
]
export function bakeSky(device: pc.GraphicsDevice, img: HTMLImageElement, size = 512) {
  const c = document.createElement('canvas')
  c.width = img.naturalWidth; c.height = img.naturalHeight
  const ctx = c.getContext('2d', { willReadFrequently: true })!
  ctx.drawImage(img, 0, 0)
  const src = ctx.getImageData(0, 0, c.width, c.height).data, sw = c.width, sh = c.height
  const levels: Uint8Array[] = []
  for (let f = 0; f < 6; f++) {
    const px = new Uint8Array(size * size * 4)
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
      const d = FACE[f](2 * (x + 0.5) / size - 1, 2 * (y + 0.5) / size - 1)
      // PlayCanvas samples the skybox with `dir.x *= -1.0` (skyboxPS, the SKY_CUBEMAP branch):
      // the cubemap face layout is the left-handed D3D one, and the engine flips x to meet it.
      // So the world direction landing on this texel is the face direction with x negated -
      // bake it the other way and the sky comes out mirrored east for west, which a rotation
      // cannot undo. Caught by the sun sitting on the wrong side.
      d[0] = -d[0]
      const n = Math.hypot(d[0], d[1], d[2])
      // Bilinear, wrapping in longitude and clamping in latitude - a nearest sample shows the
      // source pixels as blocks on the zenith faces, where one texel covers a whole column.
      const sx = (0.5 + Math.atan2(d[0] / n, -d[2] / n) / (2 * Math.PI)) * sw - 0.5
      const sy = (Math.acos(Math.max(-1, Math.min(1, d[1] / n))) / Math.PI) * sh - 0.5
      const x0 = Math.floor(sx), y0 = Math.max(0, Math.min(sh - 1, Math.floor(sy)))
      const fx = sx - x0, fy = sy - y0
      const x1 = (((x0 + 1) % sw) + sw) % sw, xa = ((x0 % sw) + sw) % sw
      const y1 = Math.min(sh - 1, y0 + 1)
      const o = (y * size + x) * 4
      for (let i = 0; i < 3; i++) {
        const a = src[(y0 * sw + xa) * 4 + i] * (1 - fx) + src[(y0 * sw + x1) * 4 + i] * fx
        const b = src[(y1 * sw + xa) * 4 + i] * (1 - fx) + src[(y1 * sw + x1) * 4 + i] * fx
        px[o + i] = a * (1 - fy) + b * fy
      }
      px[o + 3] = 255
    }
    levels.push(px)
  }
  return new pc.Texture(device, {
    name: 'sky', cubemap: true, width: size, height: size, format: pc.PIXELFORMAT_RGBA8,
    mipmaps: false, minFilter: pc.FILTER_LINEAR, magFilter: pc.FILTER_LINEAR,
    addressU: pc.ADDRESS_CLAMP_TO_EDGE, addressV: pc.ADDRESS_CLAMP_TO_EDGE,
    levels: [levels],
  })
}
