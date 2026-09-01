import React, { createContext, useEffect, useLayoutEffect, useMemo, useState, useContext } from 'react'
import ReactDOM from 'react-dom/client'
import { HashRouter } from 'react-router-dom'
import { ConfigProvider, theme as antdTheme, App as AntApp } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App.jsx'
import './styles.css'

/* ---------------- 主题：自动 / 浅色 / 暗色 三态 ---------------- */
const THEME_KEY = 'd2c-theme-mode'
// 构建版本标记：与 index.html 内联 bootstrap 写入的 data-build 必须一致。
// 若 HTML 与 JS 来自不同构建（旧缓存组合），运行时自动硬重载以清空陈旧组合。
const BUILD_ID = '20260826f'

export const ThemeContext = createContext({ mode: 'auto', setMode: () => {}, dark: false })
export const useThemeMode = () => useContext(ThemeContext)

function readSysDark() {
  try {
    return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
  } catch (e) {
    return false
  }
}
function readMode() {
  try {
    const v = localStorage.getItem(THEME_KEY)
    if (['auto', 'light', 'dark'].includes(v)) return v
  } catch (e) {}
  return 'auto'
}

// 单一写入点：data-theme、color-scheme、页面背景 全部由同一个 dark 驱动。
// 浏览器首帧由 index.html 内联 bootstrap 已写入一次（与下面逻辑一致），这里仅做幂等同步。
function applyThemeAttr(dark) {
  const root = document.documentElement
  root.setAttribute('data-theme', dark ? 'dark' : 'light')
  root.style.colorScheme = dark ? 'dark' : 'light'
  const bg = dark ? '#05070f' : '#f2f5fc'
  root.style.background = bg
  if (document.body) document.body.style.backgroundColor = bg
}

function ThemeProvider({ children }) {
  const [mode, setModeState] = useState(readMode)
  // 初值优先沿用 bootstrap 已写入的 data-theme（<head> 同步脚本先于 React 执行，
  // 其值即首帧正确值），再用 live matchMedia 兜底，彻底避免「首帧 React 与页面不一致」。
  const [sysDark, setSysDark] = useState(() => {
    const attr = document.documentElement.getAttribute('data-theme')
    if (attr === 'dark') return true
    if (attr === 'light') return false
    return readSysDark()
  })

  const dark = mode === 'auto' ? sysDark : mode === 'dark'

  // 同步写入 data-theme（CSS 与 antd 的同一事实来源）
  useLayoutEffect(() => {
    applyThemeAttr(dark)
  }, [dark])

  // 自愈守卫：若外部（浏览器扩展 / 注入脚本）把 data-theme 改成与 dark 不符，
  // 立即纠正回正确值，保证页面背景与 antd 组件始终同色、不被外部写入者覆盖。
  useEffect(() => {
    const root = document.documentElement
    const guard = new MutationObserver(() => {
      const want = dark ? 'dark' : 'light'
      if (root.getAttribute('data-theme') !== want) {
        applyThemeAttr(dark)
      }
    })
    guard.observe(root, { attributes: true, attributeFilter: ['data-theme'] })
    return () => guard.disconnect()
  }, [dark])

  // 跟随系统外观变化；并在挂载后用当前真实值校准一次，
  // 规避极少数浏览器在 <head> 解析早期 matchMedia 尚未就绪导致的初值偏差。
  useEffect(() => {
    if (!window.matchMedia) return undefined
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const fn = (e) => setSysDark(e.matches)
    mq.addEventListener('change', fn)
    setSysDark(mq.matches)
    return () => mq.removeEventListener('change', fn)
  }, [])

  // HTML 与 JS 构建版本不一致（陈旧缓存组合）→ 强制硬重载，直到版本匹配。
  // 防护：同一 BUILD_ID 最多重载一次，防止两处版本号忘记同步时陷入无限刷新。
  useEffect(() => {
    try {
      const htmlBuild = document.documentElement.getAttribute('data-build')
      if (htmlBuild && htmlBuild !== BUILD_ID) {
        const RK = 'd2c-build-reload'
        if (sessionStorage.getItem(RK) !== BUILD_ID) {
          sessionStorage.setItem(RK, BUILD_ID)
          window.location.reload(true)
        }
      }
    } catch (e) {}
  }, [])

  const setMode = (m) => {
    setModeState(m)
    try {
      localStorage.setItem(THEME_KEY, m)
    } catch (e) {}
  }

  const value = useMemo(() => ({ mode, setMode, dark }), [mode, dark])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

// 关键修复：antd 算法直接读取同一个 dark（即驱动 data-theme / CSS 的同一变量），
// 不再经过 MutationObserver 二次读取，从结构上杜绝「页面暗、组件亮」的分裂。
function Root() {
  const { dark } = useThemeMode()
  const token = {
    colorPrimary: '#2f7bff',
    colorInfo: '#2f7bff',
    colorSuccess: dark ? '#4ade80' : '#16a34a',
    colorWarning: dark ? '#fbbf24' : '#d97706',
    colorError: dark ? '#f87171' : '#dc2626',
    borderRadius: 10,
    colorBgContainer: dark ? 'rgba(13,21,40,0.72)' : 'rgba(255,255,255,0.86)',
    colorTextBase: dark ? '#e9effc' : '#17233d',
    fontFamily: '"HarmonyOS Sans SC", "PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    wireframe: false,
  }
  const components = {
    Layout: { headerBg: 'transparent', bodyBg: 'transparent', footerBg: 'transparent' },
    Table: {
      headerBg: 'transparent',
      rowHoverBg: dark ? 'rgba(59,130,246,0.08)' : 'rgba(37,99,235,0.06)',
    },
    Progress: { defaultColor: '#2f7bff' },
  }
  return (
    <ConfigProvider locale={zhCN} theme={{ algorithm: dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm, token, components }}>
      <AntApp>
        <App />
      </AntApp>
    </ConfigProvider>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ThemeProvider>
      <HashRouter>
        <Root />
      </HashRouter>
    </ThemeProvider>
  </React.StrictMode>,
)
