import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { asset } from "../types";
import { parseMesh, projectMesh, type MeshData } from "./meshProjection";

type ViewState =
  | { mode: "loading" }
  | { mode: "error"; message: string }
  | { mode: "interactive" }
  | { mode: "projection"; triangles: ReturnType<typeof projectMesh> };

// A separate keyed viewer prevents a previous drawing's state appearing on a new one.
export function Model({ id }: { id: string }) {
  return <ModelViewer key={id} id={id} />;
}

function ModelViewer({ id }: { id: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [attempt, setAttempt] = useState(0);
  const [view, setView] = useState<ViewState>({ mode: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    let disposeScene = () => {};
    setView({ mode: "loading" });

    async function load() {
      try {
        const response = await fetch(asset(id, "mesh"), { signal: controller.signal });
        if (!response.ok) throw new Error(`형상 요청 실패 (HTTP ${response.status})`);
        const mesh = parseMesh(await response.json());
        if (controller.signal.aborted) return;
        const fallback = () => {
          if (!controller.signal.aborted) {
            setView({ mode: "projection", triangles: projectMesh(mesh) });
          }
        };
        const scene = mountInteractiveMesh(ref.current!, mesh, fallback);
        if (scene) {
          disposeScene = scene;
          setView({ mode: "interactive" });
        } else fallback();
      } catch (error) {
        if (!controller.signal.aborted) {
          setView({
            mode: "error",
            message: error instanceof Error ? error.message : "형상 파일을 불러오지 못했습니다.",
          });
        }
      }
    }
    void load();
    return () => {
      controller.abort();
      disposeScene();
    };
  }, [id, attempt]);

  return (
    <div className="model" aria-busy={view.mode === "loading"}>
      <div ref={ref} style={{ position: "absolute", inset: 0 }} />
      {view.mode === "loading" && <p role="status">형상 파일을 불러오는 중…</p>}
      {view.mode === "error" && (
        <div role="alert" style={{ position: "relative", textAlign: "center" }}>
          <p style={{ marginBottom: 12 }}>{view.message}</p>
          <button onClick={() => setAttempt((value) => value + 1)}>형상 다시 불러오기</button>
        </div>
      )}
      {view.mode === "projection" && (
        <svg
          role="img"
          aria-label="실제 메시의 2D 투영"
          data-document-id={id}
          viewBox="0 0 560 264"
          style={{ position: "relative", display: "block", width: "100%", height: 264 }}
        >
          <title>원본 CAD 메시에서 계산한 고정 시점 2D 투영</title>
          {view.triangles.map((triangle, index) => (
            <polygon key={index} points={triangle.points} fill={triangle.fill} stroke={triangle.fill} strokeWidth={0.35} strokeLinejoin="round" display={triangle.frontFacing ? undefined : "none"} />
          ))}
        </svg>
      )}
      {view.mode === "interactive" && <span>드래그하여 회전 · 스크롤하여 확대</span>}
      {view.mode === "projection" && (
        <span>WebGL을 사용할 수 없어 실제 메시의 2D 투영을 표시합니다 · 회전 불가</span>
      )}
    </div>
  );
}

function mountInteractiveMesh(el: HTMLDivElement, data: MeshData, onUnavailable: () => void) {
  let renderer: THREE.WebGLRenderer | undefined;
  let geometry: THREE.BufferGeometry | undefined;
  let material: THREE.MeshStandardMaterial | undefined;
  let controls: OrbitControls | undefined;
  let observer: ResizeObserver | undefined;
  const canvas = document.createElement("canvas");
  let disposed = false;
  const cleanup = () => {
    if (disposed) return;
    disposed = true;
    canvas.removeEventListener("webglcontextlost", contextLost);
    observer?.disconnect();
    controls?.dispose();
    renderer?.setAnimationLoop(null);
    geometry?.dispose();
    material?.dispose();
    renderer?.dispose();
    canvas.remove();
  };
  const contextLost = (event: Event) => {
    event.preventDefault();
    cleanup();
    onUnavailable();
  };
  try {
    const context = canvas.getContext("webgl2", { antialias: true, alpha: true });
    if (!context) return null;
    renderer = new THREE.WebGLRenderer({ canvas, context, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 10000);
    geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(data.vertices.flat(), 3));
    geometry.setIndex(data.faces.flat());
    geometry.computeVertexNormals();
    geometry.computeBoundingSphere();
    const sphere = geometry.boundingSphere!;
    geometry.translate(-sphere.center.x, -sphere.center.y, -sphere.center.z);
    material = new THREE.MeshStandardMaterial({ color: 0x8b9487, metalness: 0.35, roughness: 0.5 });
    scene.add(new THREE.Mesh(geometry, material), new THREE.HemisphereLight(0xffffff, 0x4a6671, 3));
    const light = new THREE.DirectionalLight(0xffffff, 3);
    light.position.set(80, 100, 150);
    scene.add(light);
    camera.position.set(sphere.radius * 2, sphere.radius * 1.5, sphere.radius * 2.5);
    camera.far = Math.max(100, sphere.radius * 20);
    camera.near = Math.max(0.001, sphere.radius / 100);
    controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    const resize = () => {
      const width = Math.max(1, el.clientWidth);
      renderer!.setSize(width, 300);
      camera.aspect = width / 300;
      camera.updateProjectionMatrix();
    };
    resize();
    controls.update();
    renderer.render(scene, camera);
    canvas.setAttribute("aria-label", "회전 가능한 실제 CAD 형상");
    canvas.addEventListener("webglcontextlost", contextLost);
    el.appendChild(canvas);
    observer = new ResizeObserver(resize);
    observer.observe(el);
    renderer.setAnimationLoop(() => {
      if (disposed) return;
      try {
        controls!.update();
        renderer!.render(scene, camera);
      } catch {
        cleanup();
        onUnavailable();
      }
    });
    return cleanup;
  } catch {
    cleanup();
    return null;
  }
}
