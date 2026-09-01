// 应用骨架：背景光效层 + 玻璃头部导航 + 路由 + 页脚
import React from 'react'
import { Routes, Route, Link, useLocation } from 'react-router-dom'
import { Layout, Button, Tooltip, Space } from 'antd'
import { DesktopOutlined, SunOutlined, MoonOutlined, AppstoreOutlined, ReadOutlined } from '@ant-design/icons'
import { useThemeMode } from './main.jsx'
import Home from './pages/Home.jsx'
import TaskDetail from './pages/TaskDetail.jsx'
import ApiDocs from './pages/ApiDocs.jsx'
import BackToTop from './components/BackToTop.jsx'

const { Header, Content, Footer } = Layout

const THEME_META = {
  auto: { icon: <DesktopOutlined />, label: '自动（跟随系统）', next: 'light' },
  light: { icon: <SunOutlined />, label: '浅色', next: 'dark' },
  dark: { icon: <MoonOutlined />, label: '暗色', next: 'auto' },
}

function ThemeToggle() {
  const { mode, setMode } = useThemeMode()
  const meta = THEME_META[mode]
  return (
    <Tooltip title={`主题：${meta.label}（点击切换）`}>
      <Button
        type="text"
        shape="circle"
        icon={meta.icon}
        onClick={() => setMode(meta.next)}
        aria-label="主题切换"
      />
    </Tooltip>
  )
}

export default function App() {
  const location = useLocation()
  return (
    <>
      <Layout className="app-layout">
      {/* 全局背景光效 / 几何网格 */}
      <div className="bg-fx" aria-hidden="true" />

      <Header className="app-header">
        <Link to="/" className="brand">
          <img src="/logo.png" alt="Drawing2Model" className="brand-logo" />
          <span className="brand-text">
            Drawing2Model
            <span className="brand-sub">DRAWING → 3D MODEL · CNC · REPORT</span>
          </span>
        </Link>
        <Space size={8}>
          <span className="nav-link">
            <Link to="/">
              <Button
                type={location.pathname === '/' ? 'primary' : 'text'}
                icon={<AppstoreOutlined />}
              >
                任务中心
              </Button>
            </Link>
          </span>
          <span className="nav-link">
            <Link to="/docs">
              <Button
                type={location.pathname === '/docs' ? 'primary' : 'text'}
                icon={<ReadOutlined />}
              >
                API 文档
              </Button>
            </Link>
          </span>
          <ThemeToggle />
        </Space>
      </Header>

      <Content className="app-content">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/tasks/:id" element={<TaskDetail />} />
          <Route path="/docs" element={<ApiDocs />} />
        </Routes>
      </Content>

      <Footer className="app-footer">
        Drawing2Model
        <span className="footer-dot">·</span>
        单端口 REST API + MCP + Web-UI
        <span className="footer-dot">·</span>
        重建结果仅供工程参考，首件加工前请在 CAM 仿真环境中验证刀路
      </Footer>

      <BackToTop />
      </Layout>
    </>
  )
}
