// API 文档页：REST 端点 + MCP 工具说明 + 产物 kind 一览 + 集成示例
import React, { useEffect, useState } from 'react'
import { Card, Row, Col, Typography, Table, Tag, Space, Alert, Tabs } from 'antd'
import {
  ApiOutlined, RobotOutlined, ThunderboltOutlined, LinkOutlined,
  FileTextOutlined, CodeOutlined,
} from '@ant-design/icons'

const { Title, Text, Paragraph } = Typography

const MONO = { fontFamily: 'var(--mono)', fontSize: 12.5 }

function CodeBlock({ children, lang = 'bash' }) {
  return (
    <pre className="gcode-pre" style={{ maxHeight: 'none', margin: '8px 0' }}>
      <span style={{ color: '#5b7bb2' }}># {lang}</span>{'\n'}
      {children}
    </pre>
  )
}

/* ---------------- REST ---------------- */
const REST_ROWS = [
  {
    method: 'POST', path: '/api/jobs',
    desc: '提交重建任务',
    params: 'multipart: pdf(文件) / mode: pipeline|evolve / max_rounds: 1-5',
    ret: '{job_id, poll}',
  },
  {
    method: 'GET', path: '/api/jobs/{job_id}',
    desc: '查询任务状态、日志、迭代历史、产物清单',
    params: '—',
    ret: '{id, status, log[], result, manifest{...}}',
  },
  {
    method: 'GET', path: '/api/jobs/{job_id}/artifacts/{kind}',
    desc: '下载指定产物（本端口直接访问）',
    params: 'kind 见下表',
    ret: '文件流',
  },
  {
    method: 'GET', path: '/api/status',
    desc: '服务状态：LLM 在线/模型、案例库、任务状态表',
    params: '—',
    ret: '{llm, config, cases, jobs}',
  },
  {
    method: 'GET', path: '/mcp/sse',
    desc: 'MCP SSE 连接端点（同端口挂载）',
    params: '—',
    ret: 'SSE 事件流',
  },
]

const KIND_ROWS = [
  { kind: 'glb', file: 'model.glb', desc: 'Web 3D 展示模型（GLTF 二进制）' },
  { kind: 'stl', file: 'model.stl', desc: '三角网格模型（best 版本）' },
  { kind: 'step', file: 'model.step', desc: 'STEP 精确几何（缝合导出）' },
  { kind: 'obj', file: 'model.obj', desc: 'OBJ 通用网格' },
  { kind: 'dxf', file: 'model.dxf', desc: 'DXF 三视图投影轮廓' },
  { kind: 'gcode_mill', file: 'model_mill.nc', desc: '铣削 G-Code（FANUC 风格，带中文注释）' },
  { kind: 'gcode_turn', file: 'model_turn.nc', desc: '车削 G-Code（回转体，G71/G70 循环）' },
  { kind: 'report', file: 'report.html', desc: '单文件制造报告（含设备工装/质量检验/图纸数据）' },
  { kind: 'manifest', file: 'artifacts_manifest.json', desc: '产物清单元数据' },
  { kind: 'spec', file: 'spec.json', desc: '重建参数规格（best 版本）' },
]

const MCP_TOOLS = [
  {
    name: 'drawing2model',
    desc: '提交工程图 PDF → 3D 模型重建任务（可同步等待完成）',
    args: 'pdf_base64 / pdf_path（二选一）、use_template、max_rounds、mode(pipeline|evolve)、wait、timeout',
  },
  { name: 'd2m_status', desc: '查询任务状态 / 日志 / 迭代历史', args: 'job_id' },
  { name: 'd2m_artifacts', desc: '获取已完成任务的产物绝对路径（glb/stl/spec/report/render）', args: 'job_id' },
  { name: 'd2m_health', desc: '服务健康检查：LLM 在线状态 / 模型 / 案例库数量', args: '—' },
]

export default function ApiDocs() {
  const [host, setHost] = useState('')
  useEffect(() => {
    setHost(window.location.origin || 'http://127.0.0.1:8410')
  }, [])

  return (
    <div className="docs-page">
      <section className="glass panel fade-up" style={{ marginBottom: 20 }}>
        <div className="panel-head">
          <Title level={4} className="sec-title"><ApiOutlined /> API 文档 · Drawing2Model</Title>
          <Text type="secondary" style={MONO}>{host}</Text>
        </div>
        <Paragraph type="secondary" style={{ marginBottom: 8 }}>
          Drawing2Model 在单一端口（默认 <Text code>8410</Text>）同时提供
          <Text strong> REST API</Text>、<Text strong>MCP SSE</Text> 与 <Text strong>Web UI</Text>。
          所有产物均可通过本端口直接访问与下载；任务状态采用轮询模型（日志随状态接口一并返回）。
        </Paragraph>
        <Alert
          type="info" showIcon
          message="MCP SSE 端点已挂载在同端口 /mcp 路径下（/mcp/sse），也可单独运行 mcp_server.py 使用 8411 端口。"
          style={{ borderRadius: 10 }}
        />
      </section>

      <Tabs
        size="large"
        items={[
          {
            key: 'rest',
            label: <span><LinkOutlined /> REST API</span>,
            children: (
              <Row gutter={14}>
                <Col span={24}>
                  <Card className="glass" size="small" style={{ marginBottom: 14 }}
                        title={<span className="sec-title" style={{ fontSize: 14 }}>端点一览</span>}>
                    <Table
                      className="task-table"
                      rowKey="path" size="small" pagination={false}
                      scroll={{ x: 'max-content' }}
                      dataSource={REST_ROWS}
                      columns={[
                        { title: '方法', dataIndex: 'method', width: 80, render: (v) => <Tag color={v === 'GET' ? 'blue' : 'green'}>{v}</Tag> },
                        { title: '路径', dataIndex: 'path', width: 260, render: (v) => <Text style={MONO}>{v}</Text> },
                        { title: '说明', dataIndex: 'desc', width: 240 },
                        { title: '参数', dataIndex: 'params', width: 260, render: (v) => <Text type="secondary" style={{ fontSize: 12 }}>{v}</Text> },
                        { title: '返回', dataIndex: 'ret', render: (v) => <Text style={{ ...MONO, fontSize: 11.5 }}>{v}</Text> },
                      ]}
                    />
                  </Card>
                  <Card className="glass" size="small" style={{ marginBottom: 14 }}
                        title={<span className="sec-title" style={{ fontSize: 14 }}>产物 kind 一览</span>}>
                    <Table
                      className="task-table"
                      rowKey="kind" size="small" pagination={false}
                      scroll={{ x: 'max-content' }}
                      dataSource={KIND_ROWS}
                      columns={[
                        { title: 'kind', dataIndex: 'kind', width: 120, render: (v) => <Tag>{v}</Tag> },
                        { title: '文件', dataIndex: 'file', width: 200, render: (v) => <Text style={MONO}>{v}</Text> },
                        { title: '说明', dataIndex: 'desc' },
                      ]}
                    />
                  </Card>
                  <Card className="glass" size="small"
                        title={<span className="sec-title" style={{ fontSize: 14 }}><CodeOutlined /> curl 集成示例</span>}>
                    <CodeBlock lang="bash — 提交 evolve 任务">
{`curl -X POST "${host}/api/jobs" \\
  -F "pdf=@drawing.pdf" \\
  -F "mode=evolve" \\
  -F "max_rounds=6"`}
                    </CodeBlock>
                    <CodeBlock lang="bash — 查询状态（轮询）">
{`curl "${host}/api/jobs/job_xxxxxxxx"`}
                    </CodeBlock>
                    <CodeBlock lang="bash — 下载产物">
{`curl -OJ "${host}/api/jobs/job_xxxxxxxx/artifacts/step"
curl -OJ "${host}/api/jobs/job_xxxxxxxx/artifacts/report"`}
                    </CodeBlock>
                  </Card>
                </Col>
              </Row>
            ),
          },
          {
            key: 'mcp',
            label: <span><RobotOutlined /> MCP 使用说明</span>,
            children: (
              <Row gutter={14}>
                <Col span={24}>
                  <Card className="glass" size="small" style={{ marginBottom: 14 }}
                        title={<span className="sec-title" style={{ fontSize: 14 }}>接入配置</span>}>
                    <Paragraph type="secondary" style={{ marginBottom: 6 }}>
                      MCP 客户端（如 StaffDeck、WorkBuddy）在 <Text code>~/.workbuddy/mcp.json</Text> 中添加：
                    </Paragraph>
                    <CodeBlock lang="json — ~/.workbuddy/mcp.json">
{`{
  "mcpServers": {
    "drawing2model": {
      "url": "${host}/mcp/sse"
    }
  }
}`}
                    </CodeBlock>
                    <Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
                      独立部署时也可运行 <Text code>python mcp_server.py</Text>（默认 8411），
                      此时 URL 为 <Text code>http://&lt;host&gt;:8411/sse</Text>。
                    </Paragraph>
                  </Card>
                  <Card className="glass" size="small" style={{ marginBottom: 14 }}
                        title={<span className="sec-title" style={{ fontSize: 14 }}>工具清单</span>}>
                    <Table
                      className="task-table"
                      rowKey="name" size="small" pagination={false}
                      scroll={{ x: 'max-content' }}
                      dataSource={MCP_TOOLS}
                      columns={[
                        { title: '工具', dataIndex: 'name', width: 150, render: (v) => <Text style={MONO}>{v}</Text> },
                        { title: '说明', dataIndex: 'desc', width: 330 },
                        { title: '参数', dataIndex: 'args', render: (v) => <Text type="secondary" style={{ fontSize: 12 }}>{v}</Text> },
                      ]}
                    />
                  </Card>
                  <Card className="glass" size="small"
                        title={<span className="sec-title" style={{ fontSize: 14 }}><ThunderboltOutlined /> 典型调用流程</span>}>
                    <Paragraph style={{ marginBottom: 4 }}>
                      <Text strong>1.</Text> <Text code>drawing2model(pdf_path=..., mode="evolve", wait=true)</Text> — 提交并同步等待（默认最长 900s），返回 JSON 含状态、迭代历史、产物绝对路径与收敛结论；
                    </Paragraph>
                    <Paragraph style={{ marginBottom: 4 }}>
                      <Text strong>2.</Text> 若 <Text code>wait=false</Text>，用 <Text code>d2m_status(job_id)</Text> 轮询，直至 <Text code>status=done</Text>；
                    </Paragraph>
                    <Paragraph style={{ marginBottom: 0 }}>
                      <Text strong>3.</Text> <Text code>d2m_artifacts(job_id)</Text> 获取产物绝对路径，直接读取文件或经 REST 端口下载。
                    </Paragraph>
                  </Card>
                </Col>
              </Row>
            ),
          },
        ]}
      />
    </div>
  )
}
