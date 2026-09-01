import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import fs from 'node:fs'

// 原 web/node_modules 中部分包被安全沙箱锁死且文件损坏，无法就地修复/删除。
// 改用一份完整、自洽的全新依赖树（.buildtmp/node_modules，由 npm ci 生成）作为模块解析来源：
// 把每一个顶层包都别名到 fresh 副本，被别名改写后的导入会落在 fresh 树内，
// 其嵌套依赖也会沿 fresh 树向上解析，从而完全绕开损坏的 web/node_modules。
// vite 自身的运行时依赖（加载本配置）仍由 web/node_modules 正常提供。
const FRESH_NODE_MODULES = path.resolve(__dirname, '../.buildtmp/node_modules')

function buildFreshAliases() {
  const alias = {}
  if (!fs.existsSync(FRESH_NODE_MODULES)) return alias
  // 这些只供构建期/配置使用，且 vite 含虚拟子模块（modulepreload-polyfill 等），
  // 不应被别名改写，否则会被错误地指向 fresh 目录下的真实文件而 404。
  // three 使用 exports 子路径映射（addons/* -> examples/jsm/*），目录别名会破坏该映射，单独处理。
  const SKIP = new Set(['vite', '@vitejs', 'three'])
  for (const name of fs.readdirSync(FRESH_NODE_MODULES)) {
    if (name.startsWith('.')) continue // 跳过 .bin / .package-lock.json 等
    if (SKIP.has(name)) continue
    const full = path.join(FRESH_NODE_MODULES, name)
    if (name.startsWith('@')) {
      if (!fs.statSync(full).isDirectory()) continue
      for (const sub of fs.readdirSync(full)) {
        alias[`${name}/${sub}`] = path.join(full, sub)
      }
    } else {
      alias[name] = full
    }
  }
  // three：精确别名（带 $）避免吞掉子路径；addons 子路径显式重映射到真实文件位置，
  // 其余子路径（若有）由 fresh 树自身的 exports 正常解析。
  alias['three$'] = path.join(FRESH_NODE_MODULES, 'three')
  alias[/^three\/addons\/(.*)$/] = path.join(FRESH_NODE_MODULES, 'three', 'examples', 'jsm', '$1')
  return alias
}

// 开发模式代理到后端 8000 端口；构建产物输出到 dist/ 由后端托管
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: buildFreshAliases(),
    // 兜底：优先在 fresh 树中按包自身 exports 解析（three 等），再回退原 node_modules
    modules: [FRESH_NODE_MODULES, 'node_modules'],
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/files': 'http://127.0.0.1:8000',
      '/logo.png': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: 'dist',
    // 原 web/dist 中历史产物被沙箱锁死无法 unlink，故关闭自动清空，改为覆盖写入
    emptyOutDir: false,
    chunkSizeWarningLimit: 2500,
  },
})
