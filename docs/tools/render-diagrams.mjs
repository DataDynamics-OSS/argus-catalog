/** Rebuild the checked-in manual artwork. No network calls are made here. */
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { instance } from '@viz-js/viz';
import sharp from 'sharp';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.join(root, 'modules/ROOT/assets/images/diagrams');
const diagrams = JSON.parse(await readFile(path.join(root, 'diagrams/diagrams.json'), 'utf8'));
const viz = await instance();
const C = { ink:'#101c55', muted:'#53648a', blue:'#087bff', purple:'#7941e9', teal:'#009eaa', red:'#d84b65', line:'#c9d9f1' };
const esc = s => String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const q = s => JSON.stringify(s);
const n = x => Number(x.toFixed(2));
function measure(s, size=20) {
  return [...s].reduce((v,c)=>v+(/[\u1100-\uffff]/u.test(c)?1:/[MW@%]/.test(c)?.9:/[ilIjt.,:;!'| ]/.test(c)?.3:.6)*size,0);
}
function wrap(s, max, size=20) {
  const result=[];
  for (const paragraph of s.split('\n')) {
    let line='';
    for (const ch of paragraph) {
      if (line && measure(line+ch,size)>max) {
        const split=Math.max(line.lastIndexOf(' '),line.lastIndexOf('·'));
        if(split>0){result.push(line.slice(0,split+(line[split]==='·'?1:0)));line=line.slice(split+1)+ch;}
        else {result.push(line);line=ch;}
      } else line+=ch;
    }
    result.push(line.trim());
  }
  return result;
}
function text(x,y,lines,{size=20,color=C.ink,weight=500,anchor='middle',lineHeight=size*1.4}={}) {
  if(!Array.isArray(lines))lines=[lines];
  return `<text x="${n(x)}" y="${n(y)}" fill="${color}" font-size="${size}" font-weight="${weight}" text-anchor="${anchor}">${lines.map((l,i)=>`<tspan x="${n(x)}" dy="${i?lineHeight:0}">${esc(l)}</tspan>`).join('')}</text>`;
}
function rect(x,y,w,h,fill='white',stroke=C.line,r=14,extra='') {
  return `<rect x="${n(x)}" y="${n(y)}" width="${n(w)}" height="${n(h)}" rx="${r}" fill="${fill}" stroke="${stroke}" ${extra}/>`;
}
function iconType(label,shape) {
  if(shape==='database')return 'database';
  if(shape==='decision')return 'decision';
  if(shape==='start'||shape==='end')return shape;
  const s=label.toLowerCase();
  if(/실패|거절|reject|cancel|dismiss|숨김|무시/.test(s))return 'close';
  if(/승인|성공|완료|approved|resolved|production/.test(s))return 'check';
  if(/사용자|소유|담당|권한|역할|keycloak|인증/.test(s))return 'user';
  if(/검색|search|질의|랭킹|점수/.test(s))return 'search';
  if(/db|sql|postgres|pgvector|데이터셋|미러|스키마|컬럼|column|dataset|소스/.test(s))return 'database';
  if(/모델|model|oci|s3|minio|파일|usb|패키/.test(s))return 'cube';
  if(/ai|llm|임베딩|프롬프트|agent|에이전트|학습|few-shot/.test(s))return 'bot';
  if(/용어|단어|사전|표준|term|용어집/.test(s))return 'book';
  if(/리니지|peer|허브|도구|연결|플랫폼/.test(s))return 'network';
  if(/알림|이벤트|채널|webhook|email|open/.test(s))return 'bell';
  if(/ui|화면|메뉴|브라우저|프론트|호스트/.test(s))return 'window';
  if(/api|server|서버|sync|로더|수집|동기|loader|캐시/.test(s))return 'server';
  return 'document';
}
const paths={
 database:'<ellipse cx="16" cy="7" rx="11" ry="4"/><path d="M5 7v18c0 5 22 5 22 0V7M5 16c0 5 22 5 22 0M5 24c0 5 22 5 22 0"/>',
 cube:'<path d="m16 3 12 7v13l-12 7-12-7V10Zm0 14 12-7M16 17 4 10m12 7v13"/>',
 bot:'<rect x="4" y="9" width="24" height="20" rx="5"/><path d="M16 9V4M1 17h3m24 0h3M11 23h10"/><circle cx="16" cy="3" r="1"/><circle cx="11" cy="17" r="1.5"/><circle cx="21" cy="17" r="1.5"/>',
 user:'<circle cx="16" cy="9" r="6"/><path d="M4 29v-3c0-13 24-13 24 0v3Z"/>',
 search:'<circle cx="13" cy="13" r="10"/><path d="m21 21 9 9"/>',
 document:'<path d="M7 2h12l7 7v21H7Zm12 0v8h7M12 16h9m-9 5h9m-9 5h6"/>',
 book:'<path d="M16 6C9 2 3 4 3 4v24s6-2 13 2c7-4 13-2 13-2V4s-6-2-13 2Zm0 0v24"/>',
 network:'<circle cx="16" cy="5" r="4"/><circle cx="6" cy="26" r="4"/><circle cx="26" cy="26" r="4"/><path d="M16 9v7L6 22m10-6 10 6"/>',
 bell:'<path d="M5 24h22l-3-5v-8a8 8 0 0 0-16 0v8ZM12 28c1 4 7 4 8 0M16 3V1"/>',
 window:'<rect x="2" y="4" width="28" height="24" rx="3"/><path d="M2 11h28M7 7h1m4 0h1"/>',
 server:'<rect x="3" y="3" width="26" height="11" rx="3"/><rect x="3" y="19" width="26" height="11" rx="3"/><path d="M8 8h1m4 0h10M8 24h1m4 0h10"/>',
 decision:'<path d="m16 2 14 14-14 14L2 16Z"/><path d="M12 12c0-5 9-5 9 0 0 3-5 3-5 6m0 4v1"/>',
 check:'<circle cx="16" cy="16" r="13"/><path d="m9 16 5 5 10-11"/>',
 close:'<circle cx="16" cy="16" r="13"/><path d="m11 11 10 10m0-10L11 21"/>',
 start:'<circle cx="16" cy="16" r="13"/><path d="m13 10 9 6-9 6Z"/>',
 end:'<circle cx="16" cy="16" r="13"/><rect x="11" y="11" width="10" height="10" rx="1"/>'
};
function icon(type,x,y,color=C.blue,size=32){return `<g transform="translate(${n(x)},${n(y)}) scale(${size/32})" fill="none" stroke="${color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${paths[type]}</g>`;}
function arrow(d,color=C.blue,{dashed=false,both=false,relation=false}={}) {
  const key=Object.keys(C).find(k=>C[k]===color)||'blue';
  return `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round" ${dashed?'stroke-dasharray="7 6"':''} ${relation?'':`marker-end="url(#arrow-${key})"`} ${both?`marker-start="url(#back-${key})"`:''}/>`;
}
function label(x,y,s,color=C.blue,max=240) {
  const lines=wrap(s,max,17),w=Math.max(...lines.map(l=>measure(l,17)))+18,h=lines.length*24+6;
  return rect(x-w/2,y-h/2,w,h,'#ffffff','none',6)+text(x,y-h/2+21,lines,{size:17,color,weight:500,lineHeight:24});
}
function card(x,y,w,h,node,index=0) {
  const type=iconType(node.label,node.shape);
  const color=type==='close'?C.red:type==='check'||type==='end'?C.teal:node.shape==='decision'||index%3===1?C.purple:C.blue;
  const lines=wrap(node.label,w-28,20);
  return rect(x,y+4,w,h,'#dbe6f4','none',16,'opacity=".42"')+
    rect(x,y,w,h,'#ffffff',color+'80',16,'stroke-width="1.4"')+
    rect(x+1,y+1,w-2,5,color,'none',3)+
    `<circle cx="${n(x+w/2)}" cy="${n(y+32)}" r="22" fill="${color}0d"/>`+
    icon(type,x+w/2-15,y+17,color,30)+
    text(x+w/2,y+70,lines,{size:20,weight:600,lineHeight:27});
}
function frame(d,w,h,body,legend) {
  const subtitle={flow:'구성과 흐름',sequence:'요청 · 처리 · 응답',state:'상태와 전이',relationship:'메타데이터 관계'}[d.kind];
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${Math.ceil(w)}" height="${Math.ceil(h)}" viewBox="0 0 ${Math.ceil(w)} ${Math.ceil(h)}" role="img" aria-labelledby="title desc">
<title id="title">${esc(d.title)}</title><desc id="desc">${esc(legend)}</desc>
<defs><linearGradient id="panel" x2="1" y2="1"><stop stop-color="#eff9ff"/><stop offset="1" stop-color="#f4efff"/></linearGradient>
${['blue','purple','teal','red'].map(k=>`<marker id="arrow-${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M1 1 9 5 1 9Z" fill="${C[k]}"/></marker><marker id="back-${k}" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M9 1 1 5 9 9Z" fill="${C[k]}"/></marker>`).join('')}</defs>
<g font-family="Noto Sans CJK KR, Noto Sans KR, sans-serif">
${rect(.5,.5,w-1,h-1,'white','#dce5f2',22)}
${rect(16,16,w-32,h-32,'url(#panel)','none',18)}
${text(38,46,'ARGUS CATALOG  /  '+subtitle,{size:14,color:C.muted,anchor:'start',weight:600})}
${text(38,84,d.title,{size:Math.min(29,(w-76)/measure(d.title,29)*29),anchor:'start',weight:700})}
${body}
${text(38,h-32,legend,{size:14,color:C.muted,anchor:'start'})}
</g></svg>\n`;
}

function nodeDims(node) {
  const longest=Math.max(...node.label.split(/[\s(]/).map(word=>measure(word,20)));
  const width=node.shape==='start'||node.shape==='end'?100:Math.max(210,Math.min(300,longest+30));
  const lines=wrap(node.label,width-28,20);
  return {width,height:Math.max(108,lines.length*27+56)};
}
function dotLayout(d,direction) {
  const stmts=[`digraph G { graph [rankdir=${direction},nodesep=0.38,ranksep=0.72,pad=0.05,splines=spline,newrank=true]; node [label="",shape=box,fixedsize=true]; edge [arrowsize=0.8];`];
  for (const node of d.nodes) {
    const z=nodeDims(node);
    stmts.push(`${q(node.id)} [width=${z.width/72},height=${z.height/72}];`);
  }
  for(const group of d.groups)stmts.push(`subgraph ${q('cluster_'+group.id)} { label=" ";labelloc=t;fontsize=28;margin=22; ${group.nodes.map(q).join(';')}; }`);
  d.edges.forEach((edge,i)=>{
    const attrs=[`id="edge${i}"`];
    if(edge.label) {
      const lines=wrap(edge.label,180,17),w=Math.ceil(Math.max(...lines.map(l=>measure(l,17)))+20),h=lines.length*24+8;
      attrs.push(`label=<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="0"><TR><TD WIDTH="${w}" HEIGHT="${h}" FIXEDSIZE="TRUE"> </TD></TR></TABLE>>`);
    }
    if(edge.bidirectional)attrs.push('dir=both');
    stmts.push(`${q(edge.from)} -> ${q(edge.to)} [${attrs.join(',')}];`);
  });
  stmts.push('}');
  return viz.renderJSON(stmts.join('\n'));
}
function chain(d) {
  if(d.groups.length||d.nodes.length<5||d.edges.length!==d.nodes.length-1||d.edges.some(e=>e.bidirectional))return null;
  const incoming=new Map(),outgoing=new Map();
  for(const e of d.edges){if(outgoing.has(e.from)||incoming.has(e.to))return null;outgoing.set(e.from,e);incoming.set(e.to,e);}
  let node=d.nodes.find(n=>!incoming.has(n.id));const ordered=[];
  while(node&&!ordered.includes(node)){ordered.push(node);node=d.nodes.find(n=>n.id===outgoing.get(node.id)?.to);}
  return ordered.length===d.nodes.length?{ordered,outgoing}:null;
}
function renderChain(d,c) {
  const colW=240,gap=140,left=40,top=135,w=left*2+colW*3+gap*2;
  const cardH=Math.max(...d.nodes.map(node=>wrap(node.label,colW-28,20).length*27+56),108),rowH=cardH+110;
  const rows=Math.ceil(c.ordered.length/3),h=top+rows*rowH+15;
  const positions=c.ordered.map((node,i)=>({node,x:left+(Math.floor(i/3)%2?2-i%3:i%3)*(colW+gap),y:top+Math.floor(i/3)*rowH}));
  let body='';
  for(let i=0;i<positions.length-1;i++) {
    const a=positions[i],b=positions[i+1],e=c.outgoing.get(a.node.id);
    let x1,y1,x2,y2;
    if(a.y===b.y){const dir=b.x>a.x?1:-1;x1=a.x+(dir===1?colW:0);x2=b.x+(dir===1?0:colW);y1=y2=a.y+cardH/2;}
    else{x1=x2=a.x+colW/2;y1=a.y+cardH;y2=b.y;}
    body+=arrow(`M${x1} ${y1} L${x2} ${y2}`,C.blue);
    if(e.label){if(a.y===b.y)body+=label((x1+x2)/2,y1-32,e.label,C.blue,gap-20);else body+=label(x1,(y1+y2)/2,e.label,C.blue,240);}
  }
  positions.forEach((p,i)=>{body+=card(p.x,p.y,colW,cardH,p.node,i);body+=`<circle cx="${p.x+24}" cy="${p.y+28}" r="14" fill="${i%2?C.purple:C.blue}"/>`+text(p.x+24,p.y+34,String(i+1),{size:16,color:'white',weight:700});});
  return frame(d,w,h,body,'화살표 방향으로 진행 · 단계 번호는 작업 순서');
}
function renderGraph(d) {
  const c=chain(d);if(c)return renderChain(d,c);
  let layout=dotLayout(d,d.direction==='TB'||d.direction==='TD'?'TB':'LR');
  let [,,gw,gh]=layout.bb.split(',').map(Number);
  if(gw>1350||gh>1000) {
    const alternative=dotLayout(d,layout.rankdir==='LR'?'TB':'LR');
    const [,,aw,ah]=alternative.bb.split(',').map(Number);
    const score=(w,h)=>Math.max(w/1150,h/850)+Math.max(w/h/3,0.1)*.1;
    if(score(aw,ah)<score(gw,gh)){layout=alternative;gw=aw;gh=ah;}
  }
  const w=Math.max(760,gw+90),h=gh+210,dx=(w-gw)/2,dy=125;
  const point=p=>[n(p[0]+dx),n(gh-p[1]+dy)];
  let body='';
  for(const group of d.groups) {
    const cluster=layout.objects.find(o=>o.name==='cluster_'+group.id);
    const [x1,y1,x2,y2]=cluster.bb.split(',').map(Number);
    body+=rect(dx+x1,dy+gh-y2,x2-x1,y2-y1,group===d.groups[0]?'#e7f4ff':'#f0eaff',C.purple+'55',18,'stroke-dasharray="5 4"');
    body+=text(dx+x1+16,dy+gh-y2+27,group.label,{size:20,color:C.ink,anchor:'start',weight:600});
  }
  for(const edge of layout.edges||[]) {
    const source=d.edges[Number(edge.id.slice(4))];
    const target=d.nodes.find(node=>node.id===source.to);
    const rejected=iconType(target.label,target.shape)==='close'||/실패|Reject|취소|거절/.test(source.label);
    const color=source.dashed?C.purple:rejected?C.red:C.blue;
    for(const draw of edge._draw_||[])if(draw.op==='b') {
      const points=draw.points.map(point);let pathData=`M${points[0].join(' ')}`;
      for(let j=1;j<points.length;j+=3)pathData+=' C'+points.slice(j,j+3).map(p=>p.join(' ')).join(' ');
      // Graphviz reserves arrowhead space; continue the curve to its true endpoint.
      const end=edge.pos.match(/(?:^|\s)e,([\d.]+),([\d.]+)/);
      if(end)pathData+=' L'+point([+end[1],+end[2]]).join(' ');
      if(source.bidirectional) {
        const start=edge.pos.match(/(?:^|\s)s,([\d.]+),([\d.]+)/);
        if(start)pathData=`M${point([+start[1],+start[2]]).join(' ')} L${points[0].join(' ')}`+pathData.slice(pathData.indexOf(' C'));
      }
      body+=arrow(pathData,color,{dashed:source.dashed,both:source.bidirectional,relation:source.relation});
    }
    if(source.label&&edge.lp) {const [x,y]=point(edge.lp.split(',').map(Number));body+=label(x,y,source.label,color,180);}
  }
  d.nodes.forEach((node,i)=>{
    const o=layout.objects.find(o=>o.name===node.id),[x,y]=point(o.pos.split(',').map(Number));
    body+=card(x-Number(o.width)*36,y-Number(o.height)*36,Number(o.width)*72,Number(o.height)*72,node,i);
  });
  let legend=d.kind==='relationship'?'관계 수량: 1 = 하나 · 0..1 = 없거나 하나 · 0..N = 없거나 여러 개':'화살표 = 흐름 또는 상태 전이';
  if(d.edges.some(e=>e.dashed))legend+=d.page.startsWith('install/')?' · 점선 = 선택 구성':d.page==='plugin/index.adoc'?' · 점선 = entry point 연결':' · 점선 = 보조 연결 (라벨 참조)';
  if(d.kind!=='relationship'&&d.edges.some(e=>e.relation))legend+=' · 연결선 = 관계';
  if(d.edges.some(e=>e.bidirectional))legend+=' · 양방향 = 양쪽으로 연결 또는 전이';
  return frame(d,w,h,body,legend);
}

function renderSequence(d) {
  const count=d.participants.length,col=260,left=64,w=left*2+count*col;
  const centers=d.participants.map((p,i)=>left+col*(i+.5));
  const index=new Map(d.participants.map((p,i)=>[p.id,i]));
  const cardTop=130,cardHeight=140,start=cardTop+cardHeight+28;
  let y=start,number=0,rows=[];
  for(const e of d.events) {
    if(e.type==='end'){rows.push({e,y,h:18});y+=18;continue;}
    if(e.type==='alt'||e.type==='else'){rows.push({e,y,h:50});y+=50;continue;}
    if(e.type==='note'){rows.push({e,y,h:68});y+=68;continue;}
    const a=centers[index.get(e.from)],b=centers[index.get(e.to)],self=a===b;
    const lines=wrap(e.label,self?col-34:Math.abs(a-b)-38,18);
    const h=Math.max(86,lines.length*25+45)+(self?24:0);
    rows.push({e,y,h,a,b,lines,self,number:++number});y+=h;
  }
  const h=y+78;
  let body='';
  centers.forEach((x,i)=>{
    body+=rect(x-col/2+8,cardTop-10,col-16,h-cardTop-60,i%2?'#eee8ff80':'#e5f5ff80','none',18);
    body+=`<path d="M${x} ${cardTop+cardHeight}V${h-68}" stroke="${C.line}" stroke-width="2" stroke-dasharray="5 6"/>`;
    body+=card(x-col/2+18,cardTop,col-36,cardHeight,{label:d.participants[i].label},i);
  });
  for(const row of rows) {
    const {e,y,h:rh}=row;
    if(e.type==='alt'||e.type==='else'){
      const color=e.type==='alt'?C.teal:C.red;
      body+=rect(left+4,y,w-left*2-8,38,color+'10',color+'55',8)+text(left+20,y+26,`${e.type==='alt'?'조건 분기':'다른 조건'} · ${e.label}`,{size:18,color,anchor:'start',weight:600});continue;
    }
    if(e.type==='end'){body+=`<path d="M${left+4} ${y+6}H${w-left-4}" stroke="${C.line}" stroke-dasharray="5 4"/>`;continue;}
    if(e.type==='note') {
      const xs=e.over.map(id=>centers[index.get(id)]),mid=(Math.min(...xs)+Math.max(...xs))/2;
      const lw=Math.max(200,Math.min(w-2*left,measure(e.label,17)+32));
      body+=rect(mid-lw/2,y+8,lw,44,'#faf7ff','#c9b9ee',10)+text(mid,y+37,e.label,{size:17,color:C.purple});continue;
    }
    const {a,b,lines,self,number}=row,color=e.response?C.teal:C.blue,ay=y+rh-20;
    body+=`<circle cx="30" cy="${ay-4}" r="13" fill="${color}12"/>`+text(30,ay+1,String(number),{size:13,color,weight:700});
    if(self) {
      body+=arrow(`M${a} ${ay-23} H${a+72} V${ay+5} H${a}`,color,{dashed:e.response});
      body+=text(a,y+23,lines,{size:18,color,lineHeight:25});
    } else {
      const tx=(a+b)/2,tw=Math.max(...lines.map(l=>measure(l,18)))+16;
      body+=rect(tx-tw/2,y+2,tw,lines.length*25+6,'#ffffffee','none',5);
      body+=text(tx,y+23,lines,{size:18,color,lineHeight:25});
      body+=arrow(`M${a} ${ay} H${b}`,color,{dashed:e.response});
    }
  }
  return frame(d,w,h,body,'위에서 아래로 진행 · 파랑 실선 = 요청 / 호출 · 청록 점선 = 응답 · 꺾인 화살표 = 내부 처리');
}

await mkdir(out,{recursive:true});
for(const d of diagrams) {
  const svg=d.kind==='sequence'?renderSequence(d):renderGraph(d);
  await writeFile(path.join(out,d.id+'.svg'),svg);
  // Opaque RGB avoids costly alpha-mask processing in Asciidoctor PDF/Prawn.
  await sharp(Buffer.from(svg),{density:180}).flatten({background:'#ffffff'}).png().toFile(path.join(out,d.id+'.png'));
  console.log(d.id);
}
console.log(`Rendered ${diagrams.length} diagrams (SVG + PNG).`);
