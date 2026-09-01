// 首页：Hero + 服务能力描述 + 拖拽上传 + 统计卡片 + 最近任务表
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Upload, Table, Tag, Progress, Typography, App as AntApp, Space, Button, Empty,
  Popconfirm, Segmented,
} from 'antd'
import {
  InboxOutlined, EyeOutlined, ReloadOutlined, FileImageOutlined,
  HighlightOutlined, FontSizeOutlined, BulbOutlined, BuildOutlined,
  AuditOutlined, ExportOutlined, CheckCircleOutlined, SyncOutlined,
  ClockCircleOutlined, DatabaseOutlined, ApiOutlined, FileDoneOutlined,
  RobotOutlined, ToolOutlined, ClusterOutlined, ThunderboltOutlined,
  DeleteOutlined,
} from '@ant-design/icons'
import { listJobs, submitJob, deleteJob, deleteJobsBatch, fmtTime, isTerminal } from '../api.js'

const { Title, Paragraph, Text } = Typography

/* ---------------- 状态徽标（呼吸灯） ---------------- */
const STATUS_META = {
  pending: { cls: 's-pending', text: '排队中' },
  running: { cls: 's-running', text: '处理中' },
  done: { cls: 's-done', text: '完成' },
  failed: { cls: 's-failed', text: '失败' },
  cancelled: { cls: 's-cancelled', text: '已取消' },
}

export function StatusTag({ status }) {
  const m = STATUS_META[status] || { cls: 's-pending', text: status }
  return (
    <span className={`status-pill ${m.cls}`}>
      <span className="sdot" />
      {m.text}
    </span>
  )
}

/* ---------------- 流水线阶段链 ---------------- */
const PIPELINE = [
  { icon: <FileImageOutlined />, label: '图纸解析' },
  { icon: <FontSizeOutlined />, label: '数据提取 · OCR' },
  { icon: <BulbOutlined />, label: 'VLM 语义理解' },
  { icon: <BuildOutlined />, label: 'LLM 代码建模' },
  { icon: <AuditOutlined />, label: '视觉比对迭代' },
  { icon: <ExportOutlined />, label: '多格式交付 · G-code' },
]

/* ---------------- 服务能力 ---------------- */
const CAPABILITIES = [
  {
    icon: <RobotOutlined />,
    title: 'AI 自主建模',
    desc: 'LLM 阅读工程图并自主编写参数化建模代码，沙箱执行生成 3D 实体；拓扑不符时自动重写代码补全特征，无需人工预置零件族。',
  },
  {
    icon: <SyncOutlined />,
    title: '视觉比对闭环',
    desc: '每轮模型渲染后由 VLM 与图纸逐项比对，输出差异清单驱动下一轮修正，自动择优交付；交付前模型自测，异常自愈。',
  },
  {
    icon: <ExportOutlined />,
    title: '全格式产物',
    desc: '一次重建产出 STEP / STL / OBJ / GLB / DXF 三视图、三视图投影（亮暗版）与带中文注释的 CNC G-Code。',
  },
  {
    icon: <FileDoneOutlined />,
    title: '单文件制造报告',
    desc: '自动生成 HTML 制造报告：图纸数据、截面坐标、设备与工装、刀具切削参数、质量检验要求、迭代历史与 G-code 预览。',
  },
  {
    icon: <ApiOutlined />,
    title: 'REST + MCP 双协议',
    desc: '同一端口提供 REST API 与 MCP SSE 服务，可被 StaffDeck 等 MCP 客户端直接调用，也支持 curl / SDK 集成。',
  },
  {
    icon: <ClusterOutlined />,
    title: '零件族覆盖',
    desc: '已支持叶片（截面放样）、轴、滚柱、行星滚柱丝杠、体壳等零件族；evolve 模式对任意图纸均可尝试重建。',
  },
]

/* ---------------- 统计卡片 ---------------- */
function StatCards({ rows, llm }) {
  const stats = useMemo(() => {
    const total = rows.length
    const done = rows.filter((r) => r.status === 'done').length
    const active = rows.filter((r) => !isTerminal(r.status)).length
    const rate = total ? Math.round((done / total) * 100) : 0
    return { total, done, active, rate }
  }, [rows])

  const llmText = llm ? (llm.mode === 'online' || llm.online ? '在线' : '离线') : '-'
  const items = [
    { icon: <DatabaseOutlined />, label: '任务总数', value: stats.total, note: '全部重建任务' },
    { icon: <CheckCircleOutlined />, label: '已完成', value: stats.done, note: '通过自测交付' },
    { icon: <SyncOutlined />, label: '进行中', value: stats.active, note: '闭环实时运行' },
    { icon: <ThunderboltOutlined />, label: 'LLM 状态', value: llmText, note: llm ? `VLM ${llm.vl_model || llm.model || ''}` : '视觉语言模型' },
  ]
  return (
    <div className="stat-row fade-up fade-up-2">
      {items.map((s) => (
        <div className="glass stat-card" key={s.label}>
          <span className="stat-label">{s.icon}{s.label}</span>
          <span className="stat-value">{s.value}</span>
          <span className="stat-note">{s.note}</span>
        </div>
      ))}
    </div>
  )
}

const EXT_CHIPS = ['PDF']

export default function Home() {
  const nav = useNavigate()
  const { message } = AntApp.useApp()
  const [rows, setRows] = useState([])
  const [llm, setLlm] = useState(null)
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [mode, setMode] = useState('evolve')
  const [selectedKeys, setSelectedKeys] = useState([])
  const [deleting, setDeleting] = useState(false)
  const timerRef = useRef(null)

  const customRequest = async ({ file, onSuccess, onError }) => {
    setUploading(true)
    try {
      const d = await submitJob(file, { mode, maxRounds: mode === 'evolve' ? 6 : 4 })
      message.success('已提交，进入重建闭环')
      onSuccess(d)
      nav(`/tasks/${d.job_id}`)
    } catch (e) {
      message.error(`提交失败：${e.message}`)
      onError(e)
    } finally {
      setUploading(false)
    }
  }

  // 静默轮询刷新后清理已不存在的勾选项，避免残留幽灵勾选
  const refresh = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const [jobsRows, status] = await Promise.all([listJobs(), fetch('/api/status').then((r) => r.json()).catch(() => null)])
      setRows(jobsRows)
      if (status && status.llm) setLlm(status.llm)
      const alive = new Set(jobsRows.map((r) => r.id))
      setSelectedKeys((prev) => prev.filter((k) => alive.has(k)))
    } catch (e) {
      if (!silent) message.error(`加载任务失败：${e.message}`)
    } finally {
      if (!silent) setLoading(false)
    }
  }, [message])

  useEffect(() => {
    refresh()
  }, [refresh])

  // 有非终态任务时每 3 秒静默刷新
  useEffect(() => {
    const hasActive = rows.some((r) => !isTerminal(r.status))
    if (!hasActive) return undefined
    timerRef.current = setInterval(() => refresh(true), 3000)
    return () => clearInterval(timerRef.current)
  }, [rows, refresh])

  // 删除单个任务 (气泡确认后调用)
  const doDeleteOne = async (id) => {
    try {
      await deleteJob(id)
      message.success(`已删除 ${id}`)
      setSelectedKeys((prev) => prev.filter((k) => k !== id))
      refresh(true)
    } catch (e) {
      message.error(`删除失败：${e.message}`)
    }
  }

  // 批量删除当前勾选项
  const doDeleteBatch = async () => {
    if (!selectedKeys.length) return
    setDeleting(true)
    try {
      const res = await deleteJobsBatch(selectedKeys)
      const n = (res.deleted || []).length
      const skipped = res.skipped || {}
      if (n) message.success(`已删除 ${n} 个任务`)
      if (Object.keys(skipped).length) {
        message.warning(`${Object.keys(skipped).length} 个任务跳过：${Object.values(skipped).join('；')}`)
      }
      setSelectedKeys([])
      refresh(true)
    } catch (e) {
      message.error(`批量删除失败：${e.message}`)
    } finally {
      setDeleting(false)
    }
  }

  const columns = [
    {
      title: '任务', key: 'task', width: 240,
      render: (_, r) => (
        <Space direction="vertical" size={1}>
          <span className="src-file task-name" title={r.source_name || r.id}>
            {r.source_name || '未命名图纸'}
          </span>
          <span className="task-id">{r.mode === 'evolve' ? 'EVOLVE 自主建模' : 'PIPELINE 参数收敛'}</span>
        </Space>
      ),
    },
    {
      title: '模式', dataIndex: 'mode', key: 'mode', width: 96,
      responsive: ['md', 'lg', 'xl', 'xxl'],
      render: (v) => (
        <Tag className="ext-tag" bordered>
          {(v || 'pipeline').toUpperCase()}
        </Tag>
      ),
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 96,
      render: (v) => <StatusTag status={v} />,
    },
    {
      title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 150,
      responsive: ['md', 'lg', 'xl', 'xxl'],
      render: (v) => <span className="task-id">{fmtTime(v)}</span>,
    },
    {
      title: '操作', key: 'op', width: 100,
      render: (_, r) => (
        <Space size={0}>
          <Button
            type="link"
            size="small"
            title="详情"
            icon={<EyeOutlined />}
            onClick={(e) => { e.stopPropagation(); nav(`/tasks/${r.id}`) }}
          />
          <Popconfirm
            title="删除任务"
            description={`确认删除 ${r.id}？任务目录与全部产物将一并移除，不可恢复。`}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={(e) => { if (e) e.stopPropagation(); doDeleteOne(r.id) }}
            onCancel={(e) => { if (e) e.stopPropagation() }}
          >
            <Button
              type="link"
              size="small"
              danger
              title="删除"
              icon={<DeleteOutlined />}
              onClick={(e) => e.stopPropagation()}
            />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div className="home">
      {/* ---------- Hero ---------- */}
      <section className="hero glass fade-up">
        <div className="hero-eyebrow">
          <span className="dot" />
          Intelligent Engineering Reconstruction Engine
        </div>
        <Title level={2} className="hero-title">工程图纸 → 3D 模型 · G-Code · 制造报告</Title>
        <Paragraph className="hero-desc">
          上传机械零件工程图 PDF，系统自动完成图纸解析、数据提取、VLM 语义理解，
          由 LLM 自主编写参数化建模代码并在沙箱中执行，经多轮视觉比对迭代收敛后，
          交付 STEP / STL / OBJ / GLB / DXF 全格式模型、带中文注释的 CNC G-Code
          与单文件制造报告。
        </Paragraph>

        <div className="pipeline-chain">
          {PIPELINE.map((p, i) => (
            <React.Fragment key={p.label}>
              <span className="pipe-node">{p.icon}{p.label}</span>
              {i < PIPELINE.length - 1 && <span className="pipe-arrow">▶</span>}
            </React.Fragment>
          ))}
        </div>

        <Upload.Dragger
          name="pdf"
          multiple={false}
          showUploadList={false}
          accept=".pdf"
          customRequest={customRequest}
          disabled={uploading}
          className="upload-dragger"
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">
            {uploading ? '正在上传，即将进入重建闭环…' : '点击或拖拽工程图 PDF 到此处'}
          </p>
          <p className="ant-upload-hint">
            提交后自动跳转任务详情，实时跟踪建模 · 比对 · 迭代全过程
          </p>
          <div className="upload-ext-chips">
            {EXT_CHIPS.map((e) => <span className="ext-chip" key={e}>{e}</span>)}
          </div>
          <div style={{ marginTop: 14 }}>
            <Segmented
              size="small"
              value={mode}
              onChange={setMode}
              onClick={(e) => e.stopPropagation()}
              options={[
                { label: 'EVOLVE · LLM 自主建模（任意图纸）', value: 'evolve' },
                { label: 'PIPELINE · 零件族参数收敛', value: 'pipeline' },
              ]}
            />
          </div>
        </Upload.Dragger>
      </section>

      {/* ---------- 服务能力 ---------- */}
      <section className="glass panel fade-up fade-up-1" style={{ marginBottom: 20 }}>
        <div className="panel-head">
          <Title level={4} className="sec-title">服务能力</Title>
          <Space>
            <ToolOutlined style={{ color: 'var(--accent-2)' }} />
            <Text type="secondary" style={{ fontSize: 12 }}>Drawing2Model Agent · v0.2</Text>
          </Space>
        </div>
        <div className="cap-grid">
          {CAPABILITIES.map((c) => (
            <div className="glass hoverable cap-card" key={c.title}>
              <span className="cap-ico">{c.icon}</span>
              <div>
                <div className="cap-title">{c.title}</div>
                <div className="cap-desc">{c.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ---------- 统计 ---------- */}
      <StatCards rows={rows} llm={llm} />

      {/* ---------- 最近任务 ---------- */}
      <section className="glass panel fade-up fade-up-3">
        <div className="panel-head">
          <Title level={4} className="sec-title">最近任务</Title>
          <Space>
            {selectedKeys.length > 0 && (
              <Popconfirm
                title="批量删除任务"
                description={`确认删除已勾选的 ${selectedKeys.length} 个任务？任务目录与全部产物将一并移除，不可恢复。`}
                okText={`删除 ${selectedKeys.length} 项`}
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={doDeleteBatch}
              >
                <Button danger icon={<DeleteOutlined />} loading={deleting}>
                  批量删除（{selectedKeys.length}）
                </Button>
              </Popconfirm>
            )}
            <Button icon={<ReloadOutlined spin={loading} />} onClick={() => refresh()} title="刷新" />
          </Space>
        </div>
        <Table
          className="task-table"
          rowKey="id"
          size="middle"
          scroll={{ x: 'max-content' }}
          columns={columns}
          dataSource={rows}
          rowSelection={{
            selectedRowKeys: selectedKeys,
            onChange: setSelectedKeys,
            columnWidth: 44,
          }}
          loading={loading && rows.length === 0}
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={<Text type="secondary">暂无任务，上传一张工程图开始第一次重建</Text>}
              />
            ),
          }}
          onRow={(r) => ({
            onClick: (e) => {
              // 点击勾选框/操作列气泡时不触发跳转
              if (e.target.closest('.ant-table-selection-column')) return
              nav(`/tasks/${r.id}`)
            },
            style: { cursor: 'pointer' },
          })}
        />
      </section>
    </div>
  )
}
