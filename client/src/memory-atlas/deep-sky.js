import * as THREE from './vendor/three.module.min.js';

export const skyBackgroundGLSL = `
float skyHash(vec3 p){p=fract(p*.3183099+vec3(.11,.37,.73));p*=17.0;return fract(p.x*p.y*p.z*(p.x+p.y+p.z));}
float skyNoise(vec3 p){vec3 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);return mix(mix(mix(skyHash(i),skyHash(i+vec3(1,0,0)),f.x),mix(skyHash(i+vec3(0,1,0)),skyHash(i+vec3(1,1,0)),f.x),f.y),mix(mix(skyHash(i+vec3(0,0,1)),skyHash(i+vec3(1,0,1)),f.x),mix(skyHash(i+vec3(0,1,1)),skyHash(i+vec3(1,1,1)),f.x),f.y),f.z);}
vec3 deepSky(vec3 direction){
 vec3 d=uSkyInverseRotation*direction;
 vec3 normal=normalize(vec3(-.48,.70,-.12));
 float latitude=dot(d,normal),n=skyNoise(d*19.0)*.50+skyNoise(d*43.0+7.0)*.28+skyNoise(d*98.0)*.15+skyNoise(d*225.0)*.07;
 float warp=(skyNoise(d*8.0+3.0)-.5)*.045;
 float band=exp(-pow((latitude+warp)/.085,2.0));
 float halo=exp(-pow(latitude/.19,2.0));
 float seam=abs(latitude+warp+(skyNoise(d*35.0)-.5)*.035);
 float dust=1.0-.87*exp(-pow(seam/.015,2.0));
 float knots=pow(max(0.0,(n-.25)*1.55),2.4);
 float warm=clamp(.5+dot(d,vec3(-.7,-.3,-.5))*.6,0.0,1.0);
 vec3 color=mix(vec3(.14,.23,.43),vec3(.42,.33,.23),warm);
 return color*(band*knots*dust*.24+halo*.004);
}`;

const vertex=`precision highp float;
in vec3 position;in vec3 color;in float aSize;in float aLight;
uniform mat4 modelViewMatrix;uniform mat4 projectionMatrix;uniform float uPixelScale;
out vec3 vColor;out float vLight;out float vSize;
void main(){vec4 p=modelViewMatrix*vec4(position,1.0);gl_Position=projectionMatrix*p;float size=aSize*uPixelScale/max(.1,-p.z);gl_PointSize=clamp(size,1.3,28.0);vColor=color;vLight=aLight*min(1.0,max(.18,size*size));vSize=gl_PointSize;}`;
const fragment=`precision highp float;
in vec3 vColor;in float vLight;in float vSize;uniform float uIntensity;uniform sampler2D uClouds;uniform vec2 uResolution;out vec4 outColor;
void main(){vec2 p=gl_PointCoord*2.0-1.0;float r=dot(p,p);if(r>1.0)discard;
 float core=exp(-r*mix(3.5,23.0,smoothstep(2.0,6.0,vSize))),halo=exp(-r*5.0)*.07;
 float cloud=texture(uClouds,gl_FragCoord.xy/uResolution).a;
 float transmission=pow(max(0.0,1.0-cloud),1.7);
 outColor=vec4(vColor*(core+halo)*vLight*uIntensity*transmission,1.0);}`;

export function createDeepSky({intensity,cloudTexture}){
 let seed=86731;const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
 const gaussian=()=>Math.sqrt(-2*Math.log(Math.max(1e-6,random())))*Math.cos(random()*Math.PI*2);
 const positions=[],colors=[],sizes=[],lights=[];
 const put=(p,c,size,light)=>{positions.push(...p);colors.push(...c);sizes.push(size);lights.push(light);};
 const normal=new THREE.Vector3(-.48,.70,-.12).normalize(),u=new THREE.Vector3().crossVectors(normal,new THREE.Vector3(0,0,1)).normalize(),v=new THREE.Vector3().crossVectors(normal,u),point=new THREE.Vector3();
 for(let i=0;i<115000;i++){
   const longitude=random()*Math.PI*2,latitude=gaussian()*(random()<.80?.038:.12),radius=27+random()*13;
   point.copy(u).multiplyScalar(Math.cos(longitude)).addScaledVector(v,Math.sin(longitude)).addScaledVector(normal,latitude).normalize().multiplyScalar(radius);
   const dust=1-.88*Math.exp(-(((latitude+.012*Math.sin(longitude*13))/.011)**2));
   const bright=random(),warm=random(),color=warm>.65?[.95,.78,.54]:warm<.17?[.47,.66,1]:[.82,.86,.96];
   put(point.toArray(),color,3+Math.pow(bright,12)*47,(.055+Math.pow(bright,5)*.9)*dust);
 }
 const galaxies=[
   {center:[5.1,3.6,-12],radius:1.72,tilt:.53,angle:-.38,arms:3,count:27000,turns:5.5},
   {center:[-6.6,1.65,-15],radius:1.57,tilt:.24,angle:.49,arms:2,count:22000,turns:6.1},
   {center:[6.4,-3.1,-14],radius:1.82,tilt:.13,angle:-.40,arms:2,count:24000,turns:5.3},
   {center:[-3.5,4.7,-19],radius:.49,tilt:.66,angle:.8,arms:2,count:4500,turns:4.9},
   {center:[8.8,.3,-20],radius:.50,tilt:.32,angle:-.7,arms:2,count:4000,turns:5.2}
 ];
 for(const g of galaxies){
   const ca=Math.cos(g.angle),sa=Math.sin(g.angle);
   for(let i=0;i<g.count;i++){
     const bulge=random()<.27,interarm=random()<.30,r=bulge?Math.abs(gaussian())*.11:Math.min(1.3,-Math.log(1-random()*.985)*.31);
     const arm=Math.floor(random()*g.arms),theta=bulge||interarm?random()*Math.PI*2:arm*Math.PI*2/g.arms+Math.log(.16+r)*g.turns+gaussian()*(.13+.35*r);
     const x=(Math.cos(theta)*r+gaussian()*.012)*g.radius,y=(Math.sin(theta)*r+gaussian()*.012)*g.radius;
     const thickness=gaussian()*g.radius*(bulge?.065:.013),discY=y*g.tilt+thickness;
     const p=[g.center[0]+x*ca-discY*sa,g.center[1]+x*sa+discY*ca,g.center[2]+y*Math.sqrt(1-g.tilt*g.tilt)];
     const knot=(!bulge&&random()<.06),color=bulge?[1.0,.79,.48]:knot?[.92,.24,.42]:[.45+.30*(1-r),.63+.15*(1-r),1.0];
     const edgeDust=g.tilt<.3&&!bulge?(.25+.75*Math.min(1,Math.abs(y)/(.16*g.radius))):1;
     const bright=random();put(p,color,bulge?4+random()*8:2.4+Math.pow(bright,12)*24,(bulge?.06+random()*.13:.035+Math.pow(bright,4)*.21)*edgeDust*(interarm?.46:1));
     if(i%14===0){put(p,color,45+random()*65,(bulge?.009:.0035)*edgeDust);}
   }
 }
 const geometry=new THREE.BufferGeometry();
 for(const[name,array,size]of[['position',positions,3],['color',colors,3],['aSize',sizes,1],['aLight',lights,1]])geometry.setAttribute(name,new THREE.Float32BufferAttribute(array,size));
 const resolution=new THREE.Vector2(1,1),pixelScale={value:5};
 const material=new THREE.RawShaderMaterial({glslVersion:THREE.GLSL3,vertexShader:vertex,fragmentShader:fragment,uniforms:{uIntensity:intensity,uClouds:{value:cloudTexture},uResolution:{value:resolution},uPixelScale:pixelScale},transparent:true,blending:THREE.AdditiveBlending,depthTest:false,depthWrite:false});
 const group=new THREE.Group(),stars=new THREE.Points(geometry,material);stars.frustumCulled=false;stars.renderOrder=0;group.add(stars);
 const inverse=new THREE.Matrix3(),matrix=new THREE.Matrix4();
 return {group,inverse,resize(width,height,ratio){resolution.set(width,height);pixelScale.value=5*ratio*(height/ratio/850);},orient(x,y){group.rotation.set(-x*.07,-y*.07,0,'YXZ');group.updateMatrixWorld(true);inverse.setFromMatrix4(matrix.copy(group.matrixWorld).invert());}};
}
