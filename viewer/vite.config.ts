import { defineConfig } from 'vite'
// public/data is a symlink to build/dvr/hdz_0067: the scene (.sog), the videos and the poses.
export default defineConfig({ server: { fs: { allow: ['..'] } }, build: { target: 'es2022' } })
