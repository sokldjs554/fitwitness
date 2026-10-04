"""Read actual STEP solids and tessellate the same geometry for visualization."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import cadquery as cq


def extract_step(data: bytes) -> dict:
    if len(data)>20*1024*1024 or b'ISO-10303-21' not in data[:256]:raise ValueError('unsupported or oversized STEP')
    try:
        with TemporaryDirectory(prefix='fw-step-') as folder:
            path=Path(folder)/'input.step';path.write_bytes(data)
            shape=cq.importers.importStep(str(path)).val();box=shape.BoundingBox()
            verts,faces=shape.tessellate(.35)
            values={'bbox_mm':[box.xlen,box.ylen,box.zlen],'volume_mm3':shape.Volume(),'surface_mm2':shape.Area(),
                    'mesh':{'vertices':[[v.x,v.y,v.z] for v in verts],'faces':[list(f) for f in faces]}}
            values['source_hash']=sha256(data).hexdigest()
            values['feature_hash']=sha256(json.dumps({k:v for k,v in values.items() if k!='mesh'},sort_keys=True).encode()).hexdigest()
            return values
    except Exception as exc:raise ValueError('unreadable STEP') from exc
