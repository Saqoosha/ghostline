import { defineConfig, type Plugin } from 'vite'
import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
// public/data is a symlink to build/dvr/hdz_0067: the scene (.sog), the videos and the poses.
// The marking tool (index.html "mark") saves the human correspondences there as marks.json through
// this dev-only endpoint, so the solver on the Mac reads the same file the page writes.
const marksApi: Plugin = {
  name: 'marks-api',
  configureServer(server) {
    const file = resolve(__dirname, 'public/data/marks.json')
    server.middlewares.use('/api/marks', (req, res) => {
      if (req.method === 'GET') { res.setHeader('content-type', 'application/json'); res.end(existsSync(file) ? readFileSync(file, 'utf8') : '{"landmarks":{},"marks":[]}'); return }
      if (req.method === 'POST') { let body = ''; req.on('data', c => body += c); req.on('end', () => { writeFileSync(file, body); res.end('ok') }); return }
      res.statusCode = 405; res.end()
    })
  }
}
// VITE_BASE is the path the page is published under (tools/publish-dvr-viewer.sh sets /dvr/<name>/).
export default defineConfig({ base: process.env.VITE_BASE ?? '/', server: { fs: { allow: ['..'] } }, build: { target: 'es2022', copyPublicDir: false }, plugins: [marksApi] })   // public/data is the dev symlink to the data, not an asset
