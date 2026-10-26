import * as THREE from './vendor/three.module.min.js';

export const NEBULA_BOUNDS = [2.2, 1.8, 2.0];
const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));

// This density is evaluated throughout XYZ. No image, UV surface, projection
// compensation, or extruded sheet participates in the shape or color.
export function buildRosetteField(resolution=160){
  const n=resolution,total=n*n*n,density=new Float32Array(total),dustFraction=new Float32Array(total);
  let seed=771237;const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
  const lattice=Float32Array.from({length:64**3},random);
  function noise(x,y,z){
    const ix=Math.floor(x),iy=Math.floor(y),iz=Math.floor(z);let u=x-ix,v=y-iy,w=z-iz;
    u=u*u*(3-2*u);v=v*v*(3-2*v);w=w*w*(3-2*w);
    const x0=ix&63,x1=(ix+1)&63,y0=(iy&63)*64,y1=((iy+1)&63)*64,z0=(iz&63)*4096,z1=((iz+1)&63)*4096;
    const a=lattice[z0+y0+x0]*(1-u)+lattice[z0+y0+x1]*u,b=lattice[z0+y1+x0]*(1-u)+lattice[z0+y1+x1]*u;
    const c=lattice[z1+y0+x0]*(1-u)+lattice[z1+y0+x1]*u,d=lattice[z1+y1+x0]*(1-u)+lattice[z1+y1+x1]*u;
    return(a*(1-v)+b*v)*(1-w)+(c*(1-v)+d*v)*w;
  }
  function field(x,y,z){
    const qx=(x-.03)/1.12+(noise(x*1.5+13,y*1.5,z*1.5)-.5)*.29;
    const qy=(y-.10)/1.00+(noise(x*1.5,y*1.5+19,z*1.5)-.5)*.25;
    const qz=z/1.14+(noise(x*1.5,y*1.5,z*1.5+27)-.5)*.29;
    const r=Math.hypot(qx,qy,qz),az=Math.atan2(qy,qx),el=Math.atan2(qz,Math.hypot(qx,qy));
    const shellRadius=.98+.055*Math.sin(az*5+el*2)+.045*(noise(qx*3+7,qy*3,qz*3)-.5);
    const shell=Math.exp(-(((r-shellRadius)/.20)**2));
    // Irregular openings expose the inner star cluster, while the side walls
    // and offset front/back plumes retain a full, three-dimensional body.
    const frontOpening=Math.exp(-((qx+.04)**2/.24+(qy-.03)**2/.22))*clamp((Math.abs(qz)/(r+.001)-.40)/.50);
    const sideOpening=Math.exp(-((qz-.12)**2/.105+(qy+.08)**2/.13))*clamp((Math.abs(qx)/(r+.001)-.65)/.30)*.50;
    const f=noise(qx*2.7,qy*2.7,qz*2.7)*.49+noise(qx*5.6+4,qy*5.6,qz*5.6)*.27+noise(qx*12.1,qy*12.1+8,qz*12.1)*.16+noise(qx*25,qy*25,qz*25+11)*.08;
    const lane=Math.abs(noise(qx*4.8+21,qy*4.8-7,qz*4.8)-.5);
    const dust=1-.83*Math.exp(-((lane/.035)**2));
    const turbulence=Math.pow(clamp((f-.435)*4.5),1.75);
    const plume=Math.exp(-((x+.78)**2/.21+(y+.64)**2/.22+(z-.25)**2/.30))*.22+Math.exp(-((x+1.06)**2/.23+(y+1.02)**2/.28+(z-.42)**2/.25))*.18+Math.exp(-((x+1.30)**2/.25+(y+1.42)**2/.26+(z-.50)**2/.28))*.11;
    const envelope=clamp((2.13-Math.abs(x))/.24)*clamp((1.74-Math.abs(y))/.21)*clamp((1.94-Math.abs(z))/.24);
    const matter=(shell*(1-frontOpening*.97)*(1-sideOpening)+plume)*envelope;
    const gas=matter*turbulence*dust*1.65;
    // Cold branching dust absorbs light without radiating pink emission.
    const dark=matter*Math.exp(-((lane/.052)**2))*.32*clamp((noise(qx*2+8,qy*2-2,qz*2)-.30)*3);
    return [clamp(gas+dark),dark/(gas+dark+.000001)];
  }
  for(let z=0;z<n;z++)for(let y=0;y<n;y++)for(let x=0;x<n;x++){
    const i=(z*n+y)*n+x,v=field((x/(n-1)*2-1)*NEBULA_BOUNDS[0],(y/(n-1)*2-1)*NEBULA_BOUNDS[1],(z/(n-1)*2-1)*NEBULA_BOUNDS[2]);density[i]=v[0];dustFraction[i]=v[1];
  }
  const pixels=new Uint8Array(total*4),cdf=new Float64Array(total);let mass=0;
  const at=(x,y,z)=>density[(clamp(z,0,n-1)*n+clamp(y,0,n-1))*n+clamp(x,0,n-1)];
  for(let z=0;z<n;z++)for(let y=0;y<n;y++)for(let x=0;x<n;x++){
    const i=(z*n+y)*n+x,d=density[i];mass+=d;cdf[i]=mass;if(d<.001)continue;
    const px=(x/(n-1)*2-1)*NEBULA_BOUNDS[0],py=(y/(n-1)*2-1)*NEBULA_BOUNDS[1],pz=(z/(n-1)*2-1)*NEBULA_BOUNDS[2];
    const gx=at(x+1,y,z)-at(x-1,y,z),gy=at(x,y+1,z)-at(x,y-1,z),gz=at(x,y,z+1)-at(x,y,z-1),g=Math.hypot(gx,gy,gz)+.00001;
    const nx=-gx/g,ny=-gy/g,nz=-gz/g;
    const facing=Math.max(0,nx*(-.37)+ny*.56+nz*.74);
    const shadow=Math.exp(-(at(x-2,y+3,z+4)+at(x-4,y+6,z+8)+at(x-7,y+10,z+13))*.88);
    const radius=Math.hypot(px,py-.1,pz),inner=clamp((1.27-radius)*1.9);
    const hot=clamp(g*2.0)*shadow*.24;
    const light=.045+shadow*(.08+.82*facing)+hot;
    const cold=.045*inner/(.35+radius*radius);
    // Predominantly crimson hydrogen emission, with violet inner walls and
    // a blue-white ionizing cluster. Shading is computed from the 3D gradient.
    const c=[(.72+inner*.08)*light+hot,(.002+inner*.008)*light+cold*.13+hot*.16,(.022+inner*.082)*light+cold*.66+hot*.42];
    for(let k=0;k<3;k++)pixels[i*4+k]=clamp(c[k]*Math.pow(1-dustFraction[i],3.4)) *255;
    pixels[i*4+3]=d*255;
  }
  return {resolution:n,density,pixels,cdf,mass,bounds:NEBULA_BOUNDS};
}

// Shared by volume ray marching and stellar extinction, so the moving gas
// and the clouds hiding a star always occupy the same three-dimensional space.
export const nebulaFlowGLSL=`
vec4 gasLayer(vec3 p,float scale){
 vec3 q=p/scale;if(any(greaterThan(abs(q),uNebulaBounds)))return vec4(0.0);
 return texture(uNebulaVolume,q/(uNebulaBounds*2.0)+.5);
}
vec4 movingNebula(vec3 p){
 if(uMotion<.5)return gasLayer(p,1.0);
 float a=fract(uFlowTime/18.0),b=fract(a+.5);
 float wa=pow(sin(a*3.14159265),2.0),wb=1.0-wa;
 float emergence=mix(.10,1.0,smoothstep(0.0,3.4,uFlowTime));
 vec4 first=gasLayer(p,(.20+a)*emergence),second=gasLayer(p,(.20+b)*emergence);
 float da=first.a*wa,db=second.a*wb,d=da+db;
 vec3 color=(first.rgb*da+second.rgb*db)/max(.00001,d);
 float revealRadius=mix(.03,2.8,pow(clamp(uFlowTime/3.4,0.0,1.0),.72));
 d*=1.0-smoothstep(revealRadius-.13,revealRadius+.13,length(p));
 return vec4(color,d);
}`;

const vertex=`precision highp float;in vec3 position;in vec3 color;in float aSize;in float aOpacity;in float aPhase;in float aStar;
uniform mat4 modelViewMatrix;uniform mat4 projectionMatrix;uniform float uScale;uniform float uTime;uniform float uMotion;uniform float uFlowTime;
out vec3 vColor;out vec3 vLocal;out float vOpacity;out float vStar;
void main(){
 float age=fract(uFlowTime/9.0+aPhase/6.2831853),growth=.10+age*1.15;
 float fade=smoothstep(0.0,.12,age)*(1.0-smoothstep(.78,1.0,age));
 float emergence=mix(.10,1.0,smoothstep(0.0,3.4,uFlowTime));
 vec3 p=position*mix(1.0,growth*emergence,(1.0-aStar)*uMotion);
 p+=vec3(sin(aPhase+age*4.0),cos(aPhase*.7+age*3.0),sin(aPhase*1.3+age*3.5))*.014*sin(age*3.14159265)*uMotion*(1.0-aStar);
vec4 mv=modelViewMatrix*vec4(p,1.0);gl_Position=projectionMatrix*mv;float pixels=aSize*uScale/(-mv.z);gl_PointSize=clamp(pixels,mix(.5,1.8,aStar),40.0);vColor=color;vLocal=p;vOpacity=aOpacity*mix(min(1.0,pixels*pixels),1.0,aStar)*mix(1.0,fade,uMotion*(1.0-aStar));vStar=aStar;}`;
const fragment=`precision highp float;precision highp sampler3D;in vec3 vColor;in vec3 vLocal;in float vOpacity;in float vStar;uniform float uNebula;uniform sampler3D uNebulaVolume;uniform vec3 uCamera;uniform vec3 uNebulaBounds;uniform float uMotion;uniform float uFlowTime;out vec4 outColor;
${nebulaFlowGLSL}
void main(){
 vec2 uv=gl_PointCoord*2.0-1.0;float r=dot(uv,uv);if(r>1.0)discard;
 vec3 direction=normalize(uCamera-vLocal);vec3 inv=1.0/(direction+vec3(.000001));
 vec3 a=(-uNebulaBounds*1.2-vLocal)*inv,b=(uNebulaBounds*1.2-vLocal)*inv,lo=min(a,b),hi=max(a,b);
 float entry=max(0.0,max(lo.x,max(lo.y,lo.z))),exit=min(length(uCamera-vLocal),min(hi.x,min(hi.y,hi.z))),stepSize=max(0.0,exit-entry)/12.0,tau=0.0;
 for(int i=0;i<12;i++){vec3 p=vLocal+direction*(entry+(float(i)+.5)*stepSize);tau+=movingNebula(p).a*stepSize*4.2;}
 float profile=mix(exp(-r*7.0),exp(-r*48.0)+exp(-r*6.0)*.055,vStar);
 outColor=vec4(vColor*profile,vOpacity*uNebula*exp(-tau)*mix(exp(-r*3.0),1.0,vStar));
}`;
const lineVertex=`precision highp float;in vec3 position;in vec3 color;in float aOpacity;uniform float uFlowTime;uniform float uMotion;uniform mat4 modelViewMatrix;uniform mat4 projectionMatrix;out vec3 vColor;out float vOpacity;void main(){vColor=color;vOpacity=aOpacity;float expansion=mix(1.0,.6+fract(uFlowTime/18.0)*.6,uMotion);gl_Position=projectionMatrix*modelViewMatrix*vec4(position*expansion,1.0);}`;
const lineFragment=`precision highp float;in vec3 vColor;in float vOpacity;uniform float uNebula;uniform float uFlowTime;uniform float uMotion;out vec4 outColor;void main(){float age=fract(uFlowTime/18.0),fade=smoothstep(0.0,.12,age)*(1.0-smoothstep(.82,1.0,age));outColor=vec4(vColor,vOpacity*uNebula*mix(1.0,fade,uMotion));}`;

export async function createSculptedNebula({intensity,time,flowTime,motion,scale,camera}){
  const data=buildRosetteField(160),n=data.resolution,bounds=new THREE.Vector3(...data.bounds);
  const volume=new THREE.Data3DTexture(data.pixels,n,n,n);volume.format=THREE.RGBAFormat;volume.type=THREE.UnsignedByteType;volume.minFilter=volume.magFilter=THREE.LinearFilter;volume.unpackAlignment=1;volume.needsUpdate=true;
  let seed=77312;const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
  const normal=()=>Math.sqrt(-2*Math.log(Math.max(.00001,random())))*Math.cos(random()*Math.PI*2);
  function chooseCell(){const target=random()*data.mass;let low=0,high=data.cdf.length-1;while(low<high){const mid=(low+high)>>>1;if(data.cdf[mid]<target)low=mid+1;else high=mid;}return low;}
  function world(cell){const x=cell%n,y=Math.floor(cell/n)%n,z=Math.floor(cell/(n*n));return[( (x+random()-.5)/(n-1)*2-1)*bounds.x,((y+random()-.5)/(n-1)*2-1)*bounds.y,((z+random()-.5)/(n-1)*2-1)*bounds.z];}
  function cellAt(p){const x=Math.round((p[0]/bounds.x*.5+.5)*(n-1)),y=Math.round((p[1]/bounds.y*.5+.5)*(n-1)),z=Math.round((p[2]/bounds.z*.5+.5)*(n-1));if(x<1||y<1||z<1||x>n-2||y>n-2||z>n-2)return-1;return(z*n+y)*n+x;}
  const gasCount=60000,fieldStarCount=110000,count=gasCount+fieldStarCount+18,pos=new Float32Array(count*3),col=new Float32Array(count*3),sizes=new Float32Array(count),alpha=new Float32Array(count),phase=new Float32Array(count),stars=new Float32Array(count);
  for(let i=0;i<count;i++){
    if(i>=gasCount+fieldStarCount){pos.set([normal()*.13,normal()*.14+.04,normal()*.20],i*3);col.set([.52,.82,1.4],i*3);sizes[i]=15+random()*17;alpha[i]=1.0+random()*.65;stars[i]=1;}
    else if(i>=gasCount){
      const az=random()*Math.PI*2,vertical=random()*2-1,radius=6+random()*7,ring=Math.sqrt(1-vertical*vertical);
      pos.set([Math.cos(az)*ring*radius,vertical*radius,Math.sin(az)*ring*radius],i*3);
      const tint=random(),c=tint<.20?[1.0,.64,.35]:tint>.70?[.50,.73,1.0]:[.90,.91,1.0];
      col.set(c,i*3);const bright=random();sizes[i]=1.25+Math.pow(bright,12)*11;alpha[i]=.20+Math.pow(bright,4)*.9;stars[i]=1;
    }
    else{const cell=chooseCell();pos.set(world(cell),i*3);for(let c=0;c<3;c++)col[i*3+c]=data.pixels[cell*4+c]/255*.9;sizes[i]=1.35+Math.pow(random(),5)*3.0;alpha[i]=.035+data.density[cell]*.08;}
    phase[i]=random()*Math.PI*2;
  }
  const geometry=new THREE.BufferGeometry();for(const[name,array,size]of[['position',pos,3],['color',col,3],['aSize',sizes,1],['aOpacity',alpha,1],['aPhase',phase,1],['aStar',stars,1]])geometry.setAttribute(name,new THREE.BufferAttribute(array,size));
  const uniforms={uNebula:intensity,uTime:time,uFlowTime:flowTime,uMotion:motion,uScale:scale,uNebulaVolume:{value:volume},uNebulaBounds:{value:bounds},uCamera:camera};
  const material=new THREE.RawShaderMaterial({glslVersion:THREE.GLSL3,vertexShader:vertex,fragmentShader:fragment,uniforms,transparent:true,depthWrite:false,blending:THREE.AdditiveBlending});
  const photons=new THREE.Points(geometry,material);photons.frustumCulled=false;photons.renderOrder=1;
  const fp=[],fc=[],fa=[];
  for(let strand=0;strand<105;strand++){
    let p=world(chooseCell());const phase=random()*6.28;
    for(let j=0;j<32;j++){
      const cell=cellAt(p);if(cell<0||data.density[cell]<.08)break;
      const gx=data.density[cell+1]-data.density[cell-1],gy=data.density[cell+n]-data.density[cell-n],gz=data.density[cell+n*n]-data.density[cell-n*n];
      const g=Math.hypot(gx,gy,gz)+1e-7,nx=gx/g,ny=gy/g,nz=gz/g;
      let vx=Math.sin(p[1]*4+phase),vy=Math.sin(p[2]*4-phase*.7),vz=Math.cos(p[0]*4+phase*.3);const dot=vx*nx+vy*ny+vz*nz;vx-=dot*nx;vy-=dot*ny;vz-=dot*nz;const v=Math.hypot(vx,vy,vz)+1e-7;
      const next=[p[0]+vx/v*.022,p[1]+vy/v*.022,p[2]+vz/v*.022];if(cellAt(next)<0)break;
      fp.push(...p,...next);const c=[data.pixels[cell*4]/255,data.pixels[cell*4+1]/255,data.pixels[cell*4+2]/255];fc.push(...c,...c);const a=Math.sin(j/32*Math.PI)*.025;fa.push(a,a);p=next;
    }
  }
  const fg=new THREE.BufferGeometry();for(const[name,array,size]of[['position',fp,3],['color',fc,3],['aOpacity',fa,1]])fg.setAttribute(name,new THREE.Float32BufferAttribute(array,size));
  const fm=new THREE.RawShaderMaterial({glslVersion:THREE.GLSL3,vertexShader:lineVertex,fragmentShader:lineFragment,uniforms,transparent:true,depthWrite:false,blending:THREE.AdditiveBlending});
  const filaments=new THREE.LineSegments(fg,fm);filaments.frustumCulled=false;filaments.renderOrder=1;
  return{volume,photons,filaments,bounds,diagnostics:{source:'procedural-xyz-density',grid:n}};
}
