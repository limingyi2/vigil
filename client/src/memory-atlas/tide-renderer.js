import * as THREE from './vendor/three.module.min.js';
import { createSculptedNebula, nebulaFlowGLSL } from './sculpted-nebula.js';
import { createDeepSky, skyBackgroundGLSL } from './deep-sky.js';

const seeded = initial => { let seed = initial; return () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; }; };
const clamp = (x, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, x));

// Memory Tide: a procedural 3D environment. Atmospheric filaments provide
// depth; the denser stellar islands are generated from the actual memory layout.
// The optional galaxy uses a 3D stellar disk, bulge and absorbing dust.
// One orbit camera observes all world geometry; no independent sky rotation.
function createNebulaVolume() {
  const n = 96, data = new Uint8Array(n * n * n * 4), random = seeded(78126);
  const grids = [6, 14, 32, 64].map(size => ({ size, values: Float32Array.from({ length: size ** 3 }, random) }));
  const noise = (grid, x, y, z) => {
    const size = grid.size;
    x = clamp(x) * (size - 1.001); y = clamp(y) * (size - 1.001); z = clamp(z) * (size - 1.001);
    const ix = Math.floor(x), iy = Math.floor(y), iz = Math.floor(z);
    let u = x - ix, v = y - iy, w = z - iz;
    u *= u * (3 - 2 * u); v *= v * (3 - 2 * v); w *= w * (3 - 2 * w);
    const at = (a, b, c) => grid.values[(c * size + b) * size + a];
    const lerp = (a, b, t) => a + (b - a) * t;
    return lerp(lerp(lerp(at(ix, iy, iz), at(ix + 1, iy, iz), u), lerp(at(ix, iy + 1, iz), at(ix + 1, iy + 1, iz), u), v), lerp(lerp(at(ix, iy, iz + 1), at(ix + 1, iy, iz + 1), u), lerp(at(ix, iy + 1, iz + 1), at(ix + 1, iy + 1, iz + 1), u), v), w);
  };
  for (let z = 0; z < n; z++) for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    const u = x / (n - 1), v = y / (n - 1), w = z / (n - 1);
    const px = u * 2 - 1, py = v * 2 - 1, pz = w * 2 - 1;
    const warp = noise(grids[0], u, v, w) - .5;
    const f = noise(grids[0], u, v, w) * .45 + noise(grids[1], u + warp * .12, v - warp * .10, w) * .30 + noise(grids[2], u, v, w) * .17 + noise(grids[3], u, v, w) * .08;
    const ribbon = Math.exp(-((py - .19*Math.sin(px*3.4+pz*2.0) - px*.12)**2/.016 + (pz-.16*Math.cos(px*3.1))**2/.13));
    const second = Math.exp(-((py + .25*Math.sin(px*3.0+.4))**2/.030 + (pz+.28)**2/.10));
    const envelope = clamp((1-Math.abs(px))/.18)*clamp((1-Math.abs(py))/.3)*clamp((1-Math.abs(pz))/.3);
    const density = clamp(Math.pow(Math.max(0, f - .39) * 3.7, 1.8) * (ribbon*.85+second*.22) * envelope);
    const warm = clamp((px+.12)*.6);
    const color = [.12*(1-warm)+.42*warm,.38*(1-warm)+.35*warm,.48*(1-warm)+.24*warm];
    const i = ((z * n + y) * n + x) * 4;
    for (let c = 0; c < 3; c++) data[i+c] = clamp(color[c]) * 255;
    data[i+3] = density * 255;
  }
  const texture = new THREE.Data3DTexture(data, n, n, n);
  texture.format = THREE.RGBAFormat;
  texture.type = THREE.UnsignedByteType;
  texture.minFilter = texture.magFilter = THREE.LinearFilter;
  texture.unpackAlignment = 1;
  texture.needsUpdate = true;
  return texture;
}

const screenVertex = `precision highp float;
in vec3 position; in vec2 uv; out vec2 vUv;
void main(){vUv=uv;gl_Position=vec4(position.xy,0.0,1.0);}`;

const nebulaFragment = `precision highp float; precision highp sampler3D;
in vec2 vUv; out vec4 outColor;
uniform sampler3D uVolume; uniform mat3 uInverseRotation;
uniform vec3 uCamera; uniform float uAspect; uniform float uTanFov;
uniform sampler3D uNebulaVolume;uniform vec3 uNebulaBounds;uniform float uNebula;uniform float uTime;uniform float uMotion;uniform float uFlowTime;
${nebulaFlowGLSL}
vec4 cloudAt(vec3 p){
 vec3 uv=p/vec3(5.6,3.2,2.4)+.5;
 vec4 base=texture(uVolume,uv);if(uNebula<.001)return base;
 return mix(base,movingNebula(p),uNebula);
}
vec2 intersectCloud(vec3 o,vec3 d){vec3 inv=1.0/(d+vec3(.000001));vec3 extent=mix(vec3(2.8,1.6,1.2),uNebulaBounds*1.2,uNebula);vec3 a=(-extent-o)*inv,b=(extent-o)*inv;vec3 lo=min(a,b),hi=max(a,b);return vec2(max(lo.x,max(lo.y,lo.z)),min(hi.x,min(hi.y,hi.z)));}
void main(){
  vec2 xy=vUv*2.0-1.0;
  vec3 direction=normalize(uInverseRotation*vec3(xy.x*uAspect*uTanFov,xy.y*uTanFov,-1.0));
  vec2 bounds=intersectCloud(uCamera,direction);
  if(bounds.y<max(0.0,bounds.x)){outColor=vec4(0.0);return;}
  float steps=mix(56.0,128.0,uNebula);
  float stepSize=(bounds.y-max(0.0,bounds.x))/steps;
  float jitter=fract(sin(dot(gl_FragCoord.xy,vec2(12.9898,78.233)))*43758.5453);
  vec3 p=uCamera+direction*(max(0.0,bounds.x)+stepSize*jitter);
  vec4 light=vec4(0.0);
  vec3 lightDir=normalize(vec3(-.35,.65,.8));
  for(int i=0;i<128;i++){
    if(float(i)>=steps)break;
    vec4 field=cloudAt(p);
    float density=field.a;
    if(density>.006){
      float shadow=exp(-cloudAt(p+lightDir*.14).a*2.3);
      float alpha=1.0-exp(-density*stepSize*mix(2.8,4.7,uNebula));
      float starLight=.07/(.035+dot(p-vec3(-.04,.18,.22),p-vec3(-.04,.18,.22)));
      vec3 scattering=mix(field.rgb*(.20+shadow*.60+starLight*.25),field.rgb*1.15,uNebula);
      light.rgb+=(1.0-light.a)*scattering*alpha;
      light.a+=(1.0-light.a)*alpha;
    }
    p+=direction*stepSize;
    if(light.a>.97)break;
  }
  outColor=light;
}`;

const landscapeFragment = `precision highp float;
in vec2 vUv;out vec4 outColor;uniform sampler2D uClouds;uniform float uNebula;uniform mat3 uSkyInverseRotation;uniform float uAspect;uniform float uTanFov;
${skyBackgroundGLSL}
void main(){vec2 p=vUv-.5;float glow=exp(-dot(p*vec2(1.2,2.4),p*vec2(1.2,2.4))*5.0);
vec3 backdrop=mix(vec3(.0006,.0011,.0020),vec3(.0020,.0075,.012),glow);
vec4 cloud=texture(uClouds,vUv);vec2 xy=vUv*2.0-1.0;
vec3 direction=normalize(vec3(xy.x*uAspect*uTanFov,xy.y*uTanFov,-1.0));
vec3 sky=deepSky(direction)*uNebula*pow(max(0.0,1.0-cloud.a),1.7);
outColor=vec4(backdrop+sky+cloud.rgb*mix(.26,.9,uNebula),1.0);}`;

const starVertex = `precision highp float;
in vec3 position; in vec3 color; in float aSize; in float aAlpha;
in float aPhase; in float aBirth; in float aIndex; in float aFlow;
uniform mat4 modelViewMatrix; uniform mat4 projectionMatrix;
uniform float uNebula;uniform float uTime; uniform float uScale; uniform float uSelected; uniform float uHovered;uniform vec3 uFocus;uniform float uHasFocus;uniform float uMotion;
out vec3 vColor; out vec3 vLocal; out float vAlpha; out float vBright; out float vFlash;out float vSelected;out float vMemory;
void main(){
 vec3 drift=vec3(sin(uTime*.13+aPhase),cos(uTime*.11+aPhase*1.3),sin(uTime*.09+aPhase*.7))*aFlow*.014*uMotion;
 vec4 mv=modelViewMatrix*vec4(position+drift,1.0);
 float selected=(abs(aIndex-uSelected)<.1||abs(aIndex-uHovered)<.1)?1.0:0.0;
 float flash=clamp(1.0-(uTime-aBirth)/2.3,0.0,1.0);
 float twinkle=.91+.09*sin(uTime*.42*uMotion+aPhase);
 gl_Position=projectionMatrix*mv;
 gl_PointSize=clamp(aSize*uScale/(-mv.z)*(1.0+selected*.4+flash*1.1),1.0,80.0);
 if(aAlpha<.001)gl_PointSize=0.0;
 float focus=mix(1.0,.28+.72*exp(-dot(position-uFocus,position-uFocus)*2.2),uHasFocus*(1.0-step(0.0,aIndex)));
 vColor=color;vLocal=position;vAlpha=aAlpha*twinkle*focus*(1.0+selected*.65+flash*2.0)*mix(1.0,.35,uNebula*(1.0-step(0.0,aIndex)));vBright=step(9.0,aSize);vFlash=flash;vSelected=selected;vMemory=step(0.0,aIndex);
}`;

const starFragment = `precision highp float; precision highp sampler3D;
in vec3 vColor; in vec3 vLocal; in float vAlpha; in float vBright; in float vFlash;in float vSelected;in float vMemory;
uniform sampler3D uVolume;uniform sampler3D uNebulaVolume;uniform vec3 uNebulaBounds;uniform float uNebula; uniform vec3 uCamera;uniform float uMotion;uniform float uFlowTime;
${nebulaFlowGLSL}
out vec4 outColor;
void main(){
 vec2 uv=gl_PointCoord*2.0-1.0;float r2=dot(uv,uv);if(r2>1.0||vAlpha<.001)discard;
 float core=exp(-r2*55.0),halo=exp(-r2*9.0)*.10;
 float spikes=(exp(-abs(uv.x)*80.0-abs(uv.y)*5.0)+exp(-abs(uv.y)*80.0-abs(uv.x)*5.0))*.045*vBright;
 vec3 ray=normalize(uCamera-vLocal),sampleP=vLocal;float extinction=0.0;
 if(uNebula<.001){
   for(int i=0;i<7;i++){sampleP+=ray*.17;if(any(greaterThan(abs(sampleP),vec3(2.8,1.6,1.2))))break;extinction+=texture(uVolume,sampleP/vec3(5.6,3.2,2.4)+.5).a*.17*1.2;}
 }else{
   // Integrate only the overlap between the point-to-eye ray and the gas box.
   vec3 inv=1.0/(ray+vec3(.000001));vec3 a=(-uNebulaBounds*1.2-vLocal)*inv,b=(uNebulaBounds*1.2-vLocal)*inv;
   vec3 lo=min(a,b),hi=max(a,b);float entry=max(0.0,max(lo.x,max(lo.y,lo.z))),exit=min(length(uCamera-vLocal),min(hi.x,min(hi.y,hi.z)));
   float stepSize=max(0.0,exit-entry)/12.0;
   for(int i=0;i<12;i++){sampleP=vLocal+ray*(entry+(float(i)+.5)*stepSize);extinction+=movingNebula(sampleP).a*stepSize*3.5*uNebula;}
 }
 float transmission=exp(-extinction);transmission=mix(transmission,max(.18,transmission),uNebula*vMemory);
 vec3 spectrum=mix(vColor,vec3(1.0,.86,.60),vFlash*.7);
 float ring=exp(-pow((sqrt(r2)-.69)/.022,2.0))*.12*vSelected*vMemory;
 vec3 radiance=(spectrum*(halo+spikes+ring)*1.3+mix(spectrum,vec3(1.0),.65)*core)*vAlpha*transmission;
 outColor=vec4(radiance,1.0);
}`;

const lineVertex = `precision highp float;in vec3 position;in float aAlpha;in float aProgress;in float aHot;
uniform mat4 modelViewMatrix;uniform mat4 projectionMatrix;out float vAlpha;out float vProgress;out float vHot;
void main(){vAlpha=aAlpha;vProgress=aProgress;vHot=aHot;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}`;
const lineFragment = `precision highp float;in float vAlpha;in float vProgress;in float vHot;uniform float uTime;uniform float uMotion;out vec4 outColor;
void main(){float pulse=exp(-pow((fract(vProgress-uTime*.12*uMotion)-.5)/.075,2.0))*vHot;vec3 color=mix(vec3(.20,.44,.48),vec3(1.0,.76,.42),vHot);outColor=vec4(color*(1.0+pulse*.7),vAlpha*(.65+pulse));}`;

const blurFragment = `precision highp float; in vec2 vUv;out vec4 outColor;
uniform sampler2D uTexture;uniform vec2 uDirection;uniform float uThreshold;
vec3 sampleLight(vec2 p){vec3 c=texture(uTexture,p).rgb;return c*mix(1.0,smoothstep(.35,1.25,max(c.r,max(c.g,c.b))),uThreshold);}
void main(){vec3 c=sampleLight(vUv)*.227027;c+=(sampleLight(vUv+uDirection*1.384615)+sampleLight(vUv-uDirection*1.384615))*.316216;c+=(sampleLight(vUv+uDirection*3.230769)+sampleLight(vUv-uDirection*3.230769))*.070270;outColor=vec4(c,1.0);}`;
const compositeFragment = `precision highp float;in vec2 vUv;out vec4 outColor;
uniform sampler2D uScene;uniform sampler2D uBloom;uniform float uNebula;
void main(){vec3 x=(texture(uScene,vUv).rgb+texture(uBloom,vUv).rgb*(.40+uNebula*.25))*1.05;
vec3 tone=clamp((x*(2.51*x+.03))/(x*(2.43*x+.59)+.14),0.0,1.0);
outColor=vec4(pow(tone,vec3(1.0/2.2)),1.0);}`;

function rawMaterial(vertexShader, fragmentShader, uniforms, options = {}) {
  return new THREE.RawShaderMaterial({ glslVersion: THREE.GLSL3, vertexShader, fragmentShader, uniforms, ...options });
}

export async function createTideRenderer(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false, powerPreference: 'high-performance' });
  renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
  renderer.toneMapping = THREE.NoToneMapping;
  renderer.setClearColor(0x02040a, 1);
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 1.5);
  renderer.setPixelRatio(pixelRatio);
  const camera = new THREE.PerspectiveCamera(40, 1, .1, 160);
  camera.position.set(0, 0, 3.4);
  const scene = new THREE.Scene(), root = new THREE.Group();
  root.rotation.order = 'YXZ';
  scene.add(root);
  const volume = createNebulaVolume();
  const nebulaControl={value:0};
  let nebulaBlend=0,previousFrame=0,wasNebulaEnabled=false;
  const localCamera = new THREE.Vector3(), inverseRotation = new THREE.Matrix3();
  const inverseMatrix = new THREE.Matrix4(), scratch = new THREE.Vector3();
  let width = 1, height = 1, lastEdges = null, lastVisibility = '', lastSelected = '', memoryCount = 0;
  let memoryGeometry, memoryPoints, lineGeometry, lines;
  let projectedClusters=[];
  const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const sceneTime={value:0},sceneFlowTime={value:0},sceneScale={value:3.4},sceneMotion={value:reducedMotion?0:1};
  const sculpted=await createSculptedNebula({intensity:nebulaControl,time:sceneTime,flowTime:sceneFlowTime,motion:sceneMotion,scale:sceneScale,camera:{value:localCamera}});
  const nebulaVolume=sculpted.volume;root.add(sculpted.photons,sculpted.filaments);

  const uniforms = {
    uTime:sceneTime,uFlowTime:sceneFlowTime,uScale:sceneScale, uSelected: { value: -100 }, uHovered: { value: -100 },
    uVolume: { value: volume },uNebulaVolume:{value:nebulaVolume},uNebulaBounds:{value:sculpted.bounds},uNebula:nebulaControl, uCamera: { value: localCamera },uFocus:{value:new THREE.Vector3()},uHasFocus:{value:0},uMotion:sceneMotion
  };
  const starMaterial = rawMaterial(starVertex, starFragment, uniforms, { transparent: true, blending: THREE.AdditiveBlending, depthWrite: false });

  const memoryPosition=m=>new THREE.Vector3(...(m.pos||[0,0,0])).multiply(new THREE.Vector3(1.55,.91,.72));
  let dustGeometry=null,dustPoints=null;
  function rebuildStellarIslands(memories){
    if(dustPoints){root.remove(dustPoints);dustGeometry.dispose();}
    const random=seeded(62411),count=31000;
    const pos=new Float32Array(count*3),col=new Float32Array(count*3),size=new Float32Array(count),alpha=new Float32Array(count),phase=new Float32Array(count),flow=new Float32Array(count);
    const centers=memories.length?memories.map(memoryPosition):[new THREE.Vector3()];
    const normal=()=>{let u=Math.max(.00001,random()),v=random();return Math.sqrt(-2*Math.log(u))*Math.cos(v*Math.PI*2);};
    for(let i=0;i<count;i++){
      let x,y,z,kind=0;
      if(i<14500){const center=centers[i%centers.length];const spread=.035+Math.pow(random(),.75)*.30;x=center.x+normal()*spread;y=center.y+normal()*spread*.66;z=center.z+normal()*spread*.70;kind=1;}
      else if(i<26000){const t=random()*2-1,strand=Math.floor(random()*9),a=t*3.4+strand*.12;x=t*2.45;y=Math.sin(a)*.17+t*.12+normal()*.022;z=Math.cos(t*3.1+strand*.1)*.18+normal()*.075;kind=2;}
      else{x=(random()-.5)*9;y=(random()-.5)*5.8;z=(random()-.5)*4;}
      pos.set([x,y,z],i*3);const warm=random();let color=kind===2?[.40,.71,.77]:warm>.72?[1.0,.77,.47]:[.53,.78,.90];
      const luminosity=.65+random()*.35;col.set(color.map(c=>c*luminosity),i*3);const bright=random();size[i]=1.5+Math.pow(bright,9)*(kind===1?10:7);alpha[i]=kind===0?.045+Math.pow(bright,5)*.55:kind===1?.09+Math.pow(bright,3)*1.05:.07+Math.pow(bright,4)*.42;phase[i]=random()*Math.PI*2;flow[i]=kind===0?.15:1;
    }
    dustGeometry=new THREE.BufferGeometry();
    for(const [name,array,itemSize] of [['position',pos,3],['color',col,3],['aSize',size,1],['aAlpha',alpha,1],['aPhase',phase,1],['aFlow',flow,1]])dustGeometry.setAttribute(name,new THREE.BufferAttribute(array,itemSize));
    dustGeometry.setAttribute('aBirth',new THREE.BufferAttribute(new Float32Array(count).fill(-1000),1));dustGeometry.setAttribute('aIndex',new THREE.BufferAttribute(new Float32Array(count).fill(-1000),1));
    dustPoints=new THREE.Points(dustGeometry,starMaterial);dustPoints.frustumCulled=false;root.add(dustPoints);
    const grouped=new Map();for(const m of memories){const name=m.category||'未分类';if(!grouped.has(name))grouped.set(name,{name,p:new THREE.Vector3(),n:0});const g=grouped.get(name);g.p.add(memoryPosition(m));g.n++;}
    projectedClusters=[...grouped.values()].map(c=>({...c,p:c.p.divideScalar(c.n)}));
  }

  const screenCamera = new THREE.OrthographicCamera(-1,1,1,-1,0,1);
  const quadGeometry = new THREE.PlaneGeometry(2,2);
  const nebulaUniforms = {uVolume:{value:volume},uNebulaVolume:{value:nebulaVolume},uNebulaBounds:{value:sculpted.bounds},uNebula:nebulaControl,uTime:uniforms.uTime,uFlowTime:sceneFlowTime,uMotion:uniforms.uMotion,uCamera:{value:localCamera},uInverseRotation:{value:inverseRotation},uAspect:{value:1},uTanFov:{value:Math.tan(THREE.MathUtils.degToRad(20))}};
  const volumeMaterial = rawMaterial(screenVertex,nebulaFragment,nebulaUniforms,{depthTest:false,depthWrite:false});
  const volumeScene = new THREE.Scene();volumeScene.add(new THREE.Mesh(quadGeometry,volumeMaterial));
  const target = () => new THREE.WebGLRenderTarget(1,1,{type:THREE.HalfFloatType,minFilter:THREE.LinearFilter,magFilter:THREE.LinearFilter,depthBuffer:false});
  const nebulaTarget=target(),sceneTarget=target(),bloomA=target(),bloomB=target();
  sceneTarget.depthBuffer=true;
  const deepSky=createDeepSky({intensity:nebulaControl,cloudTexture:nebulaTarget.texture});scene.add(deepSky.group);
  const backgroundMaterial=rawMaterial(screenVertex,landscapeFragment,{uClouds:{value:nebulaTarget.texture},uNebula:nebulaControl,uSkyInverseRotation:{value:deepSky.inverse},uAspect:nebulaUniforms.uAspect,uTanFov:nebulaUniforms.uTanFov},{depthTest:false,depthWrite:false});
  const passScene=new THREE.Scene(),quad=new THREE.Mesh(quadGeometry,backgroundMaterial);passScene.add(quad);
  const blurMaterial=rawMaterial(screenVertex,blurFragment,{uTexture:{value:null},uDirection:{value:new THREE.Vector2()},uThreshold:{value:0}},{depthTest:false,depthWrite:false});
  const compositeMaterial=rawMaterial(screenVertex,compositeFragment,{uScene:{value:sceneTarget.texture},uBloom:{value:bloomB.texture},uNebula:nebulaControl},{depthTest:false,depthWrite:false});

  function rebuildGraph(memories,edges) {
    if(memoryPoints){root.remove(memoryPoints);memoryGeometry.dispose();}
    if(lines){root.remove(lines);lineGeometry.dispose();lines.material.dispose();}
    memoryCount=memories.length;rebuildStellarIslands(memories);
    const pos=new Float32Array(memoryCount*3),col=new Float32Array(memoryCount*3),alpha=new Float32Array(memoryCount),size=new Float32Array(memoryCount),birth=new Float32Array(memoryCount),index=new Float32Array(memoryCount);
    const categories=[...new Set(memories.map(m=>m.category))],palette=['#dce9df','#accdd8','#f1cc91','#b2d6c9','#ccc0e0','#ddb9a2','#aec7dc'];
    for(let i=0;i<memoryCount;i++){
      const m=memories[i];pos.set(memoryPosition(m).toArray(),i*3);
      const c=new THREE.Color(palette[categories.indexOf(m.category)%palette.length]);col.set([c.r,c.g,c.b],i*3);
      alpha[i]=2.8;size[i]=m.highlight?38:29;birth[i]=m.flashAt?m.flashAt/1000:-1000;index[i]=i;
    }
    memoryGeometry=new THREE.BufferGeometry();
    for(const [name,array,itemSize] of [['position',pos,3],['color',col,3],['aSize',size,1],['aAlpha',alpha,1],['aBirth',birth,1],['aIndex',index,1],['aPhase',new Float32Array(memoryCount),1],['aFlow',new Float32Array(memoryCount),1]]) memoryGeometry.setAttribute(name,new THREE.BufferAttribute(array,itemSize));
    memoryPoints=new THREE.Points(memoryGeometry,starMaterial);memoryPoints.frustumCulled=false;memoryPoints.renderOrder=3;root.add(memoryPoints);
    const vertices=[],progress=[];
    for(const e of edges){const a=memoryPosition(memories[e.a]),b=memoryPosition(memories[e.b]),mid=a.clone().add(b).multiplyScalar(.56);const curve=new THREE.QuadraticBezierCurve3(a,mid,b);for(let s=0;s<18;s++){vertices.push(...curve.getPoint(s/18).toArray(),...curve.getPoint((s+1)/18).toArray());progress.push(s/18,(s+1)/18);}}
    lineGeometry=new THREE.BufferGeometry();lineGeometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));lineGeometry.setAttribute('aAlpha',new THREE.BufferAttribute(new Float32Array(vertices.length/3),1));lineGeometry.setAttribute('aProgress',new THREE.Float32BufferAttribute(progress,1));lineGeometry.setAttribute('aHot',new THREE.BufferAttribute(new Float32Array(vertices.length/3),1));
    lines=new THREE.LineSegments(lineGeometry,rawMaterial(lineVertex,lineFragment,{uTime:uniforms.uTime,uMotion:uniforms.uMotion}, {transparent:true,depthWrite:false}));lines.renderOrder=2;root.add(lines);
    lastEdges=edges;lastVisibility='';lastSelected='';
  }

  function resize() {
    const rect=canvas.getBoundingClientRect();width=Math.max(1,rect.width);height=Math.max(1,rect.height);
    renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();nebulaUniforms.uAspect.value=camera.aspect;
    const w=Math.round(width*pixelRatio),h=Math.round(height*pixelRatio);
    sceneTarget.setSize(w,h);nebulaTarget.setSize(Math.max(1,Math.round(w*.55)),Math.max(1,Math.round(h*.55)));bloomA.setSize(Math.max(1,Math.round(w*.5)),Math.max(1,Math.round(h*.5)));bloomB.setSize(bloomA.width,bloomA.height);
    uniforms.uScale.value=3.4*pixelRatio*(height/850);deepSky.resize(w,h,pixelRatio);
  }

  function render({memories,edges,rotationX,rotationY,zoom,hovered,pinned,active,now,flashIds,nebulaEnabled=false}) {
    const dt=previousFrame?Math.min(.1,(now-previousFrame)/1000):0;previousFrame=now;
    const targetNebula=nebulaEnabled?1:0;
    if(nebulaEnabled&&!wasNebulaEnabled)sceneFlowTime.value=0;
    if(nebulaEnabled)sceneFlowTime.value+=dt;
    wasNebulaEnabled=nebulaEnabled;
    nebulaBlend=reducedMotion?targetNebula:nebulaBlend+(targetNebula-nebulaBlend)*(1-Math.exp(-dt*3.6));
    if(Math.abs(targetNebula-nebulaBlend)<.001)nebulaBlend=targetNebula;
    nebulaControl.value=nebulaBlend;
    sculpted.photons.visible=sculpted.filaments.visible=nebulaBlend>.001;deepSky.group.visible=nebulaBlend>.001;
    if(edges!==lastEdges||memories.length!==memoryCount)rebuildGraph(memories,edges);
    deepSky.orient(rotationX,rotationY);
    root.rotation.set(-rotationX*.58,-rotationY*.58,0,'YXZ');root.updateMatrixWorld(true);
    const fitDistance=Math.max(4.6,1.95/(Math.tan(THREE.MathUtils.degToRad(20))*camera.aspect));
    camera.position.z=(3.4+(fitDistance-3.4)*nebulaBlend)/zoom;camera.updateMatrixWorld(true);
    inverseMatrix.copy(root.matrixWorld).invert();inverseRotation.setFromMatrix4(inverseMatrix);
    localCamera.copy(camera.position).applyMatrix4(inverseMatrix);
    uniforms.uTime.value=now/1000;uniforms.uSelected.value=pinned??-100;uniforms.uHovered.value=hovered??-100;const focusIndex=hovered??pinned;uniforms.uHasFocus.value=focusIndex===null?0:1;if(focusIndex!==null&&memories[focusIndex])uniforms.uFocus.value.copy(memoryPosition(memories[focusIndex]));
    const visibility=memories.map(m=>active(m)?'1':'0').join(''),selected=`${hovered}:${pinned}`;
    if(focusIndex===null){const visible=memories.filter(active);if(visible.length&&visible.length<memories.length){uniforms.uFocus.value.set(0,0,0);for(const m of visible)uniforms.uFocus.value.add(memoryPosition(m));uniforms.uFocus.value.divideScalar(visible.length);uniforms.uHasFocus.value=1;}}
    if(visibility!==lastVisibility||selected!==lastSelected){
      const a=memoryGeometry.getAttribute('aAlpha'),neighbors=new Set([hovered,pinned]);for(const e of edges)if(neighbors.has(e.a)||neighbors.has(e.b)){if(e.a===hovered||e.a===pinned)neighbors.add(e.b);if(e.b===hovered||e.b===pinned)neighbors.add(e.a);}for(let i=0;i<memoryCount;i++)a.array[i]=visibility[i]==='1'?(focusIndex===null||neighbors.has(i)?2.8:.32):0;a.needsUpdate=true;
      const la=lineGeometry.getAttribute('aAlpha'),ha=lineGeometry.getAttribute('aHot');for(let i=0;i<edges.length;i++){const e=edges[i],show=visibility[e.a]==='1'&&visibility[e.b]==='1',hot=[hovered,pinned].some(v=>v!==null&&(v===e.a||v===e.b)),opacity=show?(hot?.58:.012):0;la.array.fill(opacity,i*36,(i+1)*36);ha.array.fill(hot?1:0,i*36,(i+1)*36);}la.needsUpdate=true;ha.needsUpdate=true;lastVisibility=visibility;lastSelected=selected;
    }
    for(const id of flashIds){const m=memories.find(v=>v.id===id);if(!m||now-m.flashAt>2300)flashIds.delete(id);}
    renderer.setRenderTarget(nebulaTarget);renderer.render(volumeScene,screenCamera);
    renderer.setRenderTarget(sceneTarget);quad.material=backgroundMaterial;renderer.render(passScene,screenCamera);
    renderer.autoClear=false;renderer.render(scene,camera);renderer.autoClear=true;
    quad.material=blurMaterial;blurMaterial.uniforms.uTexture.value=sceneTarget.texture;blurMaterial.uniforms.uDirection.value.set(1.7/sceneTarget.width,0);blurMaterial.uniforms.uThreshold.value=1;
    renderer.setRenderTarget(bloomA);renderer.render(passScene,screenCamera);
    blurMaterial.uniforms.uTexture.value=bloomA.texture;blurMaterial.uniforms.uDirection.value.set(0,1.7/bloomA.height);blurMaterial.uniforms.uThreshold.value=0;
    renderer.setRenderTarget(bloomB);renderer.render(passScene,screenCamera);
    quad.material=compositeMaterial;renderer.setRenderTarget(null);renderer.render(passScene,screenCamera);
    const labelLayer=document.getElementById('clusterLabels');
    if(labelLayer){
      if(labelLayer.childElementCount!==projectedClusters.length)labelLayer.replaceChildren(...projectedClusters.map(c=>{const label=document.createElement('div');label.className='space-label';label.textContent=c.name;label.append(document.createElement('i'));return label;}));
      const placed=[];
      projectedClusters.forEach((c,i)=>{
        scratch.copy(c.p).applyMatrix4(root.matrixWorld).project(camera);
        const x=(scratch.x*.5+.5)*width;let y=(-.5*scratch.y+.5)*height+32;
        for(let attempt=0;attempt<7;attempt++){if(!placed.some(p=>Math.abs(x-p.x)<104&&Math.abs(y-p.y)<23))break;y+=24;}
        placed.push({x,y});const label=labelLayer.children[i];if(label.firstChild.nodeValue!==c.name)label.firstChild.nodeValue=c.name;label.style.left=`${x}px`;label.style.top=`${y}px`;label.style.opacity=(scratch.z<1&&focusIndex===null&&memories.some(m=>m.category===c.name&&active(m)))?'.60':'.10';
      });
    }
    return memories.map((m,i)=>{scratch.copy(memoryPosition(m)).applyMatrix4(root.matrixWorld).project(camera);return{m,i,x:(scratch.x*.5+.5)*width,y:(-.5*scratch.y+.5)*height,z:scratch.z,visible:visibility[i]==='1'&&scratch.z<1};});
  }
  function dispose(){
    renderer.dispose();
    renderer.forceContextLoss();
  }
  return {resize,render,dispose};
}
