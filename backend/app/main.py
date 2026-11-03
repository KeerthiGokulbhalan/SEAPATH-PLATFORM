from __future__ import annotations

import heapq
import json
import math
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    from global_land_mask import globe
except Exception:
    globe = None

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("SEAPATH_DB", APP_DIR.parent / "seapath.sqlite3"))
CO2_FACTOR = 3.114

PORTS = [
    {"id":"INMUM","name":"Mumbai","state":"Maharashtra","lat":18.9388,"lon":72.8354},
    {"id":"INNSA","name":"Nhava Sheva (JNPA)","state":"Maharashtra","lat":18.9497,"lon":72.9500},
    {"id":"INPAV","name":"Pipavav","state":"Gujarat","lat":20.9050,"lon":71.5000},
    {"id":"INMUN","name":"Mundra","state":"Gujarat","lat":22.7360,"lon":69.7030},
    {"id":"INKAN","name":"Kandla","state":"Gujarat","lat":23.0330,"lon":70.2200},
    {"id":"INMAA","name":"Chennai","state":"Tamil Nadu","lat":13.0827,"lon":80.2707},
    {"id":"INVTZ","name":"Visakhapatnam","state":"Andhra Pradesh","lat":17.6868,"lon":83.2185},
    {"id":"INKRI","name":"Krishnapatnam","state":"Andhra Pradesh","lat":14.2500,"lon":80.1200},
    {"id":"INPDT","name":"Paradip","state":"Odisha","lat":20.2640,"lon":86.6740},
    {"id":"INHAL","name":"Haldia","state":"West Bengal","lat":22.0250,"lon":88.0580},
    {"id":"INCCU","name":"Kolkata","state":"West Bengal","lat":22.5726,"lon":88.3639},
    {"id":"INKOC","name":"Kochi","state":"Kerala","lat":9.9312,"lon":76.2673},
    {"id":"INMNG","name":"Mangalore","state":"Karnataka","lat":12.9141,"lon":74.8560},
    {"id":"INTUT","name":"Tuticorin","state":"Tamil Nadu","lat":8.7642,"lon":78.1348},
    {"id":"INNPT","name":"New Mangalore","state":"Karnataka","lat":12.9500,"lon":74.8000},
    {"id":"INENR","name":"Ennore (Kamarajar)","state":"Tamil Nadu","lat":13.2300,"lon":80.3300},
]

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS vessels(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, vessel_type TEXT NOT NULL,
            max_speed REAL NOT NULL, fuel_rate REAL NOT NULL, dwt REAL NOT NULL,
            draft REAL NOT NULL, created_at TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS voyages(
            id TEXT PRIMARY KEY, vessel_id TEXT NOT NULL, vessel_name TEXT NOT NULL,
            origin_id TEXT NOT NULL, origin_name TEXT NOT NULL, destination_id TEXT NOT NULL,
            destination_name TEXT NOT NULL, strategy TEXT NOT NULL, route_json TEXT NOT NULL,
            distance_nm REAL NOT NULL, duration_hr REAL NOT NULL, fuel_tons REAL NOT NULL,
            co2_tons REAL NOT NULL, risk REAL NOT NULL, progress REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL, elapsed_hr REAL NOT NULL DEFAULT 0,
            actual_distance_nm REAL NOT NULL DEFAULT 0, actual_fuel_tons REAL NOT NULL DEFAULT 0,
            hindrances INTEGER NOT NULL DEFAULT 0, reroutes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        count = con.execute("SELECT COUNT(*) FROM vessels").fetchone()[0]
        if count == 0:
            now = datetime.now(timezone.utc).isoformat()
            seeds = [
                ("vessel-demo-1","MV Ocean Pioneer","Container",21.0,2.8,42000,11.5,now),
                ("vessel-demo-2","MT Blue Horizon","Tanker",16.5,3.4,58000,13.2,now),
                ("vessel-demo-3","MV Coastal Trader","Bulk carrier",14.0,2.1,36000,9.8,now),
            ]
            con.executemany("INSERT INTO vessels VALUES(?,?,?,?,?,?,?,?)", seeds)

app = FastAPI(title="SeaPath API", version="1.0.0", description="Academic maritime routing and voyage simulation API. Environmental and vessel movement values are simulated.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def startup():
    init_db()

class VesselIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    vessel_type: str = "Container"
    max_speed: float = Field(gt=1, le=40)
    fuel_rate: float = Field(gt=0, le=30)
    dwt: float = Field(gt=0)
    draft: float = Field(gt=0, le=30)

class PlanIn(BaseModel):
    vessel_id: str
    origin_id: str
    destination_id: str
    departure_speed: float | None = None

class StartIn(BaseModel):
    vessel_id: str
    origin_id: str
    destination_id: str
    strategy: Literal["fastest","efficient","safest"]

def rowdict(row):
    return dict(row) if row else None

def haversine(a, b):
    lat1, lon1 = map(math.radians, a); lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2-lat1, lon2-lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 3440.065 * 2 * math.asin(math.sqrt(h))

def weather_risk(lat, lon):
    # Stable, coordinate-derived scenario: repeatable across refreshes and API calls.
    wave = 1.1 + 1.7 * (0.5 + 0.5 * math.sin(lat*0.31 + lon*0.17)) + 0.7 * (0.5 + 0.5 * math.cos(lon*0.21-lat*0.13))
    wind = 9.0 + 17.0 * (0.5 + 0.5 * math.sin(lat*0.19-lon*0.11))
    return min(1.0, max(0.0, (wave-1.3)/2.8 * 0.6 + (wind-9)/17 * 0.4)), wave, wind

def nearest_ocean(lat, lon):
    if globe is None:
        raise HTTPException(503, "Ocean routing dependency is unavailable. Install backend/requirements.txt.")
    if bool(globe.is_ocean(lat, lon)):
        return (lat, lon)
    # Harbours are land-side coordinates. Move to the nearest ocean grid point.
    for radius in (0.15,0.3,0.5,0.8,1.2,1.8,2.5,3.5):
        candidates=[]
        for i in range(-12,13):
            for j in range(-12,13):
                la=lat+i*radius/12; lo=lon+j*radius/12
                if bool(globe.is_ocean(la,lo)):
                    candidates.append((haversine((lat,lon),(la,lo)),la,lo))
        if candidates:
            _,la,lo=min(candidates)
            return (la,lo)
    raise HTTPException(422, "Could not locate navigable water near this port.")

def astar(start, goal, strategy):
    if globe is None:
        raise HTTPException(503, "Ocean routing dependency is unavailable. Install backend/requirements.txt.")
    step=0.25
    min_lat=min(start[0],goal[0])-10; max_lat=max(start[0],goal[0])+10
    min_lon=min(start[1],goal[1])-10; max_lon=max(start[1],goal[1])+10
    # Keep the grid bounded for predictable response time.
    rows=int((max_lat-min_lat)/step)+1; cols=int((max_lon-min_lon)/step)+1
    if rows*cols>90000:
        step=0.4; rows=int((max_lat-min_lat)/step)+1; cols=int((max_lon-min_lon)/step)+1
    def cell(point):
        return (max(0,min(rows-1,round((point[0]-min_lat)/step))),max(0,min(cols-1,round((point[1]-min_lon)/step))))
    def coord(node):
        return (min_lat+node[0]*step,min_lon+node[1]*step)
    s=cell(start); g=cell(goal)
    ocean_cache={}
    def ocean(node):
        if node not in ocean_cache:
            la,lo=coord(node)
            ocean_cache[node]=bool(globe.is_ocean(la,lo))
        return ocean_cache[node]
    def snap(node):
        if ocean(node): return node
        candidates=[]
        for radius in range(1,13):
            for dr in range(-radius,radius+1):
                for dc in range(-radius,radius+1):
                    if max(abs(dr),abs(dc))!=radius: continue
                    candidate=(node[0]+dr,node[1]+dc)
                    if 0<=candidate[0]<rows and 0<=candidate[1]<cols and ocean(candidate):
                        candidates.append((haversine(coord(node),coord(candidate)),candidate))
            if candidates: return min(candidates,key=lambda x:x[0])[1]
        raise HTTPException(422,"A route endpoint could not be snapped to open water.")
    s=snap(s); g=snap(g)
    def heuristic(n):
        return haversine(coord(n),coord(g))
    weights={"fastest":0.12,"efficient":0.65,"safest":3.4}
    pq=[(heuristic(s),0.0,s)]
    came={}; costs={s:0.0}; visited=set()
    dirs=[(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]
    while pq:
        _,cost,node=heapq.heappop(pq)
        if node in visited: continue
        if node==g: break
        visited.add(node)
        la,lo=coord(node)
        for dr,dc in dirs:
            nxt=(node[0]+dr,node[1]+dc)
            if not (0<=nxt[0]<rows and 0<=nxt[1]<cols) or nxt in visited or not ocean(nxt): continue
            nla,nlo=coord(nxt); nm=haversine((la,lo),(nla,nlo))
            risk,_,_=weather_risk((la+nla)/2,(lo+nlo)/2)
            # Time/fuel prefer shorter, calmer legs; safety strongly penalises adverse conditions.
            edge=nm*(1+weights[strategy]*risk)
            new=cost+edge
            if new<costs.get(nxt,float("inf")):
                costs[nxt]=new; came[nxt]=node
                heapq.heappush(pq,(new+heuristic(nxt),new,nxt))
    if g not in costs:
        raise HTTPException(422,"No ocean-only route found for this port pair in the current search area.")
    nodes=[g]
    while nodes[-1]!=s: nodes.append(came[nodes[-1]])
    nodes.reverse()
    points=[list(start)]
    points.extend([list(coord(n)) for n in nodes])
    points.append(list(goal))
    # Remove adjacent duplicate coordinates.
    clean=[]
    for p in points:
        if not clean or haversine(clean[-1],p)>0.05: clean.append(p)
    return clean

def route_metrics(points, vessel, speed):
    legs=[haversine(a,b) for a,b in zip(points,points[1:])]
    distance=sum(legs)
    weighted_risk=sum(weather_risk((a[0]+b[0])/2,(a[1]+b[1])/2)[0]*d for a,b,d in zip(points,points[1:],legs))/max(distance,0.001)
    effective_speed=max(3.0,speed*(1-0.12*weighted_risk))
    duration=distance/effective_speed
    fuel=duration*vessel["fuel_rate"]*(1+0.08*weighted_risk)
    return {"distance_nm":round(distance,1),"duration_hr":round(duration,1),"fuel_tons":round(fuel,2),"co2_tons":round(fuel*CO2_FACTOR,2),"risk":round(weighted_risk,3),"weather_exposure_pct":round(sum(d for a,b,d in zip(points,points[1:],legs) if weather_risk((a[0]+b[0])/2,(a[1]+b[1])/2)[0]>.55)/max(distance,0.001)*100,1)}

def get_vessel(vessel_id):
    with connect() as con: v=con.execute("SELECT * FROM vessels WHERE id=?",(vessel_id,)).fetchone()
    if not v: raise HTTPException(404,"Vessel not found")
    return rowdict(v)

def get_port(port_id):
    p=next((p for p in PORTS if p["id"]==port_id),None)
    if not p: raise HTTPException(404,"Port not found")
    return p

@app.get("/api/health")
def health(): return {"status":"ok","data_mode":"SIMULATED","service":"SeaPath API"}

@app.get("/api/ports")
def ports(q: str = ""):
    q=q.casefold().strip()
    return [p for p in PORTS if not q or q in p["name"].casefold() or q in p["state"].casefold() or q in p["id"].casefold()]

@app.get("/api/vessels")
def vessels():
    with connect() as con: return [dict(r) for r in con.execute("SELECT * FROM vessels ORDER BY created_at")]
    
@app.post("/api/vessels",status_code=201)
def create_vessel(payload: VesselIn):
    vessel_id="vsl-"+uuid.uuid4().hex[:10]
    now=datetime.now(timezone.utc).isoformat()
    with connect() as con:
        con.execute("INSERT INTO vessels VALUES(?,?,?,?,?,?,?,?)",(vessel_id,payload.name,payload.vessel_type,payload.max_speed,payload.fuel_rate,payload.dwt,payload.draft,now))
    return get_vessel(vessel_id)

@app.delete("/api/vessels/{vessel_id}",status_code=204)
def delete_vessel(vessel_id: str):
    with connect() as con:
        if not con.execute("SELECT 1 FROM vessels WHERE id=?",(vessel_id,)).fetchone(): raise HTTPException(404,"Vessel not found")
        active=con.execute("SELECT 1 FROM voyages WHERE vessel_id=? AND status='IN_PROGRESS'",(vessel_id,)).fetchone()
        if active: raise HTTPException(409,"Cannot delete a vessel with an active voyage.")
        con.execute("DELETE FROM vessels WHERE id=?",(vessel_id,))
    return None

@app.post("/api/routes/plan")
def plan_routes(payload: PlanIn):
    vessel=get_vessel(payload.vessel_id); origin=get_port(payload.origin_id); dest=get_port(payload.destination_id)
    if origin["id"]==dest["id"]: raise HTTPException(422,"Choose two different ports.")
    speed=min(payload.departure_speed or vessel["max_speed"],vessel["max_speed"])
    start=nearest_ocean(origin["lat"],origin["lon"]); end=nearest_ocean(dest["lat"],dest["lon"])
    options=[]
    for strategy,label in [("fastest","Fastest"),("efficient","Most fuel-efficient"),("safest","Safest")]:
        route=astar(start,end,strategy)
        route[0]=[origin["lat"],origin["lon"]]; route[-1]=[dest["lat"],dest["lon"]]
        metrics=route_metrics(route,vessel,speed)
        options.append({"strategy":strategy,"label":label,"route":route,**metrics,"suitability":round(max(0,100-35*metrics["risk"]-metrics["weather_exposure_pct"]*.25),1)})
    return {"data_mode":"SIMULATED","origin":origin,"destination":dest,"vessel":vessel,"options":options}

@app.post("/api/voyages",status_code=201)
def start_voyage(payload: StartIn):
    vessel=get_vessel(payload.vessel_id); origin=get_port(payload.origin_id); dest=get_port(payload.destination_id)
    if origin["id"]==dest["id"]: raise HTTPException(422,"Choose two different ports.")
    speed=vessel["max_speed"]
    start=nearest_ocean(origin["lat"],origin["lon"]); end=nearest_ocean(dest["lat"],dest["lon"])
    route=astar(start,end,payload.strategy)
    route[0]=[origin["lat"],origin["lon"]]; route[-1]=[dest["lat"],dest["lon"]]
    metrics=route_metrics(route,vessel,speed)
    voyage_id="voy-"+uuid.uuid4().hex[:12]; now=datetime.now(timezone.utc).isoformat()
    with connect() as con:
        con.execute("""INSERT INTO voyages(id,vessel_id,vessel_name,origin_id,origin_name,destination_id,destination_name,strategy,route_json,distance_nm,duration_hr,fuel_tons,co2_tons,risk,progress,status,elapsed_hr,actual_distance_nm,actual_fuel_tons,hindrances,reroutes,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'IN_PROGRESS',0,0,0,0,0,?,?)""",
        (voyage_id,vessel["id"],vessel["name"],origin["id"],origin["name"],dest["id"],dest["name"],payload.strategy,json.dumps(route),metrics["distance_nm"],metrics["duration_hr"],metrics["fuel_tons"],metrics["co2_tons"],metrics["risk"],now,now))
    return voyage(voyage_id)

def voyage(voyage_id):
    with connect() as con: r=con.execute("SELECT * FROM voyages WHERE id=?",(voyage_id,)).fetchone()
    if not r: raise HTTPException(404,"Voyage not found")
    item=dict(r); route=json.loads(item.pop("route_json")); item["route"]=route
    item["position"]=position_on_route(route,item["progress"])
    item["remaining_nm"]=round(item["distance_nm"]*(1-item["progress"]),1)
    effective_speed=max(4,get_vessel(item["vessel_id"])["max_speed"]*(1-.12*item["risk"]))
    item["eta_hr"]=round(item["remaining_nm"]/effective_speed,1)
    item["alerts"]=route_alerts(route,item["progress"])
    return item

def position_on_route(route,progress):
    lengths=[haversine(a,b) for a,b in zip(route,route[1:])]; total=sum(lengths)
    target=max(0,min(1,progress))*total; covered=0
    for a,b,d in zip(route,route[1:],lengths):
        if covered+d>=target:
            t=0 if d==0 else (target-covered)/d
            return [round(a[0]+(b[0]-a[0])*t,5),round(a[1]+(b[1]-a[1])*t,5)]
        covered+=d
    return route[-1]

def route_alerts(route,progress=0):
    alerts=[]
    for i,(a,b) in enumerate(zip(route,route[1:])):
        mid=((a[0]+b[0])/2,(a[1]+b[1])/2); risk,wave,wind=weather_risk(*mid)
        if risk>.62:
            alerts.append({"id":f"wx-{i}","severity":"HIGH" if risk>.78 else "MODERATE","segment":f"{mid[0]:.2f}°N, {mid[1]:.2f}°E","reason":f"Simulated sea state {wave:.1f} m and wind {wind:.0f} km/h","risk":round(risk,2),"approaching":(i/max(1,len(route)-1)>=progress and i/max(1,len(route)-1)-progress<0.12)})
    return alerts

@app.get("/api/voyages")
def list_voyages():
    with connect() as con: ids=[r[0] for r in con.execute("SELECT id FROM voyages ORDER BY created_at DESC")]
    return [voyage(i) for i in ids]

@app.get("/api/voyages/{voyage_id}")
def get_voyage(voyage_id: str): return voyage(voyage_id)

@app.post("/api/voyages/{voyage_id}/tick")
def tick_voyage(voyage_id: str, minutes: float = 10):
    current=voyage(voyage_id)
    if current["status"]!="IN_PROGRESS": return current
    elapsed=minutes/60
    vessel=get_vessel(current["vessel_id"])
    alerts=route_alerts(current["route"],current["progress"])
    # One deterministic automatic diversion is triggered when a severe segment is near.
    if current["reroutes"]==0 and any(a["severity"]=="HIGH" and a["approaching"] for a in alerts):
        old_route=current["route"]
        lengths=[haversine(a,b) for a,b in zip(old_route,old_route[1:])]
        target=current["progress"]*sum(lengths)
        prefix=[old_route[0]]; covered=0.0
        for a,b,d in zip(old_route,old_route[1:],lengths):
            if covered+d<target:
                prefix.append(b); covered+=d
            else:
                t=0.0 if d==0 else (target-covered)/d
                here=[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t]
                if haversine(prefix[-1],here)>.01: prefix.append(here)
                break
        destination=get_port(current["destination_id"])
        safe_start=(current["position"][0],current["position"][1])
        safe_end=nearest_ocean(destination["lat"],destination["lon"])
        safe_route=astar(safe_start,safe_end,"safest")
        safe_route[0]=list(safe_start); safe_route[-1]=[destination["lat"],destination["lon"]]
        remaining=route_metrics(safe_route,vessel,vessel["max_speed"])
        combined=prefix+safe_route[1:]
        total_distance=current["actual_distance_nm"]+remaining["distance_nm"]
        new_progress=min(1.0,current["actual_distance_nm"]/max(total_distance,.001))
        new_fuel_plan=current["actual_fuel_tons"]+remaining["fuel_tons"]
        new_duration=current["elapsed_hr"]+remaining["duration_hr"]
        now=datetime.now(timezone.utc).isoformat()
        with connect() as con:
            con.execute("""UPDATE voyages SET route_json=?,distance_nm=?,duration_hr=?,fuel_tons=?,co2_tons=?,risk=?,progress=?,hindrances=hindrances+1,reroutes=reroutes+1,updated_at=? WHERE id=?""",
            (json.dumps(combined),total_distance,new_duration,new_fuel_plan,new_fuel_plan*CO2_FACTOR,remaining["risk"],new_progress,now,voyage_id))
        return voyage(voyage_id)
    effective_speed=max(4,vessel["max_speed"]*(1-.12*current["risk"]))
    moved=min(current["distance_nm"]*(1-current["progress"]),effective_speed*elapsed)
    actual_elapsed=moved/effective_speed if effective_speed else 0
    new_distance=current["actual_distance_nm"]+moved
    new_progress=min(1,new_distance/current["distance_nm"])
    new_fuel=current["actual_fuel_tons"]+actual_elapsed*vessel["fuel_rate"]
    new_hindrances=current["hindrances"]+(1 if any(a["approaching"] for a in alerts) and current["hindrances"]==0 else 0)
    status="ARRIVED" if new_progress>=1 else "IN_PROGRESS"
    now=datetime.now(timezone.utc).isoformat()
    with connect() as con:
        con.execute("UPDATE voyages SET progress=?,elapsed_hr=elapsed_hr+?,actual_distance_nm=?,actual_fuel_tons=?,hindrances=?,status=?,updated_at=? WHERE id=?",
        (new_progress,actual_elapsed,new_distance,new_fuel,new_hindrances,status,now,voyage_id))
    return voyage(voyage_id)

@app.get("/api/analytics")
def analytics():
    with connect() as con: rows=[dict(r) for r in con.execute("SELECT * FROM voyages")]
    total=len(rows); completed=sum(r["status"]=="ARRIVED" for r in rows); active=sum(r["status"]=="IN_PROGRESS" for r in rows)
    return {"data_mode":"SIMULATED","total_voyages":total,"completed":completed,"active":active,"interrupted":total-completed-active,
      "distance_nm":round(sum(r["actual_distance_nm"] for r in rows),1),"fuel_tons":round(sum(r["actual_fuel_tons"] for r in rows),2),
      "co2_tons":round(sum(r["actual_fuel_tons"]*CO2_FACTOR for r in rows),2),"hindrances":sum(r["hindrances"] for r in rows),
      "reroutes":sum(r["reroutes"] for r in rows),"average_duration_hr":round(sum(r["elapsed_hr"] for r in rows)/max(1,total),1),
      "voyages":[{"id":r["id"],"vessel":r["vessel_name"],"route":f'{r["origin_name"]} → {r["destination_name"]}',"status":r["status"],"progress":round(r["progress"]*100,1),"distance_nm":r["actual_distance_nm"],"fuel_tons":r["actual_fuel_tons"]} for r in rows]}

@app.get("/api/voyages/{voyage_id}/analytics")
def voyage_analytics(voyage_id: str):
    v=voyage(voyage_id)
    return {"data_mode":"SIMULATED","voyage_id":v["id"],"status":v["status"],"planned_distance_nm":v["distance_nm"],"actual_distance_nm":round(v["actual_distance_nm"],1),
      "planned_duration_hr":v["duration_hr"],"actual_duration_hr":round(v["elapsed_hr"],1),"planned_fuel_tons":v["fuel_tons"],"actual_fuel_tons":round(v["actual_fuel_tons"],2),
      "planned_co2_tons":v["co2_tons"],"actual_co2_tons":round(v["actual_fuel_tons"]*CO2_FACTOR,2),"route_deviation_nm":0,
      "hindrances":v["hindrances"],"reroutes":v["reroutes"],"completion_pct":round(v["progress"]*100,1)}
