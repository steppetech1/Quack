import type { WorkspaceMock } from "@/lib/mock/types";

/**
 * The demo page of mock mode: one self-contained HTML document for the sandboxed frame (`srcDoc`), made like the pages
 * the backend serves at `/v1/viz/{id}`: no network, scripts only, the canvas fits the device pixels of the frame.
 */
export function mockDemoHtml(labels: WorkspaceMock["demoLabels"]): string {
  const readout = labels.readout("${x.toFixed(2)}", "${sum.toFixed(3)}", "${limit.toFixed(3)}");
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html,body{margin:0;height:100%;background:#1b1a17;color:#dcd6c8;font:14px/1.4 system-ui,sans-serif}
body{display:flex;flex-direction:column}
canvas{flex:1;min-height:0;width:100%;display:block}
label{display:flex;gap:12px;align-items:center;padding:10px 16px;border-top:1px solid #34312b}
input{flex:1;accent-color:#edb966;min-width:0}
output{padding:0 16px 12px;color:#a9a392}
</style></head><body>
<canvas id="c"></canvas>
<label>${labels.sliderLabel}<input id="x" type="range" min="-0.95" max="0.95" step="0.01" value="0.6"></label>
<output id="o"></output>
<script>
const cv=document.getElementById('c'),ctx=cv.getContext('2d'),sl=document.getElementById('x'),out=document.getElementById('o');
function fit(){const r=cv.getBoundingClientRect(),d=window.devicePixelRatio||1;cv.width=Math.round(r.width*d);cv.height=Math.round(r.height*d);draw();}
function draw(){
  const x=parseFloat(sl.value),W=cv.width,H=cv.height,N=21,d=window.devicePixelRatio||1;
  ctx.clearRect(0,0,W,H);
  let sum=0;const part=[];
  for(let n=0;n<N;n++){sum+=Math.pow(x,n);part.push(sum);}
  const limit=1/(1-x);
  const lo=Math.min(0,limit,...part),hi=Math.max(0,limit,...part),pad=24*d;
  const y=v=>H-pad-(v-lo)/(hi-lo||1)*(H-2*pad);
  ctx.strokeStyle='#4a463d';ctx.lineWidth=d;ctx.beginPath();ctx.moveTo(pad,y(0));ctx.lineTo(W-pad,y(0));ctx.stroke();
  const bw=(W-2*pad)/N;
  ctx.fillStyle='#edb966';
  part.forEach((v,i)=>{const t=y(v),z=y(0);ctx.fillRect(pad+i*bw+bw*0.15,Math.min(t,z),bw*0.7,Math.abs(z-t)||d);});
  ctx.strokeStyle='#dcd6c8';ctx.setLineDash([6*d,5*d]);ctx.beginPath();ctx.moveTo(pad,y(limit));ctx.lineTo(W-pad,y(limit));ctx.stroke();ctx.setLineDash([]);
  out.textContent=\`${readout}\`;
}
sl.addEventListener('input',draw);window.addEventListener('resize',fit);fit();
</script></body></html>`;
}
