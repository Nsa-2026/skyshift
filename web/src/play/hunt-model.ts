export type Point={x:number;y:number};
export type Size={width:number;height:number};
export type Camera=Point&{zoom:number};
export type Footprint=Point&{epoch:string};
export function mapScale(camera:Camera,world:Size,screen:Size){return Math.min(screen.width/world.width,screen.height/world.height)*camera.zoom}
export function toScreen(point:Point,camera:Camera,world:Size,screen:Size):Point{const s=mapScale(camera,world,screen);return {x:screen.width/2+(point.x-camera.x)*s,y:screen.height/2+(point.y-camera.y)*s}}
export function toWorld(point:Point,camera:Camera,world:Size,screen:Size):Point{const s=mapScale(camera,world,screen);return {x:camera.x+(point.x-screen.width/2)/s,y:camera.y+(point.y-screen.height/2)/s}}
export function clampCamera(camera:Camera,world:Size,screen:Size):Camera{
  const zoom=Math.max(1,Math.min(12,camera.zoom)),s=mapScale({...camera,zoom},world,screen);
  const limit=(value:number,extent:number,visible:number)=>visible>=extent?extent/2:Math.max(visible/2,Math.min(extent-visible/2,value));
  return {zoom,x:limit(camera.x,world.width,screen.width/s),y:limit(camera.y,world.height,screen.height/s)};
}
export function zoomAt(camera:Camera,zoom:number,anchor:Point,world:Size,screen:Size):Camera{
  const before=toWorld(anchor,camera,world,screen),next={...camera,zoom:Math.max(1,Math.min(12,zoom))},after=toWorld(anchor,next,world,screen);
  return clampCamera({...next,x:next.x+before.x-after.x,y:next.y+before.y-after.y},world,screen);
}
/** A win needs the same known target in every distinct observation, not three clicks on one image. */
export function verifyTrail(marks:Footprint[],expected:Footprint[],radius:number){
  return expected.length>=3&&new Set(expected.map(p=>p.epoch)).size===expected.length&&marks.length===expected.length&&
    expected.every(p=>{const found=marks.filter(m=>m.epoch===p.epoch);return found.length===1&&Math.hypot(found[0].x-p.x,found[0].y-p.y)<=radius});
}
export function sector(point:Point,world:Size){
  const col=Math.max(0,Math.min(3,Math.floor(point.x/world.width*4))),row=Math.max(0,Math.min(3,Math.floor(point.y/world.height*4)));
  return {col,row,name:`${'ABCD'[col]}${row+1}`,x:col*world.width/4,y:row*world.height/4,width:world.width/4,height:world.height/4};
}
/** A broad clue must still cover a trail that crosses a map-grid boundary. */
export function clueRegion(points:Point[],world:Size,level:number){
  const xs=points.map(p=>p.x),ys=points.map(p=>p.y),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  const mid={x:(minX+maxX)/2,y:(minY+maxY)/2},area=sector(mid,world);
  const width=Math.min(world.width,Math.max(world.width/(level<2?3:6),maxX-minX+20)),height=Math.min(world.height,Math.max(world.height/(level<2?3:6),maxY-minY+20));
  const origin=(center:number,min:number,max:number,extent:number,size:number,offset:number)=>Math.max(0,Math.min(extent-size,Math.max(max+8-size,Math.min(min-8,center-size*offset))));
  return {...area,x:origin(mid.x,minX,maxX,world.width,width,.36),y:origin(mid.y,minY,maxY,world.height,height,.43),width,height};
}
