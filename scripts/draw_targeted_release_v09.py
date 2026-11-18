"""Original public example: host-designed targeting and light-triggered release.

No preparation, dosing, ligand, wavelength or release chemistry is inferred.
Program executes only when explicitly invoked. Saved plans never execute it.
"""
from __future__ import annotations
import argparse
import copy
import math
from pathlib import Path
from scientific_figure.builder import SceneBuilder
from scientific_figure.context import canonical_hash
from scientific_figure.planning_tools import draft_plan, context_for, check_plan, suggest
from scientific_figure.planning import compile_plan
from scientific_figure.project import json_text, write_project

W,H=1320,760
INK='#254457';MUTED='#627987';GOLD='#e8b85f';TEAL='#359a8c';BLUE='#9fc8da';RED='#d96865'


class Drawing:
    def __init__(self):self.objects=[];self.resources=[]
    def add(self,oid,kind,geometry=None,**kw):
        node={'id':oid,'kind':kind,**kw}
        if geometry is not None:node['geometry']=geometry
        self.objects.append(node)
    def group(self,oid,**kw):self.add(oid,'group',**kw)
    def path(self,oid,d,fill='none',stroke=None,width=1,parent=None,**style):
        self.add(oid,'path',{'d':d},**({'parent':parent} if parent else {}),style={'fill':fill,**({'stroke':stroke,'stroke_width':width} if stroke else {}),**style})
    def circle(self,oid,x,y,r,fill,parent=None,**style):
        self.add(oid,'ellipse',{'cx':x,'cy':y,'rx':r,'ry':r},**({'parent':parent} if parent else {}),style={'fill':fill,**style})
    def text(self,oid,x,y,text,size=18,fill=INK,parent=None,anchor='start'):
        self.add(oid,'text',{'x':x,'y':y,'text':text},style={'fill':fill,'font_size':size,'anchor':anchor},**({'parent':parent} if parent else {}))
    def rect(self,oid,x,y,w,h,fill,rx=0):
        self.add(oid,'rect',{'x':x,'y':y,'width':w,'height':h,**({'rx':rx} if rx else {})},style={'fill':fill})
    def scene(self):
        builder=SceneBuilder(W,H,schema_version='0.3',background='none');builder.add_many(self.objects,resources=self.resources);return builder.build()


def arc(cx,cy,r,a,z):
    n=max(1,math.ceil(abs(z-a)/30));d=''
    for i in range(n):
        p=math.radians(a+(z-a)*i/n);q=math.radians(a+(z-a)*(i+1)/n);k=4/3*math.tan((q-p)/4)
        if i==0:d=f'M{cx+r*math.cos(p):.3f} {cy+r*math.sin(p):.3f}'
        d+=f' C{cx+r*(math.cos(p)-k*math.sin(p)):.3f} {cy+r*(math.sin(p)+k*math.cos(p)):.3f} {cx+r*(math.cos(q)+k*math.sin(q)):.3f} {cy+r*(math.sin(q)-k*math.cos(q)):.3f} {cx+r*math.cos(q):.3f} {cy+r*math.sin(q):.3f}'
    return d


def particle(d,oid,x,y,r,opened=False):
    d.group(oid,transform=[r/90,0,0,r/90,x,y])
    def key(name):return oid+'-'+name
    # Deliberate section window, not an inferred organelle or transparency claim.
    if opened:
        outline=arc(0,0,90,35,325)+' L34 -27 Q17 -2 34 27 Z'
        d.path(key('shell'),outline,'url(#shell-paint)','#be914a',1.7,oid)
        d.path(key('shell-rim'),arc(0,0,78,40,320),stroke='#f6d99c',width=2,parent=oid)
    else:
        d.circle(key('shell'),0,0,90,'url(#shell-paint)',oid,stroke='#be914a',stroke_width=1.7)
    d.path(key('section'),'M-9 -70 C29 -71 60 -46 70 -15 L-19 12 C-31 -15 -25 -51 -9 -70 Z','#fbefd0','#c19c60',1.3,oid)
    d.path(key('core'),'M-5 -58 C22 -58 45 -39 53 -22 L-14 -3 C-19 -20 -16 -45 -5 -58 Z','#728ea1','#546f83',1.1,oid)
    positions=[(5,-42),(22,-35),(4,-22),(35,-26),(17,-15)]
    if not opened:positions += [(-30,12),(-7,35),(27,34)]
    for i,(px,py) in enumerate(positions):d.circle(key(f'payload-{i}'),px,py,4.5,TEAL,oid,stroke='#f4fffb',stroke_width=.7)
    if opened:
        for i,(px,py) in enumerate([(67,4),(89,21),(112,6),(131,29),(151,12)]):d.circle(key(f'released-{i}'),px,py,5.5,TEAL,oid)


def resources(d):
    d.resources.append({'id':'shell-paint','kind':'radial_gradient','center':[.38,.32],'focus':[.28,.22],'radius':.8,
        'stops':[[0,'#fff4cd'],[.55,'#f1cd83'],[1,'#d9a64c']]})


def frame():
    d=Drawing();d.rect('background',0,0,W,H,'#ffffff')
    d.text('title',52,65,'Targeted nanoparticles & light-triggered release',32)
    d.text('subtitle',54,99,'Conceptual mechanism',17,MUTED)
    for i,(x,label) in enumerate([(40,'1  Nanoparticle'),(464,'2  Tumor targeting'),(888,'3  Triggered release')]):
        d.rect(f'panel-{i}',x,143,392,485,['#fbf8ef','#f1f7fa','#fcf4f1'][i],20)
        d.text(f'step-{i}',x+24,183,label,22)
    d.text('footer',52,708,'Schematic only: composition, targeting route, wavelength and release chemistry are unspecified.',15,MUTED)
    return d.scene()


def intact():
    d=Drawing();resources(d);particle(d,'nanoparticle',222,335,94)
    d.text('shell-label',77,488,'Carrier shell',18)
    d.path('shell-leader','M181 482 L170 447 L162 404',stroke=MUTED,width=1.2)
    d.text('payload-label',242,506,'Payload',18)
    d.path('payload-leader','M273 480 L266 428 L251 376',stroke=MUTED,width=1.2)
    d.text('particle-caption',72,568,'A carrier contains a payload;',17,MUTED)
    d.text('particle-caption-2',72,592,'the section exposes its interior.',17,MUTED)
    return d.scene()


def tumor():
    d=Drawing();resources(d);d.group('tumor')
    points=[(577,297,40),(639,272,44),(697,295,41),(554,357,46),(617,343,50),(679,350,48),(716,393,38),(583,413,42),(649,423,47)]
    for i,(x,y,r) in enumerate(points):
        oid=f'cell-{i}';d.group(oid,parent='tumor')
        # Smooth closed contour with curved quadrant construction, no microscopic tracing.
        shape=f'M{x-r} {y} C{x-r*1.1} {y-r*.65} {x-r*.5} {y-r*1.1} {x} {y-r} C{x+r*.6} {y-r*1.08} {x+r*1.08} {y-r*.45} {x+r} {y} C{x+r*1.05} {y+r*.65} {x+r*.4} {y+r*1.1} {x} {y+r} C{x-r*.7} {y+r*1.05} {x-r*1.1} {y+r*.4} {x-r} {y} Z'
        d.path(f'membrane-{i}',shape,BLUE,'#71a9c0',1.3,oid)
        d.circle(f'nucleus-{i}',x-4,y+3,r*.34,'#6f9fb7',oid)
    particle(d,'arriving-particle',517,253,24)
    d.path('approach-route','M496 295 C498 373 508 419 540 458',stroke='#b49557',width=2,stroke_dasharray=[5,6])
    particle(d,'localized-particle',730,447,26)
    d.text('tumor-label',548,514,'Tumor region',19)
    d.text('targeting-caption',494,568,'Preferential localization is illustrated;',16,MUTED)
    d.text('targeting-caption-2',494,592,'no receptor mechanism is assumed.',16,MUTED)
    return d.scene()


def release(revision=0):
    d=Drawing();resources(d);particle(d,'opening-particle',1058,387,76,True)
    d.group('laser',transform=[.8,-.6,.6,.8,1186,240])
    d.path('laser-body','M-35 -16 H32 Q38 -16 38 -10 V10 Q38 16 32 16 H-35 Z','#a9c4ce','#638696',1.5,'laser')
    d.add('laser-aperture','ellipse',{'cx':-35,'cy':0,'rx':6,'ry':16},parent='laser',style={'fill':'#6b8d9d','stroke':'#476d80','stroke_width':1})
    d.path('beam','M1149 259 L1027 338 L1090 369 Z',RED,opacity=.22)
    d.path('beam-center','M1150 259 L1058 348',stroke=RED,width=2)
    d.text('laser-label',1096,214,'Laser',18)
    if revision:
        d.text('released-label',1090,514,'Released payload',18)
        d.path('release-leader','M1193 493 L1194 423 L1177 411',stroke=MUTED,width=1.2)
    else:
        d.text('released-label',1080,444,'Released payload',18)
        d.path('release-leader','M1172 432 L1178 413',stroke=MUTED,width=1.2)
    d.text('release-caption',916,568,'Local light exposure opens the carrier;',16,MUTED)
    d.text('release-caption-2',916,592,'payload release is shown schematically.',16,MUTED)
    return d.scene()


def draw(output, revision=0):
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    def save(name,value):(out/name).write_text(json_text(value),encoding='utf-8')
    requested='画一个纳米颗粒靶向肿瘤并通过激光触发释放的机制示意图。'
    objects=[{'id':oid,'role':role,'editable_parts':parts} for oid,role,parts in [
        ('framing','Host-designed reading order and captions',['title']),
        ('particle','User-explicit nanoparticle',['nanoparticle-shell','nanoparticle-core','nanoparticle-payload-0']),
        ('target','User-explicit tumor targeting',['tumor','membrane-0','localized-particle']),
        ('release','User-explicit laser and release',['laser','opening-particle','opening-particle-released-0','released-label'])]]
    request={'id':'targeted_release','task_type':'text_design','purpose':requested,'canvas':{'width':W,'height':H,'background':'#ffffff'},
        'modules':[{'id':'mechanism','objects':objects}],
        'relations':[{'id':'targeting-meaning','type':'points_to','mode':'semantic','source':{'object_id':'particle'},'target':{'object_id':'target'},'certainty':'declared','evidence':'User requested targeting, without specifying a receptor or delivery route.'},
                     {'id':'release-meaning','type':'points_to','mode':'semantic','source':{'object_id':'target'},'target':{'object_id':'release'},'certainty':'declared','evidence':'User requested laser-triggered release; wavelength and molecular mechanism are unspecified.'}],
        'review_spec':{'required_objects':['framing','particle','target','release'],'unknown':['Composition, target route, wavelength, dose and release chemistry unspecified.'],'text_policy':'host_authored','scientific_review':'not_executed'}}
    plan=draft_plan(request);save('draft-plan.json',plan);save('semantic-check.json',check_plan(plan))
    save('particle-candidates.json',suggest({'query':'particle','required_parts':['shell','core','payload.0'],'limit':3}))
    save('tumor-candidates.json',suggest({'query':'cell','required_parts':['membrane','irregular_cell_cluster'],'limit':3}))
    fragments={'framing':frame(),'particle':intact(),'target':tumor(),'release':release(revision)}
    reasons={'framing':'Original direct program sets a restrained three-stage reading order; no experimental steps added.',
        'particle':'Free program preserves a visible section, shell, core and payload; no targeting ligand is inferred.',
        'target':'Generic cell Recipe lacks irregular tumor cluster and local particles; explicit editable geometry chosen.',
        'release':'No laser-release Recipe exists; free program supplies laser, opening section and dispersed payload.'}
    for obj in plan['modules'][0]['objects']:
        oid=obj['id'];obj['construction']={'kind':'program','candidate':oid,'sha256':canonical_hash(fragments[oid])}
    # Two actual installed Recipe arrows used only for the explicit reading order.
    for i,(oid,x) in enumerate([('reading-arrow-a',427),('reading-arrow-b',851)]):
        plan['modules'][0]['objects'].append({'id':oid,'role':'Host-designed reading order','construction':{'kind':'recipe','id':'arrow','version':'1.0.0','params':{'start':[x,342],'end':[x+43,342],'shaft_width':3,'head_width':13,'head_length':13}}})
    context=context_for(plan,requested)
    for obj in plan['modules'][0]['objects']:
        oid=obj['id'];context['objects'][oid]={'construction_sha256':canonical_hash(obj['construction']),
            'reason':reasons.get(oid,'Installed arrow Recipe fits a simple directed reading guide.'),
            'strategies':['filled_shape','text'] if oid=='framing' else ['filled_shape','gradient','repeated_instances'] if oid in fragments else ['filled_shape'],
            'confirmed':True,'unknown':[]}
    save('figure-plan.json',plan);save('planning-context.json',context);save('fragments.json',fragments)
    check=check_plan(plan,context=context,phase='construction',fragments=fragments);save('construction-check.json',check)
    if check['status']!='compilable':raise ValueError(check)
    scene,extras,report=compile_plan(plan,fragments=fragments,planning_context=context)
    write_project(scene,out/'project',scale=1.5,extras=extras)
    save('reuse.json',{'external_artwork':[],'drawing_code':'Original program authored for this description; arc/Batch patterns from own prior examples, new composition and geometry.','libraries':['SceneBuilder','FontTools engine text','resvg engine rendering'],'recipes':['arrow@1.0.0'],'scientific_assumptions':'Opening carrier and dispersed dots are schematic design choices, not a specific release chemistry.'})
    return {'project':str(out/'project'),'objects':len(scene['objects']),'revision':revision}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',type=int,choices=[0,1],default=0);a=p.parse_args()
    print(draw(a.output,a.revision))
