import { defineConfig, type Plugin } from 'vite'
import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
// public/data is a symlink to build/dvr/hdz_0067: the scene (.sog), the videos and the poses.
// The marking tool (index.html "mark") saves the human correspondences there as marks.json through
// this dev-only endpoint, so the solver on the Mac reads the same file the page writes.
const marksApi: Plugin = {
  name: 'marks-api',
  configureServer(server) {
    server.middlewares.use('/api/marks', (req, res) => {
      // ?data=<flight>: that flight's folder under public/ (default: public/data, hdz_0067)
      const dir = new URL(req.url ?? '', 'http://x').searchParams.get('data') ?? 'data'
      if (!/^[\w-]+$/.test(dir)) { res.statusCode = 400; res.end('bad data'); return }
      const file = resolve(__dirname, 'public', dir, 'marks.json')
      if (req.method === 'GET') { res.setHeader('content-type', 'application/json'); res.end(existsSync(file) ? readFileSync(file, 'utf8') : '{"landmarks":{},"marks":[]}'); return }
      if (req.method === 'POST') { let body = ''; req.on('data', c => body += c); req.on('end', () => { writeFileSync(file, body); res.end('ok') }); return }
      res.statusCode = 405; res.end()
    })
  }
}
// The pad tool (index.html "pad") saves where the drone sat before takeoff as <flight>/pad.json; takeoff.py reads it.
const padApi: Plugin = {
  name: 'pad-api',
  configureServer(server) {
    server.middlewares.use('/api/pad', (req, res) => {
      const dir = new URL(req.url ?? '', 'http://x').searchParams.get('data') ?? ''
      if (!/^[\w-]+$/.test(dir)) { res.statusCode = 400; res.end('bad data'); return }
      const file = resolve(__dirname, 'public', dir, 'pad.json')
      if (req.method === 'GET') { if (!existsSync(file)) { res.statusCode = 404; res.end(); return } res.setHeader('content-type', 'application/json'); res.end(readFileSync(file, 'utf8')); return }
      if (req.method === 'POST' && req.headers['content-type'] === 'application/json') { let body = ''; req.on('data', c => body += c); req.on('end', () => { JSON.parse(body); writeFileSync(file, body); res.end('ok') }); return }
      res.statusCode = 405; res.end()
    })
  }
}
// VITE_BASE is the path the page is published under (tools/publish-dvr-viewer.sh sets /<name>/).
export default defineConfig({ base: process.env.VITE_BASE ?? '/', server: { fs: { allow: ['..'] } }, build: { target: 'es2022', copyPublicDir: false, rollupOptions: { input: { main: resolve(__dirname, 'index.html'), race: resolve(__dirname, 'race.html') } } }, plugins: [marksApi, padApi] })   // public/data is the dev symlink to the data, not an asset
