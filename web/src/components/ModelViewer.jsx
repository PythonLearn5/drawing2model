// GLB 模型查看器（three.js）：旋转 / 缩放 / 线框 / 剖切 / 自适应
// 单模型展示：仅渲染最终交付模型（多产物切换功能已移除）。
import React, { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { Button, Space, Spin } from 'antd'
import { ReloadOutlined, BorderOutlined, ColumnHeightOutlined, FullscreenOutlined, FullscreenExitOutlined } from '@ant-design/icons'

export default function ModelViewer({ url, dark, height = 460 }) {
  const selUrl = url

  const wrapRef = useRef(null)
  const shellRef = useRef(null)
  const stateRef = useRef({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [wire, setWire] = useState(false)
  const [clip, setClip] = useState(false)
  const [isFs, setIsFs] = useState(false)

  useEffect(() => {
    const wrap = wrapRef.current
    if (!wrap || !selUrl) return undefined

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(
      45, wrap.clientWidth / Math.max(height, 1), 0.1, 8000)
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.setSize(wrap.clientWidth, height)
    wrap.appendChild(renderer.domElement)

    const hemi = new THREE.HemisphereLight(0xeaf2ff, 0x1c2c48, 1.05)
    scene.add(hemi)
    const key = new THREE.DirectionalLight(0xffffff, 1.25)
    key.position.set(1.4, 2.2, 1.6)
    scene.add(key)
    const rim = new THREE.DirectionalLight(0x5eb2ff, 0.5)
    rim.position.set(-1.8, 0.8, -1.4)
    scene.add(rim)

    const grid = new THREE.GridHelper(800, 40, 0x3b82f6, 0x24406e)
    grid.material.transparent = true
    grid.material.opacity = 0.22
    scene.add(grid)

    const applyTheme = (d) => {
      if (d) {
        hemi.color.set(0xeaf2ff); hemi.groundColor.set(0x1c2c48); hemi.intensity = 1.05
        key.intensity = 1.25
        rim.color.set(0x5eb2ff); rim.intensity = 0.5
        grid.material.color.set(0x3b82f6); grid.material.opacity = 0.22
      } else {
        hemi.color.set(0xffffff); hemi.groundColor.set(0xe7edf7); hemi.intensity = 1.2
        key.intensity = 1.45
        rim.color.set(0x5b86c9); rim.intensity = 0.55
        grid.material.color.set(0x6b8cce); grid.material.opacity = 0.4
      }
    }
    applyTheme(dark)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.autoRotateSpeed = 0.9

    const clipPlane = new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0)
    let meshes = []
    let model = null

    const fit = () => {
      if (!model) return
      const box = new THREE.Box3().setFromObject(model)
      const c = box.getCenter(new THREE.Vector3())
      const size = box.getSize(new THREE.Vector3()).length()
      model.position.x -= c.x
      model.position.y -= c.y
      model.position.z -= c.z
      controls.target.set(0, 0, 0)
      camera.position.set(size * 0.55, size * 0.5, size * 0.75)
      camera.near = Math.max(size / 200, 0.01)
      camera.far = size * 20
      camera.updateProjectionMatrix()
      grid.position.set(0, box.min.y - c.y, 0)
      clipPlane.constant = 0
      controls.update()
    }

    setLoading(true); setError('')
    new GLTFLoader().load(
      selUrl,
      (gltf) => {
        model = gltf.scene
        model.traverse((o) => {
          if (o.isMesh) {
            o.material = o.material || new THREE.MeshStandardMaterial()
            if (o.material.metalness !== undefined && o.material.metalness < 0.2) {
              o.material.metalness = Math.max(o.material.metalness, 0.35)
              o.material.roughness = Math.min(o.material.roughness ?? 0.6, 0.45)
            }
            meshes.push(o)
          }
        })
        scene.add(model)
        fit()
        controls.autoRotate = true
        setTimeout(() => { controls.autoRotate = false }, 3200)
        setLoading(false)
      },
      undefined,
      () => { setError('GLB 模型加载失败'); setLoading(false) },
    )

    let raf = 0
    const animate = () => {
      raf = requestAnimationFrame(animate)
      controls.update()
      renderer.render(scene, camera)
    }
    animate()

    const onResize = () => {
      const w = wrap.clientWidth
      const h = wrap.clientHeight || height
      if (!w) return
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      renderer.setSize(w, h)
    }
    const onFs = () => {
      setIsFs(!!document.fullscreenElement)
      onResize()
    }
    window.addEventListener('resize', onResize)
    document.addEventListener('fullscreenchange', onFs)

    stateRef.current = { renderer, camera, controls, fit, meshes, clipPlane, scene, applyTheme }

    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', onResize)
      document.removeEventListener('fullscreenchange', onFs)
      controls.dispose()
      renderer.dispose()
      if (renderer.domElement.parentNode === wrap) wrap.removeChild(renderer.domElement)
      stateRef.current = {}
    }
  }, [selUrl, height])

  const toggleFull = () => {
    const el = shellRef.current
    if (!el) return
    if (document.fullscreenElement) document.exitFullscreen()
    else if (el.requestFullscreen) el.requestFullscreen()
  }

  useEffect(() => {
    const { meshes } = stateRef.current
    if (!meshes) return
    meshes.forEach((m) => { if (m.material) m.material.wireframe = wire })
  }, [wire, loading])

  useEffect(() => {
    const { renderer, meshes, clipPlane } = stateRef.current
    if (!renderer || !meshes) return
    renderer.localClippingEnabled = clip
    meshes.forEach((m) => {
      if (m.material) m.material.clippingPlanes = clip ? [clipPlane] : null
    })
  }, [clip, loading])

  useEffect(() => {
    const { applyTheme } = stateRef.current
    if (!applyTheme) return
    applyTheme(dark)
  }, [dark, loading])

  return (
    <div className="viewer-shell" ref={shellRef} style={{ height }}>
      <div ref={wrapRef} className="viewer-canvas" />
      {loading && !error && (
        <div className="viewer-loading"><Spin tip="模型加载中…" /></div>
      )}
      {error && (
        <div className="viewer-loading" style={{ color: 'var(--err)' }}>{error}</div>
      )}
      {selUrl && (
        <Space className="viewer-tools" size={6}>
          <Button size="small" icon={<ReloadOutlined />}
                  onClick={() => stateRef.current.fit && stateRef.current.fit()}>
            复位视角
          </Button>
          <Button size="small"
                  icon={isFs ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
                  onClick={toggleFull}>
            {isFs ? '退出全屏' : '全屏'}
          </Button>
          <Button size="small" icon={<BorderOutlined />}
                  type={wire ? 'primary' : 'default'}
                  onClick={() => setWire(!wire)}>
            线框
          </Button>
          <Button size="small" icon={<ColumnHeightOutlined />}
                  type={clip ? 'primary' : 'default'}
                  onClick={() => setClip(!clip)}>
            剖切
          </Button>
        </Space>
      )}
      <div className="viewer-badge">WEBGL · GLB · PBR RENDER</div>
    </div>
  )
}
