import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { asset } from "../types";
export function Model({ id }: { id: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    const el = ref.current!;
    let disposed = false;
    let renderer: THREE.WebGLRenderer;
    let cleanup = () => {};
    fetch(asset(id, "mesh"))
      .then((r) => r.json())
      .then((data) => {
        if (disposed) return;
        try {
          renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
          renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
          renderer.setSize(el.clientWidth, 300);
          el.appendChild(renderer.domElement);
          const scene = new THREE.Scene();
          const camera = new THREE.PerspectiveCamera(
            35,
            el.clientWidth / 300,
            0.1,
            10000,
          );
          const geometry = new THREE.BufferGeometry();
          geometry.setAttribute(
            "position",
            new THREE.Float32BufferAttribute(data.vertices.flat(), 3),
          );
          geometry.setIndex(data.faces.flat());
          geometry.computeVertexNormals();
          geometry.computeBoundingSphere();
          const sphere = geometry.boundingSphere!;
          geometry.translate(
            -sphere.center.x,
            -sphere.center.y,
            -sphere.center.z,
          );
          const material = new THREE.MeshStandardMaterial({
            color: 0x8b9487,
            metalness: 0.35,
            roughness: 0.5,
          });
          const mesh = new THREE.Mesh(geometry, material);
          scene.add(mesh, new THREE.HemisphereLight(0xffffff, 0x4a6671, 3));
          const light = new THREE.DirectionalLight(0xffffff, 3);
          light.position.set(80, 100, 150);
          scene.add(light);
          camera.position.set(
            sphere.radius * 2,
            sphere.radius * 1.5,
            sphere.radius * 2.5,
          );
          const controls = new OrbitControls(camera, renderer.domElement);
          controls.enableDamping = true;
          const observer = new ResizeObserver(() => {
            renderer.setSize(el.clientWidth, 300);
            camera.aspect = el.clientWidth / 300;
            camera.updateProjectionMatrix();
          });
          observer.observe(el);
          renderer.setAnimationLoop(() => {
            controls.update();
            renderer.render(scene, camera);
          });
          cleanup = () => {
            observer.disconnect();
            controls.dispose();
            renderer.dispose();
            geometry.dispose();
            material.dispose();
            renderer.domElement.remove();
          };
        } catch {
          setErr(
            "이 브라우저에서는 3D를 표시할 수 없습니다. STEP 파일을 내려받아 확인해 주세요.",
          );
        }
      })
      .catch(() => setErr("3D 파일을 불러오지 못했습니다."));
    return () => {
      disposed = true;
      cleanup();
    };
  }, [id]);
  return (
    <div className="model" ref={ref}>
      {err && <p>{err}</p>}
      <span>드래그하여 회전 · 스크롤하여 확대</span>
    </div>
  );
}
