import * as THREE from './vendor/three.module.min.js';

const seeded = initial => { let seed = initial; return () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; }; };
const clamp = (x, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, x));

// A CPU-generated density field is uploaded once. The GPU integrates light
// through the actual 3D field; rotating it never exposes a flat cloud billboard.
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
    const lobe = (a, b, c, sx, sy, sz) => Math.exp(-((px-a)**2/sx**2 + (py-b)**2/sy**2 + (pz-c)**2/sz**2) * 1.4);
    const teal = lobe(-.20, -.08, .14, .40, .64, .36);
    const violet = lobe(.31, .22, -.14, .36, .43, .40);
    const bridge = Math.exp(-((py - px * .55 + Math.sin(pz * 5) * .10) ** 2 / .035 + (px * .58 + pz * .82) ** 2 / .32)) * .55;
    const envelope = clamp((1.02 - Math.hypot(px, py, pz)) / .23);
    const density = clamp(Math.pow(Math.max(0, f - .42) * 4.0, 1.7) * (teal + violet * .84 + bridge) * envelope);
    const purple = clamp(violet / (teal + violet + .08));
    const warm = lobe(-.04, .18, .22, .19, .17, .22) * .75;
    const color = [(.08 * (1-purple) + .37 * purple), (.55 * (1-purple) + .20 * purple), (.66 * (1-purple) + .65 * purple)];
    const gold = [1.0, .63, .28];
    const i = ((z * n + y) * n + x) * 4;
    for (let c = 0; c < 3; c++) data[i+c] = clamp(color[c] * (1-warm) + gold[c] * warm) * 255;
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
vec2 intersectSphere(vec3 o,vec3 d){float b=dot(o,d);float c=dot(o,o)-1.065*1.065;float h=b*b-c;if(h<0.0)return vec2(-1.0);h=sqrt(h);return vec2(-b-h,-b+h);}
void main(){
  vec2 xy=vUv*2.0-1.0;
  vec3 direction=normalize(uInverseRotation*vec3(xy.x*uAspect*uTanFov,xy.y*uTanFov,-1.0));
  vec2 bounds=intersectSphere(uCamera,direction);
  vec3 background=vec3(.0018,.0031,.0058);
  if(bounds.y<0.0){outColor=vec4(background,1.0);return;}
  float stepSize=(bounds.y-max(0.0,bounds.x))/56.0;
  float jitter=fract(sin(dot(gl_FragCoord.xy,vec2(12.9898,78.233)))*43758.5453);
  vec3 p=uCamera+direction*(max(0.0,bounds.x)+stepSize*jitter);
  vec4 light=vec4(0.0);
  vec3 lightDir=normalize(vec3(-.35,.65,.8));
  for(int i=0;i<56;i++){
    vec4 field=texture(uVolume,p*.5+.5);
    float density=field.a;
    if(density>.006){
      float shadow=exp(-texture(uVolume,(p+lightDir*.14)*.5+.5).a*2.3);
      float alpha=1.0-exp(-density*stepSize*2.8);
      float starLight=.07/(.035+dot(p-vec3(-.04,.18,.22),p-vec3(-.04,.18,.22)));
      vec3 scattering=field.rgb*(.20+shadow*.60+starLight*.25);
      light.rgb+=(1.0-light.a)*scattering*alpha;
      light.a+=(1.0-light.a)*alpha;
    }
    p+=direction*stepSize;
    if(light.a>.97)break;
  }
  outColor=vec4(light.rgb+background*(1.0-light.a),1.0);
}`;

const starVertex = `precision highp float;
in vec3 position; in vec3 color; in float aSize; in float aAlpha;
in float aPhase; in float aBirth; in float aIndex;
uniform mat4 modelViewMatrix; uniform mat4 projectionMatrix;
uniform float uTime; uniform float uScale; uniform float uSelected; uniform float uHovered;
out vec3 vColor; out vec3 vLocal; out float vAlpha; out float vBright; out float vFlash;
void main(){
 vec4 mv=modelViewMatrix*vec4(position,1.0);
 float selected=(abs(aIndex-uSelected)<.1||abs(aIndex-uHovered)<.1)?1.0:0.0;
 float flash=clamp(1.0-(uTime-aBirth)/2.3,0.0,1.0);
 float twinkle=.88+.12*sin(uTime*.7+aPhase);
 gl_Position=projectionMatrix*mv;
 gl_PointSize=clamp(aSize*uScale/(-mv.z)*(1.0+selected*.4+flash*1.1),1.0,80.0);
 if(aAlpha<.001)gl_PointSize=0.0;
 vColor=color;vLocal=position;vAlpha=aAlpha*twinkle*(1.0+selected*.65+flash*2.0);vBright=step(9.0,aSize);vFlash=flash;
}`;

const starFragment = `precision highp float; precision highp sampler3D;
in vec3 vColor; in vec3 vLocal; in float vAlpha; in float vBright; in float vFlash;
uniform sampler3D uVolume; uniform vec3 uCamera;
out vec4 outColor;
void main(){
 vec2 uv=gl_PointCoord*2.0-1.0;float r2=dot(uv,uv);if(r2>1.0||vAlpha<.001)discard;
 float core=exp(-r2*55.0),halo=exp(-r2*9.0)*.10;
 float spikes=(exp(-abs(uv.x)*80.0-abs(uv.y)*5.0)+exp(-abs(uv.y)*80.0-abs(uv.x)*5.0))*.045*vBright;
 vec3 ray=normalize(uCamera-vLocal),sampleP=vLocal;float extinction=0.0;
 for(int i=0;i<7;i++){sampleP+=ray*.17;if(length(sampleP)>1.03)break;extinction+=texture(uVolume,sampleP*.5+.5).a*.17*2.1;}
 float transmission=exp(-extinction);
 vec3 spectrum=mix(vColor,vec3(1.0,.86,.60),vFlash*.7);
 vec3 radiance=(spectrum*(halo+spikes)*1.3+mix(spectrum,vec3(1.0),.65)*core)*vAlpha*transmission;
 outColor=vec4(radiance,1.0);
}`;

const lineVertex = `precision highp float; in vec3 position; in float aAlpha;
uniform mat4 modelViewMatrix; uniform mat4 projectionMatrix; out float vAlpha;
void main(){vAlpha=aAlpha;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}`;
const lineFragment = `precision highp float;in float vAlpha;out vec4 outColor;
void main(){outColor=vec4(.24,.67,.74,vAlpha);}`;

const blurFragment = `precision highp float; in vec2 vUv;out vec4 outColor;
uniform sampler2D uTexture;uniform vec2 uDirection;uniform float uThreshold;
vec3 sampleLight(vec2 p){vec3 c=texture(uTexture,p).rgb;return c*mix(1.0,smoothstep(.35,1.25,max(c.r,max(c.g,c.b))),uThreshold);}
void main(){vec3 c=sampleLight(vUv)*.227027;c+=(sampleLight(vUv+uDirection*1.384615)+sampleLight(vUv-uDirection*1.384615))*.316216;c+=(sampleLight(vUv+uDirection*3.230769)+sampleLight(vUv-uDirection*3.230769))*.070270;outColor=vec4(c,1.0);}`;
const compositeFragment = `precision highp float;in vec2 vUv;out vec4 outColor;
uniform sampler2D uScene;uniform sampler2D uBloom;
void main(){vec3 x=(texture(uScene,vUv).rgb+texture(uBloom,vUv).rgb*.65)*1.05;
vec3 tone=clamp((x*(2.51*x+.03))/(x*(2.43*x+.59)+.14),0.0,1.0);
outColor=vec4(pow(tone,vec3(1.0/2.2)),1.0);}`;

function rawMaterial(vertexShader, fragmentShader, uniforms, options = {}) {
  return new THREE.RawShaderMaterial({ glslVersion: THREE.GLSL3, vertexShader, fragmentShader, uniforms, ...options });
}

export function createGalaxyRenderer(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false, powerPreference: 'high-performance' });
  renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
  renderer.toneMapping = THREE.NoToneMapping;
  renderer.setClearColor(0x02040a, 1);
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 1.5);
  renderer.setPixelRatio(pixelRatio);
  const camera = new THREE.PerspectiveCamera(40, 1, .1, 30);
  camera.position.set(0, 0, 3.4);
  const scene = new THREE.Scene(), root = new THREE.Group();
  root.rotation.order = 'YXZ';
  scene.add(root);
  const volume = createNebulaVolume();
  const localCamera = new THREE.Vector3(), inverseRotation = new THREE.Matrix3();
  const inverseMatrix = new THREE.Matrix4(), scratch = new THREE.Vector3();
  let width = 1, height = 1, lastEdges = null, lastVisibility = '', lastSelected = '', memoryCount = 0;
  let memoryGeometry, memoryPoints, lineGeometry, lines;
  const uniforms = {
    uTime: { value: 0 }, uScale: { value: 3.4 }, uSelected: { value: -100 }, uHovered: { value: -100 },
    uVolume: { value: volume }, uCamera: { value: localCamera }
  };
  const starMaterial = rawMaterial(starVertex, starFragment, uniforms, { transparent: true, blending: THREE.AdditiveBlending, depthWrite: false });

  const random = seeded(71428), count = 23500;
  const positions = new Float32Array(count*3), spectra = new Float32Array(count*3);
  const sizes = new Float32Array(count), alphas = new Float32Array(count), phases = new Float32Array(count);
  for (let i=0;i<count;i++) {
    const angle=random()*Math.PI*2, y=random()*2-1;
    let r=i<5900?.94+random()*.08:Math.cbrt(random())*1.01;
    let x=Math.sqrt(1-y*y)*Math.cos(angle)*r, py=y*r, z=Math.sqrt(1-y*y)*Math.sin(angle)*r;
    if(i>17200){do{x=random()*2-1;z=random()*2-1;py=x*.31+(random()+random()+random()-1.5)*.25;}while(x*x+py*py+z*z>1.01);}
    positions.set([x,py,z],i*3);
    const type=random(), color=type>.965?[1,.69,.36]:type>.86?[.32,.79,1]:[.71+random()*.20,.80+random()*.15,1];
    spectra.set(color,i*3);
    const bright=random();sizes[i]=2.0+Math.pow(bright,8)*15;
    alphas[i]=.13+Math.pow(bright,3)*1.65;phases[i]=random()*Math.PI*2;
  }
  const starGeometry = new THREE.BufferGeometry();
  for (const [name,array,size] of [['position',positions,3],['color',spectra,3],['aSize',sizes,1],['aAlpha',alphas,1],['aPhase',phases,1]]) starGeometry.setAttribute(name,new THREE.BufferAttribute(array,size));
  starGeometry.setAttribute('aBirth',new THREE.BufferAttribute(new Float32Array(count).fill(-1000),1));
  starGeometry.setAttribute('aIndex',new THREE.BufferAttribute(new Float32Array(count).fill(-1000),1));
  const stars = new THREE.Points(starGeometry,starMaterial);stars.frustumCulled=false;root.add(stars);

  const screenCamera = new THREE.OrthographicCamera(-1,1,1,-1,0,1);
  const quadGeometry = new THREE.PlaneGeometry(2,2);
  const nebulaUniforms = {uVolume:{value:volume},uCamera:{value:localCamera},uInverseRotation:{value:inverseRotation},uAspect:{value:1},uTanFov:{value:Math.tan(THREE.MathUtils.degToRad(20))}};
  const volumeMaterial = rawMaterial(screenVertex,nebulaFragment,nebulaUniforms,{depthTest:false,depthWrite:false});
  const volumeScene = new THREE.Scene();volumeScene.add(new THREE.Mesh(quadGeometry,volumeMaterial));
  const target = () => new THREE.WebGLRenderTarget(1,1,{type:THREE.HalfFloatType,minFilter:THREE.LinearFilter,magFilter:THREE.LinearFilter,depthBuffer:false});
  const nebulaTarget=target(),sceneTarget=target(),bloomA=target(),bloomB=target();
  sceneTarget.depthBuffer=true;
  const backgroundMaterial=rawMaterial(screenVertex,`precision highp float;in vec2 vUv;out vec4 outColor;uniform sampler2D uTexture;void main(){outColor=texture(uTexture,vUv);}`,{uTexture:{value:nebulaTarget.texture}},{depthTest:false,depthWrite:false});
  const passScene=new THREE.Scene(),quad=new THREE.Mesh(quadGeometry,backgroundMaterial);passScene.add(quad);
  const blurMaterial=rawMaterial(screenVertex,blurFragment,{uTexture:{value:null},uDirection:{value:new THREE.Vector2()},uThreshold:{value:0}},{depthTest:false,depthWrite:false});
  const compositeMaterial=rawMaterial(screenVertex,compositeFragment,{uScene:{value:sceneTarget.texture},uBloom:{value:bloomB.texture}},{depthTest:false,depthWrite:false});

  function rebuildGraph(memories,edges) {
    if(memoryPoints){root.remove(memoryPoints);memoryGeometry.dispose();}
    if(lines){root.remove(lines);lineGeometry.dispose();lines.material.dispose();}
    memoryCount=memories.length;
    const pos=new Float32Array(memoryCount*3),col=new Float32Array(memoryCount*3),alpha=new Float32Array(memoryCount),size=new Float32Array(memoryCount),birth=new Float32Array(memoryCount),index=new Float32Array(memoryCount);
    const categories=[...new Set(memories.map(m=>m.category))],palette=['#78dfe0','#91baff','#e9b97b','#83d9b0','#bf9bf2','#f08d9a','#74c8ee'];
    for(let i=0;i<memoryCount;i++){
      const m=memories[i];pos.set((m.pos||[0,0,0]).map(x=>x*.82),i*3);
      const c=new THREE.Color(palette[categories.indexOf(m.category)%palette.length]);col.set([c.r,c.g,c.b],i*3);
      alpha[i]=2.4;size[i]=m.highlight?32:25;birth[i]=m.flashAt?m.flashAt/1000:-1000;index[i]=i;
    }
    memoryGeometry=new THREE.BufferGeometry();
    for(const [name,array,itemSize] of [['position',pos,3],['color',col,3],['aSize',size,1],['aAlpha',alpha,1],['aBirth',birth,1],['aIndex',index,1],['aPhase',new Float32Array(memoryCount),1]]) memoryGeometry.setAttribute(name,new THREE.BufferAttribute(array,itemSize));
    memoryPoints=new THREE.Points(memoryGeometry,starMaterial);memoryPoints.frustumCulled=false;memoryPoints.renderOrder=3;root.add(memoryPoints);
    const vertices=[];
    for(const e of edges){const a=new THREE.Vector3(...memories[e.a].pos).multiplyScalar(.82),b=new THREE.Vector3(...memories[e.b].pos).multiplyScalar(.82),mid=a.clone().add(b).multiplyScalar(.56);const curve=new THREE.QuadraticBezierCurve3(a,mid,b);for(let s=0;s<18;s++){vertices.push(...curve.getPoint(s/18).toArray(),...curve.getPoint((s+1)/18).toArray());}}
    lineGeometry=new THREE.BufferGeometry();lineGeometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));lineGeometry.setAttribute('aAlpha',new THREE.BufferAttribute(new Float32Array(vertices.length/3),1));
    lines=new THREE.LineSegments(lineGeometry,rawMaterial(lineVertex,lineFragment,{}, {transparent:true,depthWrite:false}));lines.renderOrder=2;root.add(lines);
    lastEdges=edges;lastVisibility='';lastSelected='';
  }

  function resize() {
    const rect=canvas.getBoundingClientRect();width=Math.max(1,rect.width);height=Math.max(1,rect.height);
    renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();nebulaUniforms.uAspect.value=camera.aspect;
    const w=Math.round(width*pixelRatio),h=Math.round(height*pixelRatio);
    sceneTarget.setSize(w,h);nebulaTarget.setSize(Math.max(1,Math.round(w*.55)),Math.max(1,Math.round(h*.55)));bloomA.setSize(Math.max(1,Math.round(w*.5)),Math.max(1,Math.round(h*.5)));bloomB.setSize(bloomA.width,bloomA.height);
    uniforms.uScale.value=3.4*pixelRatio*(height/850);
  }

  function render({memories,edges,rotationX,rotationY,zoom,hovered,pinned,active,now,flashIds}) {
    if(edges!==lastEdges||memories.length!==memoryCount)rebuildGraph(memories,edges);
    root.rotation.set(-rotationX,-rotationY,0,'YXZ');root.updateMatrixWorld(true);
    camera.position.z=3.4/zoom;camera.updateMatrixWorld(true);
    inverseMatrix.copy(root.matrixWorld).invert();inverseRotation.setFromMatrix4(inverseMatrix);
    localCamera.copy(camera.position).applyMatrix4(inverseMatrix);
    uniforms.uTime.value=now/1000;uniforms.uSelected.value=pinned??-100;uniforms.uHovered.value=hovered??-100;
    const visibility=memories.map(m=>active(m)?'1':'0').join(''),selected=`${hovered}:${pinned}`;
    if(visibility!==lastVisibility||selected!==lastSelected){
      const a=memoryGeometry.getAttribute('aAlpha');for(let i=0;i<memoryCount;i++)a.array[i]=visibility[i]==='1'?2.4:0;a.needsUpdate=true;
      const la=lineGeometry.getAttribute('aAlpha');for(let i=0;i<edges.length;i++){const e=edges[i],show=visibility[e.a]==='1'&&visibility[e.b]==='1',hot=[hovered,pinned].some(v=>v!==null&&(v===e.a||v===e.b)),opacity=show?(hot?.58:.15+Math.max(0,e.sim)*.09):0;la.array.fill(opacity,i*36,(i+1)*36);}la.needsUpdate=true;lastVisibility=visibility;lastSelected=selected;
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
    return memories.map((m,i)=>{scratch.fromArray(m.pos||[0,0,0]).multiplyScalar(.82).applyMatrix4(root.matrixWorld).project(camera);return{m,i,x:(scratch.x*.5+.5)*width,y:(-.5*scratch.y+.5)*height,z:scratch.z,visible:visibility[i]==='1'&&scratch.z<1};});
  }
  return {resize,render};
}
