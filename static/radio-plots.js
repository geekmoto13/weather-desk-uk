/* Plots built from NOAA observations. No synthetic samples or gap filling. */
(function(root,factory){const api=factory();if(typeof module==="object"&&module.exports)module.exports=api;else root.WeatherRadioPlots=api;})(typeof globalThis!=="undefined"?globalThis:this,function(){
  "use strict";
  const finite=n=>typeof n==="number"&&Number.isFinite(n);
  const utc=t=>new Intl.DateTimeFormat("en-GB",{timeZone:"UTC",hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).format(new Date(t*1000));
  function points(rows,now,seconds,positive=false){
    const byTime=new Map();
    for(const p of rows||[])if(finite(p.timestamp)&&finite(p.value)&&p.timestamp>=now-seconds&&p.timestamp<=now+120&&(!positive||p.value>0))byTime.set(p.timestamp,p);
    return [...byTime.values()].sort((a,b)=>a.timestamp-b.timestamp);
  }
  function model(kind,value,now=Date.now()/1000){
    if(kind==="kp")return {kind,seconds:86400,min:0,max:9,log:false,units:"Kp",series:[{name:"3h Kp",color:"#91cf50",points:points((value?.points||[]).filter(p=>p.kind==="observed"||p.kind==="estimated").map(p=>({timestamp:p.timestamp,value:p.kp})),now,86400)}]};
    const definitions=kind==="proton" ? [["10","≥10 MeV","#f0b63e"],["50","≥50 MeV","#ab94f3"],["100","≥100 MeV","#70ced7"]] : [["long","0.1–0.8 nm","#f5b836"],["short","0.05–0.4 nm","#aa8cff"]];
    const series=definitions.map(([key,name,color])=>({name,color,points:points(value?.series?.[key],now,21600,true)}));
    const values=series.flatMap(s=>s.points.map(p=>p.value));
    const min=Math.min(kind==="proton" ? -2 : -9, values.length ? Math.floor(Math.log10(Math.min(...values))) : 0);
    const high=values.length?Math.ceil(Math.log10(Math.max(...values))):0;
    return {kind,seconds:21600,min,max:Math.max(kind==="proton"?2:-4,high),log:true,units:kind==="proton"?"Proton flux · pfu (log)":"X-ray flux · W/m² (log)",series};
  }
  function render(container,kind,feed,now=Date.now()/1000){
    const m=model(kind,feed?.value,now);container.replaceChildren();
    const all=m.series.flatMap(s=>s.points);
    if(!all.length){const p=document.createElement("p");p.textContent=feed?.loading?"Loading NOAA observations…":"NOAA observations unavailable. The source link opens the official product.";container.append(p);return "No usable observations · retry follows the source schedule";}
    const ns="http://www.w3.org/2000/svg";
    function element(name,attrs={},text){const e=document.createElementNS(ns,name);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;return e;}
    const svg=element("svg",{viewBox:"0 0 520 300",role:"img","aria-label":`${m.units}; ${feed.stale?"saved, possibly old":"NOAA"} observations; UTC times`});
    svg.append(element("rect",{width:520,height:300,fill:"#0c1d2b"}),element("title",{},`${m.units}. ${feed.stale?"Saved data; refresh unavailable.":"NOAA measurement history."}`));
    const left=58,right=505,top=42,bottom=242;
    const from=now-m.seconds;
    const x=t=>left+(t-from)/m.seconds*(right-left);
    const y=v=>bottom-((m.log?Math.log10(v):v)-m.min)/(m.max-m.min)*(bottom-top);
    const label=(tx,ty,text,attrs={})=>element("text",{x:tx,y:ty,fill:"#b4c8d5","font-family":"system-ui,sans-serif","font-size":12,...attrs},text);
    svg.append(label(left,18,m.units));
    for(let tick=m.min;tick<=m.max;tick+=(kind==="kp"?3:1)){
      const yy=bottom-(tick-m.min)/(m.max-m.min)*(bottom-top);
      svg.append(element("line",{x1:left,x2:right,y1:yy,y2:yy,stroke:"#294154","stroke-width":1}),label(left-8,yy+4,m.log?`10^${tick}`:String(tick),{"text-anchor":"end"}));
    }
    for(let i=0;i<=4;i++){
      const xx=left+(right-left)*i/4,t=from+m.seconds*i/4;
      svg.append(element("line",{x1:xx,x2:xx,y1:top,y2:bottom,stroke:"#21394c","stroke-width":1}),label(xx,bottom+21,utc(t),{"text-anchor":"middle"}));
    }
    if(kind!=="kp"){
      const threshold=kind==="proton"?10:1e-5,yy=y(threshold);
      svg.append(element("line",{x1:left,x2:right,y1:yy,y2:yy,stroke:"#e36e81","stroke-width":1,"stroke-dasharray":"5 4"}),label(right-3,yy-5,kind==="proton"?"S1 threshold · 10 pfu":"M1 threshold",{"text-anchor":"end",fill:"#eda3af","font-size":11}));
    }
    if(kind==="kp"){
      const colors=["#91cf50","#f2e600","#ffbd20","#ff8b24","#fa4646","#bd2442"];
      for(const p of all){
        const level=p.value>=9?5:p.value>=8?4:p.value>=7?3:p.value>=6?2:p.value>=5?1:0;
        const rect=element("rect",{x:x(p.timestamp)+2,y:y(p.value),width:Math.max(1,Math.min(right-x(p.timestamp),(right-left)*10800/m.seconds-4)),height:Math.max(1,bottom-y(p.value)),fill:feed.stale?"#8295a3":colors[level]});
        rect.append(element("title",{},`${utc(p.timestamp)} UTC · Kp ${p.value}`));svg.append(rect);
      }
    }else{
      for(const [index,s]of m.series.entries()){
        let d="",previous=null;
        for(const p of s.points){d+=`${previous===null||p.timestamp-previous>900?"M":"L"}${x(p.timestamp).toFixed(2)},${y(p.value).toFixed(2)} `;previous=p.timestamp;}
        const path=element("path",{d,fill:"none",stroke:feed.stale?"#8295a3":s.color,"stroke-width":2});path.append(element("title",{},s.name));svg.append(path);
        svg.append(label(left+index*145,36,s.name,{fill:feed.stale?"#8295a3":s.color,"font-size":11}));
      }
    }
    const detail=label(left,288,"Move over plot for sample values",{"font-size":11});svg.append(detail);
    const cursor=element("rect",{x:left,y:top,width:right-left,height:bottom-top,fill:"transparent"});
    cursor.addEventListener("pointermove",event=>{
      const box=svg.getBoundingClientRect(),position=(event.clientX-box.left)/box.width*520;
      const target=from+(position-left)/(right-left)*m.seconds;
      const nearest=all.reduce((a,b)=>Math.abs(a.timestamp-target)<Math.abs(b.timestamp-target)?a:b);
      detail.textContent=`${utc(nearest.timestamp)} UTC · ${nearest.value.toPrecision(4)} ${kind==="kp"?"Kp":kind==="proton"?"pfu":"W/m²"}`;
    });
    svg.append(cursor);container.append(svg);
    const last=Math.max(...all.map(p=>p.timestamp));
    return `${feed.stale?"Saved observations · ":"NOAA observations · "}latest sample ${utc(last)} UTC · refresh 5 min`;
  }
  return {model,points,render};
});
