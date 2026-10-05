"""Read actual STEP solids and tessellate the same geometry for visualization."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import math
import cadquery as cq


def _select_primary_shape(objects):
    """Pick the physical body and ignore void/annotation-only STEP objects."""
    candidates = []
    for shape in objects:
        try:
            box = shape.BoundingBox()
            dims = [float(box.xlen), float(box.ylen), float(box.zlen)]
            volume = abs(float(shape.Volume()))
            area = float(shape.Area())
        except Exception:
            continue
        if (
            all(math.isfinite(value) and value > 0 for value in dims)
            and math.isfinite(volume)
            and volume > 0
            and math.isfinite(area)
            and area > 0
        ):
            candidates.append((volume, area, shape))
    if not candidates:
        raise ValueError("STEP contains no measurable physical body")
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def extract_step(data: bytes, *, include_mesh: bool = True) -> dict:
    if len(data) > 20 * 1024 * 1024 or b"ISO-10303-21" not in data[:256]:
        raise ValueError("unsupported or oversized STEP")
    try:
        with TemporaryDirectory(prefix="fw-step-") as folder:
            path = Path(folder) / "input.step"
            path.write_bytes(data)
            imported = cq.importers.importStep(str(path))
            shape = _select_primary_shape(imported.vals())
            box = shape.BoundingBox()
            values = {
                "bbox_mm": [float(box.xlen), float(box.ylen), float(box.zlen)],
                "volume_mm3": abs(float(shape.Volume())),
                "surface_mm2": float(shape.Area()),
            }
            if include_mesh:
                verts, faces = shape.tessellate(0.35)
                values["mesh"] = {
                    "vertices": [[v.x, v.y, v.z] for v in verts],
                    "faces": [list(f) for f in faces],
                }
            values["source_hash"] = sha256(data).hexdigest()
            values["feature_hash"] = sha256(
                json.dumps(
                    {k: v for k, v in values.items() if k != "mesh"}, sort_keys=True
                ).encode()
            ).hexdigest()
            return values
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("unreadable STEP") from exc
