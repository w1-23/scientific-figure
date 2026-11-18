"""Recover straight open strokes instead of outlining their thickness.

Curved/branched lines are deliberately rejected; host-supplied cubic paths remain
available when a centerline cannot be inferred reliably.
"""
from __future__ import annotations
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from .quality import mask_metrics
from .render import render_svg
from .svg import to_svg
from io import BytesIO


def fit_stroke(image_path: Path, region: list[int], *, object_id: str, parameters: dict):
    allowed={"hsv","stroke","stroke_width","max_deviation","arrow_end"}
    if set(parameters)-allowed:raise ValueError("unsupported stroke parameters")
    if len(region)!=4 or any(type(v) is not int for v in region):raise ValueError("invalid stroke region")
    x,y,w,h=region
    if min(x,y)<0 or min(w,h)<=0 or w*h>512*512:raise ValueError("invalid stroke region or budget")
    with Image.open(image_path) as image:
        if x+w>image.width or y+h>image.height:raise ValueError("stroke region outside source")
        rgba=np.asarray(image.convert("RGBA").crop((x,y,x+w,y+h)))
        rgb=rgba[:,:,:3]
    bounds=parameters.get("hsv",[[0,0,0],[179,255,110]])
    if (not isinstance(bounds,list) or len(bounds)!=2 or any(len(v)!=3 for v in bounds)
        or any(type(v) is not int for row in bounds for v in row)
        or any(not 0<=bounds[0][i]<=bounds[1][i]<=cap for i,cap in enumerate([179,255,255]))):
        raise ValueError("invalid HSV bounds")
    mask=(cv2.inRange(cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV),np.array(bounds[0],np.uint8),np.array(bounds[1],np.uint8))>0)&(rgba[:,:,3]>0)
    count,labels,stats,_=cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
    components=[i for i in range(1,count) if stats[i,cv2.CC_STAT_AREA]>=3]
    if len(components)!=1:raise ValueError("needs_input: fit_stroke requires one connected unbranched stroke")
    yy,xx=np.nonzero(labels==components[0]);points=np.column_stack([xx,yy]).astype(float)
    if len(points)<6:raise ValueError("needs_input: too few stroke pixels")
    origin=points.mean(axis=0);_,values,vectors=np.linalg.svd(points-origin,full_matrices=False)
    direction=vectors[0]
    if direction[0]<0 or abs(direction[0])<1e-9 and direction[1]<0:direction=-direction
    normal=np.array([-direction[1],direction[0]])
    along=(points-origin)@direction;perp=(points-origin)@normal
    start,end=np.quantile(along,[0.005,0.995]);length=end-start
    if length<4 or values[1]/values[0]>0.18:raise ValueError("needs_input: region is not a thin straight stroke")
    inferred=max(0.5,float(len(points)/(length+1)))
    width=parameters.get("stroke_width",inferred)
    deviation=parameters.get("max_deviation",1.2)
    if isinstance(width,bool) or not isinstance(width,(int,float)) or not np.isfinite(width) or not 0<width<=100:
        raise ValueError("invalid stroke width")
    if isinstance(deviation,bool) or not isinstance(deviation,(int,float)) or not np.isfinite(deviation) or not 0<deviation<=10:
        raise ValueError("invalid stroke deviation")
    errors=[]
    bins=np.linspace(start,end,9)
    for low,high in zip(bins[:-1],bins[1:]):
        values_in_bin=perp[(along>=low)&(along<=high)]
        if len(values_in_bin):errors.append(abs(float(np.median(values_in_bin))))
    if max(errors,default=0)>deviation:raise ValueError("needs_input: curved centerline exceeds straight-stroke tolerance")
    # Erode half the line width from extrema because round caps restore those pixels.
    inset=min(width/2,length/4)
    # Pixel indices refer to pixel cells; the SVG center is offset by half a pixel.
    p1=origin+0.5+direction*(start+inset);p2=origin+0.5+direction*(end-inset)
    node={"id":object_id,"kind":"path","geometry":{"d":f"M {p1[0]:.5f} {p1[1]:.5f} L {p2[0]:.5f} {p2[1]:.5f}"},
          "style":{"fill":"none","stroke":parameters.get("stroke","#222222"),"stroke_width":float(width),
                   "arrow_end":parameters.get("arrow_end",False)}}
    test={"schema_version":"0.1","revision":0,"canvas":{"width":w,"height":h},"objects":[node]}
    rendered=np.asarray(Image.open(BytesIO(render_svg(to_svg(test),2))).convert("RGBA"))[:,:,3]>=128
    expected=cv2.resize(mask.astype(np.uint8),(w*2,h*2),interpolation=cv2.INTER_NEAREST)>0
    audit=mask_metrics(expected,rendered)
    return node,{"backend":"principal-axis-centerline","region":region,"estimated_width":inferred,
                 "centerline_deviation":max(errors,default=0),"audit":audit,
                 "status":"passed" if audit["iou"]>=0.8 and audit["topology_equal"] else "needs_review",
                 "limitations":["Only straight, single-component strokes are supported.",
                                "Arrow meaning is supplied by host, not inferred from pixels."]}
