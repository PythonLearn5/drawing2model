// API 封装 — 对齐 Drawing2Model Agent 后端 (/api/jobs)
// REST + 轮询（后端无 SSE 日志流，日志随状态接口一并返回）
const BASE = '/api'

async function _json(resp) {
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const d = await resp.json()
      detail = d.detail || JSON.stringify(d)
    } catch (e) { /* 忽略解析失败 */ }
    throw new Error(detail)
  }
  return resp.json()
}

// 服务状态：LLM 在线 / 模型 / 案例库 / 全部任务状态表
export function getStatus() {
  return fetch(`${BASE}/status`).then(_json)
}

// 任务列表（由 /api/status 的 jobs 字段展开：{id: {status, mode, created_at, source_name}}）
export async function listJobs() {
  const d = await getStatus()
  const rows = Object.entries(d.jobs || {}).map(([id, v]) => ({
    id,
    status: typeof v === 'string' ? v : (v.status || 'unknown'),
    mode: (typeof v === 'object' && v.mode) || '',
    created_at: (typeof v === 'object' && v.created_at) || null,
    source_name: (typeof v === 'object' && v.source_name) || '',
  }))
  rows.sort((a, b) => ((b.created_at || 0) - (a.created_at || 0)) || (a.id < b.id ? 1 : -1))
  return rows
}

// 任务详情（状态 + 日志 + 迭代历史 + 产物清单 + 报告地址）
export async function getJob(id) {
  const t = await fetch(`${BASE}/jobs/${id}`).then(_json)
  return normalizeJob(t)
}

// 将后端原始 job 对象规范化为页面所需的统一结构
export function normalizeJob(t) {
  const done = t.status === 'done'
  const result = t.result || {}
  // manifest: {kind: {file, ok, size}} 字典（后端 /api/jobs/{id} 附带）
  const manifest = t.manifest || {}
  const KIND_LABEL = {
    glb: 'GLB 模型', stl: 'STL 模型', step: 'STEP 模型', obj: 'OBJ 模型',
    dxf: 'DXF 三视图', model_mill: '铣削 G-Code', model_turn: '车削 G-Code',
    report: '制造报告',
  }
  const artifacts = []
  if (done && Object.keys(manifest).length) {
    for (const [kind, it] of Object.entries(manifest)) {
      if (!it.ok) continue
      // 三视图投影按需求不在产物面板展示
      if (kind === 'proj_light' || kind === 'proj_dark') continue
      artifacts.push({
        kind,
        label: KIND_LABEL[kind] || kind,
        file: it.file || '',
        size: it.size ?? null,
        url: `${BASE}/jobs/${t.id}/artifacts/${kind}`,
      })
    }
    artifacts.push({
      kind: 'report', label: '制造报告', file: 'report.html', size: null,
      url: `${BASE}/jobs/${t.id}/artifacts/report`,
    })
  } else if (done) {
    // 无 manifest 时兜底常用产物
    for (const [kind, label] of [['glb', 'GLB 模型'], ['stl', 'STL 模型'], ['report', '制造报告']]) {
      artifacts.push({ kind, label, file: '', size: null, url: `${BASE}/jobs/${t.id}/artifacts/${kind}` })
    }
  }
  return {
    id: t.id,
    source_name: t.source_name || '',
    status: t.status,
    mode: t.mode || 'pipeline',
    created_at: t.created_at || null,
    log: t.log || [],
    error: t.error || '',
    result,
    artifacts,
    report: done ? `${BASE}/jobs/${t.id}/artifacts/report` : null,
    best_round: result.best_round ?? null,
    best_score: result.best_score ?? null,
    history: result.history || [],
  }
}

// 提交重建任务（multipart：pdf 文件 + mode + max_rounds）
export async function submitJob(file, { mode = 'pipeline', maxRounds = 4 } = {}, onProgress) {
  const form = new FormData()
  form.append('pdf', file)
  form.append('mode', mode)
  form.append('max_rounds', String(maxRounds))
  const xhr = new XMLHttpRequest()
  return new Promise((resolve, reject) => {
    xhr.open('POST', `${BASE}/jobs`)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText))
      else {
        let detail = `HTTP ${xhr.status}`
        try { detail = JSON.parse(xhr.responseText).detail } catch (e) { /* ignore */ }
        reject(new Error(detail))
      }
    }
    xhr.onerror = () => reject(new Error('上传失败（网络错误）'))
    xhr.send(form)
  })
}

// 产物直链（kind: glb/stl/step/obj/dxf/report/gcode_mill/gcode_turn/...）
export function artifactUrl(id, kind) {
  return `${BASE}/jobs/${id}/artifacts/${kind}`
}

// 删除单个任务（运行中的任务后端会返回 409）
export function deleteJob(id) {
  return fetch(`${BASE}/jobs/${id}`, { method: 'DELETE' }).then(_json)
}

// 批量删除任务，返回 {deleted: [...], skipped: {id: reason}}
export function deleteJobsBatch(ids) {
  return fetch(`${BASE}/jobs/delete-batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  }).then(_json)
}

// 终态判断
export const TERMINAL = ['done', 'failed', 'cancelled']
export const isTerminal = (s) => TERMINAL.includes(s)

// 阶段 → Steps 映射（evolve 闭环管线）
export const STAGE_STEPS = [
  { key: 'analyze', label: '图纸解析' },
  { key: 'code', label: 'LLM 生成建模代码' },
  { key: 'run', label: '沙箱执行 · 建模' },
  { key: 'compare', label: '视觉比对 · 迭代' },
  { key: 'deliver', label: '自测 · 交付' },
]

// 由日志推断当前阶段（后端日志为中文进度文本；兼容 {ts,msg} 对象与纯字符串）
// 注意: 不能用 `v\d+`/`第.*轮` 泛匹配判阶段——执行日志 "[E] v1 harness 执行 worker..."
// 也含 "v1", 会把进度条误判到"视觉比对·迭代"。各阶段只用互斥的语义关键词。
// 判定顺序从低阶段到高阶段: 一行日志若含低阶段特征词 (如"高清化无产物"的"高清化")
// 应优先归入低阶段, 避免"产物"等词把预处理日志误判成交付阶段 (曾致进度条跳到 92%)。
const _logText = (l) => (typeof l === 'string' ? l : ((l && l.msg) || ''))
export function stageFromLog(logs = []) {
  const last = [...logs].reverse().map(_logText).find((s) => s) || ''
  // 判定策略: ① 交付阶段精确标记最先判 (曾用宽泛的"产物/完成"把"[E1] 高清化无产物"/
  // "图纸预处理完成"误判成 92% 交付进度); ② 其余阶段从低到高依次匹配,
  // 低阶段特征词优先 (一行日志同时含多阶段词时归入最低阶段, 避免进度回跳假象)。
  if (/交付|自愈|finalize|\[F\]|\[S6\]|产物生成|GLB|G-?code|生成报告|任务总/.test(last)) return 4
  if (/\[S1\]|\[E1\]|高清化|预处理|图纸|OCR|探测/.test(last)) return 0
  // prompt(?!\s*=): 排除 token 统计行的 "prompt=1200" (那属于比对/交付阶段的日志)
  if (/\[S2\]|视觉识别|LLM|prompt(?!\s*=)|脚本|代码/.test(last)) return 1
  if (/\[S4\]|执行|建模|布尔|放样|渲染|重写|校验|几何/.test(last)) return 2
  if (/比对|评分|得分|差异|score=|收敛|迭代|token/.test(last)) return 3
  return -1
}

// 由日志推断百分比进度（粗略，用于进度条展示）
export function progressFromJob(job) {
  if (job.status === 'done') return 100
  if (job.status === 'failed') return 100
  const stage = stageFromLog(job.log || [])
  const base = [6, 25, 50, 75, 92]
  return stage < 0 ? 3 : base[stage]
}

// 时间格式化（支持 unix 秒 / ISO 字符串）
export function fmtTime(ts) {
  if (!ts) return '-'
  let d
  if (typeof ts === 'number') d = new Date(ts * 1000)
  else d = new Date(ts)
  if (Number.isNaN(d.getTime())) return '-'
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

// 文件大小格式化
export function fmtSize(n) {
  if (n == null) return ''
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(2)} MB`
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${n} B`
}
