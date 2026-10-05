export type MeshData = { vertices: [number, number, number][]; faces: [number, number, number][] };

export function parseMesh(value: unknown): MeshData {
  const data = value as Partial<MeshData> | null;
  if (
    !data || !Array.isArray(data.vertices) || !data.vertices.length ||
    !Array.isArray(data.faces) || !data.faces.length ||
    !data.vertices.every((vertex) => Array.isArray(vertex) && vertex.length === 3 && vertex.every(Number.isFinite)) ||
    !data.faces.every((face) => Array.isArray(face) && face.length === 3 &&
      face.every((index) => Number.isInteger(index) && index >= 0 && index < data.vertices!.length))
  ) throw new Error("형상 파일의 메시 데이터가 올바르지 않습니다.");
  return data as MeshData;
}

// Orthographic isometric projection of the downloaded triangles, with painter's
// depth ordering. This is a static 2D fallback, not an interactive WebGL scene.
export function projectMesh(mesh: MeshData) {
  const projected = mesh.vertices.map(([x, y, z]) => ({
    x: (x - y) / Math.sqrt(2),
    y: (x + y - 2 * z) / Math.sqrt(6),
    depth: (x + y + z) / Math.sqrt(3),
  }));
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const point of projected) {
    minX = Math.min(minX, point.x); maxX = Math.max(maxX, point.x);
    minY = Math.min(minY, point.y); maxY = Math.max(maxY, point.y);
  }
  const scale = Math.min(510 / Math.max(maxX - minX, 1e-6), 224 / Math.max(maxY - minY, 1e-6));
  return mesh.faces.map((face) => {
    const [a, b, c] = face.map((index) => mesh.vertices[index]);
    const u = b.map((value, axis) => value - a[axis]);
    const v = c.map((value, axis) => value - a[axis]);
    const normal = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
    const light = Math.abs((normal[0] * 0.3 + normal[1] * 0.5 + normal[2] * 0.8) / (Math.hypot(...normal) || 1));
    const tone = Math.round(38 + Math.min(1, light) * 31);
    const points = face.map((index) => projected[index]);
    return {
      points: points.map((point) => `${(280 + (point.x - (minX + maxX) / 2) * scale).toFixed(2)},${(132 + (point.y - (minY + maxY) / 2) * scale).toFixed(2)}`).join(" "),
      fill: `hsl(101 7% ${tone}%)`,
      frontFacing: normal[0] + normal[1] + normal[2] > 0,
      depth: points.reduce((sum, point) => sum + point.depth, 0),
    };
  }).sort((a, b) => a.depth - b.depth);
}
