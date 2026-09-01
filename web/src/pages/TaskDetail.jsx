// 任务详情：进度步骤 + 轮询实时日志 + 结果（3D 查看器单模型 / 迭代历史 / 产物下载）
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Steps, Progress, Card, Row, Col, Tabs, Table, Button, Spin,
  Space, Typography, App as AntApp, Empty, Alert, Statistic, Tooltip,
} from 'antd'
import {
  DownloadOutlined, FileTextOutlined, ArrowLeftOutlined,
  CloudDownloadOutlined, HistoryOutlined, TrophyOutlined,
} from '@ant-design/icons'
import {
  getJob, artifactUrl, isTerminal, stageFromLog, progressFromJob,
  STAGE_STEPS, fmtTime, fmtSize,
} from '../api.js'
import ModelViewer from '../components/ModelViewer.jsx'
import { StatusTag } from './Home.jsx'
import { useThemeMode } from '../main.jsx'

const { Title, Text } = Typography

/* ---------------- 迭代历史表 ---------------- */
function HistoryTab({ history, bestRound }) {
  if (!history || !history.length) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无迭代记录" />
  }
  return (
    <Table
      className="task-table"
      rowKey="round"
      size="small"
      pagination={false}
      dataSource={history}
      columns={[
        {
          title: '轮次', dataIndex: 'round', width: 84,
          render: (v) => (
            <Space size={4}>
              <span className="task-id" style={{ fontSize: 12 }}>v{v}</span>
              {Number(v) === Number(bestRound) && (
                <Tooltip title="最优交付版本"><TrophyOutlined style={{ color: 'var(--warn)' }} /></Tooltip>
              )}
            </Space>
          ),
        },
        {
          title: '相似度得分', dataIndex: 'score', width: 110,
          render: (v) => (
            <Progress
              percent={Math.round((v || 0) * 100)}
              size="small"
              className="glow-progress"
              status={Number(v) === Number(bestRound) ? 'success' : 'normal'}
            />
          ),
        },
        {
          title: '遗留问题', dataIndex: 'issues',
          render: (v) => {
            const n = (v || []).length
            if (!n) return <Text type="secondary" style={{ fontSize: 12 }}>无</Text>
            return (
              <Space direction="vertical" size={0}>
                <Text style={{ fontSize: 12 }}>{n} 项</Text>
                {v.slice(0, 2).map((it, i) => (
                  <Text key={i} type="secondary" style={{ fontSize: 11.5 }}>
                    · {typeof it === 'string' ? it : (it.message || JSON.stringify(it))}
                  </Text>
                ))}
              </Space>
            )
          },
        },
      ]}
    />
  )
}

/* ---------------- G-code 预览 ---------------- */
function GcodeTab({ id, artifacts }) {
  const [codes, setCodes] = useState({})
  const ncArts = artifacts.filter((a) => a.kind === 'gcode_mill' || a.kind === 'gcode_turn')

  useEffect(() => {
    ncArts.forEach((a) => {
      fetch(a.url)
        .then((r) => (r.ok ? r.text() : null))
        .then((t) => t && setCodes((c) => ({ ...c, [a.kind]: t })))
        .catch(() => {})
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  if (!ncArts.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无 G-code 产物" />
  return (
    <Tabs
      size="small"
      items={ncArts.map((a) => ({
        key: a.kind,
        label: a.label,
        children: codes[a.kind] ? (
          <>
            <Space style={{ marginBottom: 10 }}>
              <Button size="small" icon={<DownloadOutlined />}
                      onClick={() => window.open(a.url)}>
                下载完整代码
              </Button>
              <Text type="secondary" style={{ fontSize: 12, fontFamily: 'var(--mono)' }}>
                {codes[a.kind].split('\n').length} 行 · 带中文注释
              </Text>
            </Space>
            <pre className="gcode-pre">{codes[a.kind].split('\n').slice(0, 200).join('\n')}</pre>
            {codes[a.kind].split('\n').length > 200 && (
              <Text type="secondary" style={{ fontSize: 12 }}>… 预览前 200 行，完整内容请下载</Text>
            )}
          </>
        ) : <Spin style={{ padding: 30 }} />,
      }))}
    />
  )
}

/* ---------------- 产物下载网格 ---------------- */
function ArtifactGrid({ artifacts }) {
  return (
    <div className="export-grid">
      {artifacts.map((a) => (
        <Tooltip key={a.kind} title={`${a.file || a.kind}${a.size ? ' · ' + fmtSize(a.size) : ''}`}>
          <button type="button" className="export-item" onClick={() => window.open(a.url)}>
            <span className="exp-ico">{(a.kind || '').replace(/^gcode_/, '').replace('proj_', '').slice(0, 4).toUpperCase()}</span>
            <span>
              <div className="exp-name">{a.label}</div>
              <div className="exp-sub">{a.file || a.kind}{a.size ? ` · ${fmtSize(a.size)}` : ''}</div>
            </span>
          </button>
        </Tooltip>
      ))}
    </div>
  )
}

/* ---------------- 主页面 ---------------- */
export default function TaskDetail() {
  const { id } = useParams()
  const nav = useNavigate()
  const { message, modal } = AntApp.useApp()
  const { dark } = useThemeMode()
  const [task, setTask] = useState(null)
  const [notFound, setNotFound] = useState(false)
  const logBoxRef = useRef(null)

  const load = useCallback(async () => {
    try {
      const t = await getJob(id)
      setTask(t)
      return t
    } catch (e) {
      if (String(e.message).includes('404')) setNotFound(true)
      return null
    }
  }, [id])

  // 初次加载
  useEffect(() => {
    load()
  }, [load])

  // 非终态轮询（后端无 SSE，日志随状态接口返回）
  useEffect(() => {
    if (!task || isTerminal(task.status)) return undefined
    const timer = setInterval(load, 2500)
    return () => clearInterval(timer)
  }, [task, load])

  // 日志自动滚动（仅在接近底部时）
  const logs = task ? task.log : []
  useEffect(() => {
    const box = logBoxRef.current
    if (!box) return
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80
    if (nearBottom) box.scrollTop = box.scrollHeight
  }, [logs.length])

  if (notFound) {
    return (
      <div className="glass panel fade-up" style={{ textAlign: 'center', padding: '70px 20px' }}>
        <Empty description="任务不存在或已被删除" />
        <Button style={{ marginTop: 16 }} icon={<ArrowLeftOutlined />} onClick={() => nav('/')}>
          返回任务中心
        </Button>
      </div>
    )
  }
  if (!task) {
    return (
      <div style={{ padding: '90px 0', textAlign: 'center' }}>
        <Spin size="large" tip="加载任务数据…" />
      </div>
    )
  }

  const done = task.status === 'done'
  const failed = task.status === 'failed'
  const running = !isTerminal(task.status)
  const stageIdx = done ? STAGE_STEPS.length : Math.max(0, stageFromLog(task.log || []))
  const percent = progressFromJob(task)
  const glbUrl = done ? artifactUrl(id, 'glb') : null
  const reportArt = task.artifacts.find((a) => a.kind === 'report')
  const modelArts = task.artifacts.filter((a) => a.kind !== 'report')

  return (
    <div className="task-page">
      {/* ---- 头部信息 ---- */}
      <Card className="glass fade-up">
        <Row gutter={16} align="middle" wrap>
          <Col flex="auto">
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <div className="task-head-title">
                <Button type="text" size="small" icon={<ArrowLeftOutlined />}
                        onClick={() => nav('/')} aria-label="返回任务中心" />
                <Title level={4} style={{ margin: 0 }}>{task.source_name || '未命名图纸'}</Title>
                <StatusTag status={task.status} />
              </div>
              <div className="task-meta">
                <Text type="secondary" style={{ fontSize: 12 }}>{task.id}</Text>
                &nbsp;·&nbsp; MODE <b>{(task.mode || 'pipeline').toUpperCase()}</b>
                &nbsp;·&nbsp; 创建于 <b>{fmtTime(task.created_at)}</b>
                {done && task.best_round != null && (
                  <span> &nbsp;·&nbsp; 最优版本 <b>v{task.best_round}</b>（相似度 {task.best_score}）</span>
                )}
                {task.result && task.result.elapsed != null && (
                  <span> &nbsp;·&nbsp; 耗时 <b>{Math.round(task.result.elapsed)}s</b></span>
                )}
              </div>
            </Space>
          </Col>
          <Col>
            <Space>
              {done && reportArt && (
                <Button icon={<FileTextOutlined />} type="primary"
                        onClick={() => window.open(reportArt.url, '_blank')}>
                  制造报告
                </Button>
              )}
            </Space>
          </Col>
        </Row>
        <div style={{ marginTop: 20 }}>
          <Steps
            className="tech-steps"
            size="small"
            current={stageIdx}
            status={failed ? 'error' : undefined}
            items={STAGE_STEPS.map((s) => ({ title: s.label }))}
          />
          <Progress
            className="glow-progress"
            percent={percent}
            status={failed ? 'exception' : done ? 'success' : 'active'}
            style={{ marginTop: 14 }}
          />
        </div>
      </Card>

      {failed && task.error && (
        <Alert type="error" showIcon style={{ marginTop: 14, borderRadius: 12 }}
               message="任务失败" description={task.error} />
      )}

      <Row gutter={14} style={{ marginTop: 14 }}>
        {/* ---- 左：3D 查看器 / 实时日志 ---- */}
        <Col xs={24} lg={13}>
          {done && glbUrl && (
            <Card className="glass fade-up fade-up-1" size="small"
                  title={<span className="sec-title" style={{ fontSize: 14 }}>3D 模型查看器</span>}>
              <ModelViewer url={glbUrl} dark={dark} />
            </Card>
          )}
          <Card
            className="glass fade-up fade-up-2" size="small" style={{ marginTop: 14 }}
            title={(
              <div className="log-head-bar">
                <span className="bullets"><i /><i /><i /></span>
                <HistoryOutlined style={{ color: 'var(--accent-2)' }} />
                PIPELINE LIVE LOG
              </div>
            )}
          >
            <div className="log-box" ref={logBoxRef}>
              {logs.length === 0 && (
                <div className="log-line">
                  <span className="log-ts">--</span>
                  <span className="log-msg" style={{ color: '#5b7bb2' }}>
                    {running ? '等待流水线日志…' : '任务已结束，无日志留存'}
                  </span>
                </div>
              )}
              {logs.map((l, i) => {
                // 兼容两种格式: 新 {ts, msg} 带秒级时间戳; 旧纯字符串
                // 只显示时间不显示日期 (后端存 "YYYY-MM-DD HH:MM:SS", 截取后半段)
                const rawTs = (l && typeof l === 'object') ? (l.ts || '') : ''
                const ts = rawTs.includes(' ') ? rawTs.split(' ').pop() : rawTs
                const msg = typeof l === 'string' ? l : ((l && l.msg) ?? JSON.stringify(l))
                return (
                  <div key={i} className="log-line log-in">
                    <span className="log-ts">{ts || String(i + 1).padStart(3, '0')}</span>
                    <span className="log-msg">{msg}</span>
                  </div>
                )
              })}
            </div>
          </Card>
        </Col>

        {/* ---- 右：结果 Tabs / 产物 ---- */}
        <Col xs={24} lg={11}>
          {done ? (
            <>
              <Card className="glass fade-up fade-up-1" size="small">
                <Tabs
                  size="small"
                  items={[
                    {
                      key: 'history', label: '迭代历史',
                      children: <HistoryTab history={task.history} bestRound={task.best_round} />,
                    },
                    {
                      key: 'gcode', label: 'G-code',
                      children: <GcodeTab id={id} artifacts={task.artifacts} />,
                    },
                  ]}
                />
              </Card>
              <Card className="glass fade-up fade-up-2" size="small"
                    title={<span className="sec-title" style={{ fontSize: 14 }}>交付产物</span>}
                    style={{ marginTop: 14 }}
              >
                <ArtifactGrid artifacts={task.artifacts} />
                <Text type="secondary" style={{ display: 'block', marginTop: 10, fontSize: 12 }}>
                  全部产物可通过本服务端口直接下载；三视图投影（亮/暗）已生成，可在制造报告中查看。
                </Text>
              </Card>
            </>
          ) : (
            <Card className="glass fade-up fade-up-1" size="small"
                  title={<span className="sec-title" style={{ fontSize: 14 }}>
                    {task.mode === 'evolve' ? 'EVOLVE 闭环说明' : 'PIPELINE 闭环说明'}
                  </span>}>
              <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.9 }}>
                {task.mode === 'evolve' ? (
                  <>
                    <p><b>图纸解析</b>：VLM 读取工程图，提取视图、尺寸与截面数据。</p>
                    <p><b>LLM 生成建模代码</b>：LLM 依据图纸自主编写参数化建模脚本。</p>
                    <p><b>沙箱执行 · 建模</b>：代码在受控沙箱中执行，生成 3D 实体并渲染三视图。</p>
                    <p><b>视觉比对 · 迭代</b>：VLM 比对渲染图与图纸，输出差异清单驱动下一轮重写；拓扑差异自动升级重写代码补特征。</p>
                    <p><b>自测 · 交付</b>：交付前模型自测（体积/拓扑校验），异常自愈；生成 STEP/STL/OBJ/GLB/DXF、G-Code 与制造报告。</p>
                  </>
                ) : (
                  <>
                    <p><b>图纸解析</b>：识别零件族（叶片/轴/滚柱/丝杠/体壳）。</p>
                    <p><b>参数收敛</b>：按零件族模板提取参数并迭代修正。</p>
                    <p><b>交付</b>：全格式产物 + 制造报告。</p>
                  </>
                )}
              </div>
            </Card>
          )}
        </Col>
      </Row>
    </div>
  )
}
