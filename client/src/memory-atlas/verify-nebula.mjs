import {buildRosetteField} from './sculpted-nebula.js';
import assert from 'node:assert/strict';
const start=performance.now(),field=buildRosetteField(Number(process.argv[2])||96),{resolution:n,density:d,bounds:b}=field;
const axes=[new Float64Array(n),new Float64Array(n),new Float64Array(n)],front=new Float64Array(n*n),side=new Float64Array(n*n);
const first=[0,0,0],second=[0,0,0];let mass=0,xy=0,xz=0,yz=0;
for(let z=0;z<n;z++)for(let y=0;y<n;y++)for(let x=0;x<n;x++){
 const v=d[(z*n+y)*n+x],p=[x,y,z].map((q,i)=>(q/(n-1)*2-1)*b[i]);mass+=v;
 for(let i=0;i<3;i++){first[i]+=p[i]*v;second[i]+=p[i]*p[i]*v;axes[i][[x,y,z][i]]+=v;}
 xy+=p[0]*p[1]*v;xz+=p[0]*p[2]*v;yz+=p[1]*p[2]*v;
 front[y*n+x]+=v;side[y*n+z]+=v;
 assert.ok(Number.isFinite(v)&&v>=0&&v<=1);
}
const mean=first.map(x=>x/mass),sigma=second.map((x,i)=>Math.sqrt(x/mass-mean[i]**2));
function span(hist,axis){let sum=0,lo=0,hi=n-1;for(let i=0;i<n;i++){sum+=hist[i];if(sum<mass*.05)lo=i;if(sum<mass*.95)hi=i;}return(hi-lo)/(n-1)*2*b[axis];}
const spans=axes.map(span),maskArea=(array,cell)=>array.filter(v=>v>.08*n).length*cell;
const frontArea=maskArea(front,4*b[0]*b[1]/(n*n)),sideArea=maskArea(side,4*b[2]*b[1]/(n*n));
assert.ok(sigma[2]/sigma[0]>.65,'The gas must have substantial density-weighted Z thickness');
assert.ok(spans[2]/spans[0]>.65,'Central 90% mass must not form an extruded sheet');
assert.ok(sideArea/frontArea>.65,'The 90-degree silhouette must retain a large occupied area');
const moments=[[second[0]/mass-mean[0]**2,xy/mass-mean[0]*mean[1],xz/mass-mean[0]*mean[2]],[xy/mass-mean[0]*mean[1],second[1]/mass-mean[1]**2,yz/mass-mean[1]*mean[2]],[xz/mass-mean[0]*mean[2],yz/mass-mean[1]*mean[2],second[2]/mass-mean[2]**2]];
const eigen= moments.map(r=>r.slice());
for(let k=0;k<20;k++)for(let p=0;p<3;p++)for(let q=p+1;q<3;q++){if(Math.abs(eigen[p][q])<1e-9)continue;const a=.5*Math.atan2(2*eigen[p][q],eigen[q][q]-eigen[p][p]),c=Math.cos(a),s=Math.sin(a),pp=eigen[p][p],qq=eigen[q][q],pq=eigen[p][q];for(let r=0;r<3;r++)if(r!==p&&r!==q){const rp=eigen[r][p],rq=eigen[r][q];eigen[r][p]=eigen[p][r]=c*rp-s*rq;eigen[r][q]=eigen[q][r]=s*rp+c*rq;}eigen[p][p]=c*c*pp-2*c*s*pq+s*s*qq;eigen[q][q]=s*s*pp+2*c*s*pq+c*c*qq;eigen[p][q]=eigen[q][p]=0;}
const values=eigen.map((r,i)=>r[i]),ratio=Math.min(...values)/Math.max(...values);
assert.ok(ratio>.28,'Density covariance must retain thickness along every principal axis');
assert.equal(field.pixels.length,n**3*4);assert.ok(mass>0);
console.log(JSON.stringify({source:'procedural-xyz-density',grid:n,sigma,central90PercentSpans:spans,sideToFrontArea:sideArea/frontArea,minToMaxCovariance:ratio,generationMs:Math.round(performance.now()-start)},null,2));
