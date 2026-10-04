"""Typed visual observations remain uncertain until independently validated."""
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
from typing import Literal
from PIL import Image
from pydantic import Field, model_validator
from fitwitness.contracts import Strict, Fact, Interval, SourceRef


class Annotation(Strict):
    field: Literal['width','height','thickness','hole_spacing','material']
    value: str | None = Field(default=None,max_length=100)
    unit: Literal['mm','cm','m','in'] | None = None
    visible_text: str = Field(max_length=200)
    bbox: tuple[float,float,float,float]

    @model_validator(mode='after')
    def valid_box(self):
        SourceRef(revision_id='validation',source_hash='0'*64,page=1,bbox=self.bbox)
        return self


class ImageReading(Strict):
    annotations: list[Annotation] = Field(default_factory=list,max_length=12)


def image_facts(reading,revision_id,source_hash,region=(0.,0.,1.,1.)):
    facts=[]
    for a in reading.annotations:
        if a.value is None:
            continue
        value=a.value.strip()
        if not value:
            continue
        if a.field!='material':
            try:
                number=Decimal(value)
                if not number.is_finite() or not a.unit:
                    continue
                value=Interval(low=number,high=number)
            except InvalidOperation:
                continue
        x,y,right,bottom=region
        box=(x+a.bbox[0]*(right-x),y+a.bbox[1]*(bottom-y),x+a.bbox[2]*(right-x),y+a.bbox[3]*(bottom-y))
        facts.append(Fact(field=a.field,value=value,unit=a.unit,
            source=SourceRef(revision_id=revision_id,source_hash=source_hash,page=1,bbox=box),
            method='vlm_observation',certainty='uncertain'))
    return facts


def validated_png(data,region=(0.,0.,1.,1.)):
    if len(data)>10_000_000:
        raise ValueError('image exceeds byte limit')
    try:
        with Image.open(BytesIO(data)) as im:
            if im.width*im.height>16_000_000:
                raise ValueError('image exceeds pixel limit')
            w,h=im.size
            bounds=(int(region[0]*w),int(region[1]*h),int(region[2]*w),int(region[3]*h))
            if bounds[2]<=bounds[0] or bounds[3]<=bounds[1]:
                raise ValueError('empty image region')
            image=im.convert('RGB').crop(bounds)
            image.thumbnail((1600,1600))
            out=BytesIO();image.save(out,format='PNG')
            return out.getvalue()
    except (OSError,Image.DecompressionBombError) as exc:
        raise ValueError('invalid image') from exc
